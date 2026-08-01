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

from crawagent.harness.types import CrawlMessage, LaneInfo, OperationType, OperationRecord
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
