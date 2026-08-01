"""pi Agent Harness Session — Python + SQLAlchemy 2.0 async MySQL 实现

参考 pi Agent Harness 的 session 设计，用 Python + SQLAlchemy 2.0 async 实现 MySQL 持久化。

核心设计原则（从 pi 继承）：
1. 对话树 append-only — 条目只增不改不删
2. Lanes — 命名位置（main/monitor/security），每个 lane 有 leaf 指针
3. Operation Logs — 持久化操作记录，崩溃后可恢复
4. Global Facts — session 级 key-value，最新写胜

表结构：
- harness_sessions: Session 元数据
- harness_entries: 对话树条目（append-only）
- harness_lanes: 命名位置指针
- harness_operation_logs: 操作日志
- harness_global_facts: Session 级 key-value
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Double, String, Text, UniqueConstraint, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from crawagent.harness.types import CrawlMessage, LaneInfo, OperationType, OperationRecord, RecoveryPlan, RecoveredOperation
from crawagent.core.database import Base


# ---------------------------------------------------------------------------
# ORM 模型
# ---------------------------------------------------------------------------

class SessionModel(Base):
    """Session 元数据表。"""
    __tablename__ = "harness_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[float] = mapped_column(Double, default=0.0)
    updated_at: Mapped[float] = mapped_column(Double, default=0.0)


class EntryModel(Base):
    """对话树条目表（append-only）。"""
    __tablename__ = "harness_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[str] = mapped_column(Text, default="[]")
    tool_call_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[float] = mapped_column(Double, default=0.0)


class LaneModel(Base):
    """Lane 命名位置表。"""
    __tablename__ = "harness_lanes"
    __table_args__ = (
        UniqueConstraint("session_id", "name", name="uq_lane_session_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    leaf_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    has_open_operation: Mapped[bool] = mapped_column(Boolean, default=False)


class OperationLogModel(Base):
    """操作日志表。"""
    __tablename__ = "harness_operation_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    lane_name: Mapped[str] = mapped_column(String(64))
    op_type: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[float] = mapped_column(Double, default=0.0)
    finished_at: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    entries_json: Mapped[str] = mapped_column(Text, default="[]")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class GlobalFactModel(Base):
    """Global Facts 表（session 级 key-value）。"""
    __tablename__ = "harness_global_facts"
    __table_args__ = (
        UniqueConstraint("session_id", "key", name="uq_fact_session_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[float] = mapped_column(Double, default=0.0)


# ---------------------------------------------------------------------------
# ID 预分配池（P6-2）
# ---------------------------------------------------------------------------

class IdPool:
    """批量预分配 uuid id，降低每次 append_entry/start_operation 都重新生成的散点。

    典型用法：
        pool = IdPool(pool_size=20)
        entry = CrawlMessage(id=pool.next_entry_id(), ...)
        op_id = pool.next_operation_id()
    """

    def __init__(self, pool_size: int = 20) -> None:
        self.pool_size = pool_size
        self._entry_ids: List[str] = []
        self._op_ids: List[str] = []

    def _refill_entry(self) -> None:
        if self._entry_ids:
            return
        for _ in range(self.pool_size):
            self._entry_ids.append(uuid.uuid4().hex[:24])

    def _refill_op(self) -> None:
        if self._op_ids:
            return
        for _ in range(max(4, self.pool_size // 4)):
            self._op_ids.append(uuid.uuid4().hex)

    def next_entry_id(self) -> str:
        self._refill_entry()
        return self._entry_ids.pop(0)

    def next_operation_id(self) -> str:
        self._refill_op()
        return self._op_ids.pop(0)

    def bulk_entry_ids(self, n: int) -> List[str]:
        out: List[str] = []
        for _ in range(n):
            out.append(self.next_entry_id())
        return out


# ---------------------------------------------------------------------------
# 辅助函数：ORM <-> Domain 转换
# ---------------------------------------------------------------------------

def _entry_to_model(entry: CrawlMessage, session_id: str) -> EntryModel:
    """CrawlMessage -> EntryModel"""
    return EntryModel(
        id=entry.id,
        session_id=session_id,
        parent_id=entry.parent_id,
        role=entry.role,
        content=entry.content,
        tool_calls=json.dumps(entry.tool_calls, ensure_ascii=False),
        tool_call_id=entry.tool_call_id,
        metadata_json=json.dumps(entry.metadata, ensure_ascii=False),
        created_at=entry.created_at,
    )


def _model_to_entry(m: EntryModel) -> CrawlMessage:
    """EntryModel -> CrawlMessage"""
    return CrawlMessage(
        id=m.id,
        parent_id=m.parent_id,
        role=m.role,
        content=m.content,
        tool_calls=json.loads(m.tool_calls),
        tool_call_id=m.tool_call_id,
        metadata=json.loads(m.metadata_json),
        created_at=m.created_at,
    )


def _op_model_to_record(m: OperationLogModel) -> OperationRecord:
    """OperationLogModel -> OperationRecord"""
    return OperationRecord(
        id=m.id,
        lane_name=m.lane_name,
        op_type=OperationType(m.op_type),
        started_at=m.started_at,
        finished_at=m.finished_at,
        entries=json.loads(m.entries_json),
        error=m.error,
    )


# ---------------------------------------------------------------------------
# CrawlSession
# ---------------------------------------------------------------------------

class CrawlSession:
    """pi 风格的 Agent Session（MySQL 持久化）。

    提供对话树（append-only）、Lanes、操作日志、Global Facts 四大核心能力。
    """

    def __init__(self, session_id: str, engine: AsyncEngine) -> None:
        self.session_id = session_id
        self._engine = engine

    # --- 对话树操作 ---

    async def append_entry(self, entry: CrawlMessage) -> None:
        """追加条目到对话树（append-only）。"""
        model = _entry_to_model(entry, self.session_id)
        async with AsyncSession(self._engine) as session:
            session.add(model)
            await session.commit()

    async def get_entry(self, entry_id: str) -> Optional[CrawlMessage]:
        """获取单个条目。"""
        async with AsyncSession(self._engine) as session:
            result = await session.get(EntryModel, entry_id)
            if result is None:
                return None
            return _model_to_entry(result)

    async def get_entries(
        self,
        limit: int = 100,
        before_id: Optional[str] = None,
        order: str = "asc",
    ) -> List[CrawlMessage]:
        """获取条目列表（支持分页、向前翻页）。

        默认按 created_at 升序返回（老→新），保证发送给 LLM 时 tool 消息紧跟
        其 assistant tool_calls 消息。如需前端展示"最新在前"，传 order="desc"。
        """
        async with AsyncSession(self._engine) as session:
            desc = order == "desc"
            stmt = (
                select(EntryModel)
                .where(EntryModel.session_id == self.session_id)
                .order_by(EntryModel.created_at.desc() if desc else EntryModel.created_at.asc())
            )
            if before_id is not None:
                ref = await session.get(EntryModel, before_id)
                if ref is not None:
                    stmt = stmt.where(EntryModel.created_at < ref.created_at)
            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [_model_to_entry(r) for r in rows]

    async def get_leaf_entry(self, lane_name: str) -> Optional[CrawlMessage]:
        """获取 lane 的当前叶子条目。"""
        async with AsyncSession(self._engine) as session:
            lane_stmt = (
                select(LaneModel)
                .where(
                    LaneModel.session_id == self.session_id,
                    LaneModel.name == lane_name,
                )
            )
            lane_result = await session.execute(lane_stmt)
            lane = lane_result.scalar_one_or_none()
            if lane is None or lane.leaf_id is None:
                return None
            entry = await session.get(EntryModel, lane.leaf_id)
            if entry is None:
                return None
            return _model_to_entry(entry)

    # --- Lane 操作 ---

    async def create_lane(
        self, name: str, at_entry_id: Optional[str] = None
    ) -> LaneInfo:
        """创建 lane。"""
        model = LaneModel(
            session_id=self.session_id,
            name=name,
            leaf_id=at_entry_id,
            has_open_operation=False,
        )
        async with AsyncSession(self._engine) as session:
            session.add(model)
            await session.commit()
        return LaneInfo(name=name, leaf_id=at_entry_id, has_open_operation=False)

    async def get_lane(self, name: str) -> Optional[LaneInfo]:
        """获取 lane 信息。"""
        async with AsyncSession(self._engine) as session:
            stmt = (
                select(LaneModel)
                .where(
                    LaneModel.session_id == self.session_id,
                    LaneModel.name == name,
                )
            )
            result = await session.execute(stmt)
            lane = result.scalar_one_or_none()
            if lane is None:
                return None
            return LaneInfo(
                name=lane.name,
                leaf_id=lane.leaf_id,
                has_open_operation=lane.has_open_operation,
            )

    async def list_lanes(self) -> List[LaneInfo]:
        """列出所有 lane。"""
        async with AsyncSession(self._engine) as session:
            stmt = select(LaneModel).where(
                LaneModel.session_id == self.session_id
            )
            result = await session.execute(stmt)
            lanes = result.scalars().all()
            return [
                LaneInfo(
                    name=l.name,
                    leaf_id=l.leaf_id,
                    has_open_operation=l.has_open_operation,
                )
                for l in lanes
            ]

    async def update_lane_leaf(self, name: str, leaf_id: str) -> None:
        """更新 lane 的 leaf 指针。"""
        async with AsyncSession(self._engine) as session:
            stmt = (
                update(LaneModel)
                .where(
                    LaneModel.session_id == self.session_id,
                    LaneModel.name == name,
                )
                .values(leaf_id=leaf_id)
            )
            await session.execute(stmt)
            await session.commit()

    async def set_lane_open_operation(self, name: str, is_open: bool) -> None:
        """设置 lane 是否有打开的操作。"""
        async with AsyncSession(self._engine) as session:
            stmt = (
                update(LaneModel)
                .where(
                    LaneModel.session_id == self.session_id,
                    LaneModel.name == name,
                )
                .values(has_open_operation=is_open)
            )
            await session.execute(stmt)
            await session.commit()

    # --- 操作日志 ---

    async def start_operation(
        self, lane_name: str, op_type: OperationType
    ) -> str:
        """开始操作，返回 operation id。"""
        op_id = uuid.uuid4().hex
        now = time.time()
        model = OperationLogModel(
            id=op_id,
            session_id=self.session_id,
            lane_name=lane_name,
            op_type=op_type.value,
            started_at=now,
            finished_at=None,
            entries_json="[]",
            error=None,
        )
        async with AsyncSession(self._engine) as session:
            session.add(model)
            await session.commit()
        # 同步标记 lane 有 open operation
        await self.set_lane_open_operation(lane_name, True)
        return op_id

    async def finish_operation(
        self, op_id: str, error: Optional[str] = None
    ) -> None:
        """完成操作。"""
        now = time.time()
        async with AsyncSession(self._engine) as session:
            stmt = (
                update(OperationLogModel)
                .where(OperationLogModel.id == op_id)
                .values(finished_at=now, error=error)
            )
            await session.execute(stmt)
            await session.commit()
        # 检查该 lane 是否还有未完成操作，若无则清除标记
        async with AsyncSession(self._engine) as session:
            op_stmt = (
                select(OperationLogModel)
                .where(
                    OperationLogModel.session_id == self.session_id,
                    OperationLogModel.finished_at.is_(None),
                )
            )
            op_result = await session.execute(op_stmt)
            remaining = op_result.scalars().first()
            if remaining is None:
                # 没有剩余 open operation，清除所有 lane 标记
                lane_stmt = select(LaneModel).where(
                    LaneModel.session_id == self.session_id,
                    LaneModel.has_open_operation == True,  # noqa: E712
                )
                lane_result = await session.execute(lane_stmt)
                for lane in lane_result.scalars().all():
                    lane.has_open_operation = False
                await session.commit()

    async def get_open_operations(
        self, lane_name: Optional[str] = None
    ) -> List[OperationRecord]:
        """获取未完成的操作（崩溃恢复用）。"""
        async with AsyncSession(self._engine) as session:
            stmt = (
                select(OperationLogModel)
                .where(
                    OperationLogModel.session_id == self.session_id,
                    OperationLogModel.finished_at.is_(None),
                )
                .order_by(OperationLogModel.started_at.asc())
            )
            if lane_name is not None:
                stmt = stmt.where(OperationLogModel.lane_name == lane_name)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [_op_model_to_record(r) for r in rows]

    async def add_operation_entry(self, op_id: str, entry_id: str) -> None:
        """关联 entry 到 operation。"""
        async with AsyncSession(self._engine) as session:
            op = await session.get(OperationLogModel, op_id)
            if op is None:
                return
            entries: List[str] = json.loads(op.entries_json)
            entries.append(entry_id)
            op.entries_json = json.dumps(entries, ensure_ascii=False)
            await session.commit()

    # --- Global Facts ---

    async def get_fact(
        self, key: str, default: Optional[str] = None
    ) -> Optional[str]:
        """获取 global fact。"""
        async with AsyncSession(self._engine) as session:
            stmt = (
                select(GlobalFactModel)
                .where(
                    GlobalFactModel.session_id == self.session_id,
                    GlobalFactModel.key == key,
                )
            )
            result = await session.execute(stmt)
            fact = result.scalar_one_or_none()
            if fact is None:
                return default
            return json.loads(fact.value)

    async def set_fact(self, key: str, value: Any) -> None:
        """设置 global fact（最新写胜）。"""
        now = time.time()
        value_json = json.dumps(value, ensure_ascii=False)
        async with AsyncSession(self._engine) as session:
            stmt = (
                select(GlobalFactModel)
                .where(
                    GlobalFactModel.session_id == self.session_id,
                    GlobalFactModel.key == key,
                )
            )
            result = await session.execute(stmt)
            fact = result.scalar_one_or_none()
            if fact is None:
                fact = GlobalFactModel(
                    session_id=self.session_id,
                    key=key,
                    value=value_json,
                    updated_at=now,
                )
                session.add(fact)
            else:
                fact.value = value_json
                fact.updated_at = now
            await session.commit()

    async def list_facts(self) -> Dict[str, Any]:
        """列出所有 facts。"""
        async with AsyncSession(self._engine) as session:
            stmt = select(GlobalFactModel).where(
                GlobalFactModel.session_id == self.session_id
            )
            result = await session.execute(stmt)
            facts = result.scalars().all()
            return {f.key: json.loads(f.value) for f in facts}

    # --- 批量写入（append_entries） ---

    async def append_entries(self, entries: List[CrawlMessage]) -> None:
        """批量追加条目（减少 commit 次数）。"""
        if not entries:
            return
        models = [_entry_to_model(e, self.session_id) for e in entries]
        async with AsyncSession(self._engine) as session:
            session.add_all(models)
            await session.commit()

    # --- 预分配 ID（可选路径：调用方先拿预分配 ID，再构造 CrawlMessage）---

    def create_id_pool(self, pool_size: int = 20) -> IdPool:
        """创建预分配 ID 池。"""
        return IdPool(pool_size=pool_size)

    # --- Operation 增强：operation_id 可预分配，anchor leaf 记录 ---

    async def start_operation(
        self,
        lane_name: str,
        op_type: OperationType,
        op_id: Optional[str] = None,
    ) -> str:
        """开始操作，返回 operation id。

        - op_id=None 时自动生成
        - op_id 可来自 IdPool.next_operation_id()（预分配，用于崩溃前已知 op_id 场景）
        - 同时在 global fact 里记录 {f"op_anchor_{op_id}": 当前 lane leaf_id}，用于恢复时找到"operation 起点之前"的上下文锚点
        """
        new_op_id = op_id or uuid.uuid4().hex
        now = time.time()
        # 记录锚点：当前 leaf
        leaf = await self.get_leaf_entry(lane_name)
        anchor_leaf_id = leaf.id if leaf else None
        model = OperationLogModel(
            id=new_op_id,
            session_id=self.session_id,
            lane_name=lane_name,
            op_type=op_type.value,
            started_at=now,
            finished_at=None,
            entries_json="[]",
            error=None,
        )
        async with AsyncSession(self._engine) as session:
            session.add(model)
            await session.commit()
        # 同步标记 lane 有 open operation
        await self.set_lane_open_operation(lane_name, True)
        # 锚点存 global fact（简洁）
        await self.set_fact(f"__op_anchor_{new_op_id}", anchor_leaf_id)
        return new_op_id

    async def heartbeat_operation(self, op_id: str, touch_entry: Optional[str] = None) -> None:
        """心跳：把 operation started_at 往前挪一点（避免误认为崩溃）。
        可选择把某个 entry 追加到 operation 的 entries 列表。
        """
        now = time.time()
        async with AsyncSession(self._engine) as session:
            stmt = (
                select(OperationLogModel)
                .where(OperationLogModel.id == op_id)
            )
            result = await session.execute(stmt)
            op = result.scalar_one_or_none()
            if op is None:
                return
            op.started_at = now  # 用 started_at 作为"最后心跳时间"代替（兼容字段）
            if touch_entry:
                entries: List[str] = json.loads(op.entries_json)
                if touch_entry not in entries:
                    entries.append(touch_entry)
                op.entries_json = json.dumps(entries, ensure_ascii=False)
            await session.commit()

    # --- 崩溃恢复（P6-2 核心）---

    async def build_recovery_plan(
        self,
        hooks: Any = None,
        now_threshold_seconds: float = 600.0,  # started_at 距离"现在"超过这个阈值且未完成 → 视为崩溃
    ) -> RecoveryPlan:
        """扫描所有 open operation，生成 RecoveryPlan。

        策略：
        - COMPACTION 未完成：自动标记失败（不会脏写 append-only，安全）
        - NAVIGATION / RUN：需要上层结合 tool replay_safe 判断
        - 超长时间未心跳（started_at + now_threshold 内未 finish）：视为崩溃
        - 对每个 open op，读取 global fact 锚点作为 anchor_leaf_id
        """
        from crawagent.harness.types import RecoveryAction
        open_ops = await self.get_open_operations()

        plan = RecoveryPlan(session_id=self.session_id)

        # lanes that has_open_operation
        for lane in await self.list_lanes():
            if lane.has_open_operation:
                plan.open_lanes.append(lane.name)

        # 孤儿消息：未完成的 assistant tool_calls 消息 / tool 消息 pair 中断
        all_entries = await self.get_entries(limit=5000, order="asc")
        pending_tool_call_ids: Set[str] = set()
        seen_tool_result_ids: Set[str] = set()
        # Step 1: 收集所有 assistant 发起的 tool_call_id
        for e in all_entries:
            if e.role == "assistant" and e.tool_calls:
                for tc in e.tool_calls:
                    tc_id = tc.get("id") if isinstance(tc, dict) else None
                    if tc_id:
                        pending_tool_call_ids.add(tc_id)
        for e in all_entries:
            if e.role in ("tool", "function") and e.tool_call_id:
                seen_tool_result_ids.add(e.tool_call_id)
        # 孤儿 = 有 tool_call 发起但没 tool 结果消息 → assistant 消息标记为 orphan（恢复时跳过/回滚）
        orphan_call_ids = pending_tool_call_ids - seen_tool_result_ids
        orphan_entries = []
        for e in all_entries:
            if e.role == "assistant" and e.tool_calls:
                call_ids = [tc.get("id") for tc in e.tool_calls if isinstance(tc, dict)]
                if any(cid in orphan_call_ids for cid in call_ids):
                    orphan_entries.append(e.id)
        plan.orphan_entry_ids = orphan_entries

        for op_rec in open_ops:
            anchor_raw = await self.get_fact(f"__op_anchor_{op_rec.id}", default=None)
            anchor_leaf_id = anchor_raw if isinstance(anchor_raw, (str, type(None))) else None
            # 判断动作
            dur = time.time() - op_rec.started_at
            action = RecoveryAction.MARK_FAILED
            reason = f"未完成操作 {op_rec.op_type.value}, duration={dur:.0f}s"
            if op_rec.op_type == OperationType.COMPACTION:
                # 压缩失败不会破坏 append-only，可直接放弃
                action = RecoveryAction.MARK_FAILED
                reason = "压缩操作未完成（压缩逻辑幂等，标记失败，由下次 checkpoint 自动重试）"
            elif op_rec.op_type == OperationType.NAVIGATION:
                # 导航操作：如果是纯读抓取（HTTP GET），属于幂等，默认 replay_safe
                action = RecoveryAction.REPLAY_SAFE
                reason = "导航 / 抓取操作未完成（HTTP GET 默认幂等，建议 REPLAY_SAFE 重放）"
            elif op_rec.op_type == OperationType.RUN:
                # RUN 是更高级别的 Agent 运行，可能含写操作，默认 RETRY，上层再判断
                action = RecoveryAction.RETRY
                reason = "RUN 操作未完成（上层 CrawlLoop 再根据工具是否 replay_safe 决定是否重放）"
            plan.operations.append(
                RecoveredOperation(
                    record=op_rec,
                    action=action,
                    reason=reason,
                    anchor_leaf_id=anchor_leaf_id,
                    lane_name=op_rec.lane_name,
                )
            )

        # fire hooks（如传入）
        if hooks is not None:
            try:
                from crawagent.harness.types import HookEvent
                ctx = {"plan": plan, "open_ops": [r.model_dump() if hasattr(r, "model_dump") else r for r in open_ops]}
                if hasattr(hooks, "fire"):
                    await hooks.fire(HookEvent.BEFORE_CRASH_RECOVERY, ctx)
            except Exception:
                pass

        return plan

    async def apply_recovery_plan(self, plan: RecoveryPlan) -> None:
        """执行恢复计划：把 MARK_FAILED / 执行了 REPLAY_SAFE / RETRY 后的 open ops 统一收尾。

        本方法的语义：
        - 对 MARK_FAILED：finish_operation(error=...) 标记失败
        - 对 REPLAY_SAFE / RETRY：**调用方已负责重放或重试成功后**，再调用本方法收尾
        - 最终会把所有 plan.operations 对应的 op 都设为已结束
        """
        for ro in plan.operations:
            rec = ro.record
            if rec.finished_at is not None:
                continue
            error = ro.reason or f"recovery_action={ro.action.value}"
            await self.finish_operation(rec.id, error=error)
        # fire AFTER_CRASH_RECOVERY hook
        # (hooks 不作为参数传进来，避免破坏签名稳定；上层可自行 fire)
        return None

    async def lane_chain_from_anchor(
        self,
        lane_name: str,
        anchor_leaf_id: Optional[str] = None,
    ) -> List[CrawlMessage]:
        """从锚点 leaf 起重建 lane chain（锚点之后的消息）。

        用于崩溃恢复：anchor_leaf_id = operation 开始前的 leaf；
        返回 anchor 之后（不含 anchor）到当前 leaf 的所有 entries，按时间升序。
        """
        all_entries = await self.get_entries(limit=5000, order="asc")
        if not all_entries:
            return []
        by_id = {e.id: e for e in all_entries}

        leaf = await self.get_leaf_entry(lane_name)
        if leaf is None:
            return []
        chain_desc: List[CrawlMessage] = []
        cur: Optional[CrawlMessage] = leaf
        while cur is not None:
            if cur.id == anchor_leaf_id:
                break
            chain_desc.append(cur)
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
        chain_desc.reverse()  # 老 → 新
        return chain_desc


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """Session 工厂和生命周期管理。"""

    def __init__(self, dsn: str) -> None:
        """dsn: mysql+aiomysql://user:pass@host:port/db"""
        self._engine = create_async_engine(dsn, pool_size=10, echo=False)

    async def initialize(self) -> None:
        """创建所有表。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create_session(self, name: str = "") -> CrawlSession:
        """创建新 session。"""
        session_id = uuid.uuid4().hex
        now = time.time()
        async with AsyncSession(self._engine) as session:
            model = SessionModel(
                id=session_id,
                name=name,
                created_at=now,
                updated_at=now,
            )
            session.add(model)
            await session.commit()
        return CrawlSession(session_id, self._engine)

    async def get_session(self, session_id: str) -> Optional[CrawlSession]:
        """获取已有 session。"""
        async with AsyncSession(self._engine) as session:
            result = await session.get(SessionModel, session_id)
            if result is None:
                return None
        return CrawlSession(session_id, self._engine)

    async def list_sessions(self) -> List[Dict]:
        """列出所有 sessions。"""
        async with AsyncSession(self._engine) as session:
            stmt = select(SessionModel).order_by(SessionModel.created_at.desc())
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ]

    async def delete_session(self, session_id: str) -> bool:
        """删除 session 及其所有关联数据（entries / lanes / operation_logs / global_facts）。

        Returns: True if deleted, False if not found.
        """
        from sqlalchemy import delete as _sa_delete
        async with AsyncSession(self._engine) as session:
            # 检查是否存在
            existing = await session.get(SessionModel, session_id)
            if existing is None:
                return False
            # 按外键依赖顺序删除（entries 引用 lanes；operation_logs 引用 sessions）
            await session.execute(_sa_delete(EntryModel).where(EntryModel.session_id == session_id))
            await session.execute(_sa_delete(OperationLogModel).where(OperationLogModel.session_id == session_id))
            await session.execute(_sa_delete(LaneModel).where(LaneModel.session_id == session_id))
            await session.execute(_sa_delete(GlobalFactModel).where(GlobalFactModel.session_id == session_id))
            await session.execute(_sa_delete(SessionModel).where(SessionModel.id == session_id))
            await session.commit()
            return True

    async def close(self) -> None:
        """关闭引擎。"""
        await self._engine.dispose()
