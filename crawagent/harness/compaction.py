"""上下文压缩模块

当对话过长时，压缩早期消息为摘要，保留最近的消息。

参考 pi Agent Harness 的 Compaction 设计：
- 由 checkpoint 自动触发（token 数超过阈值）
- 生成 compaction summary entry
- 旧消息不删除（append-only），但上下文窗口只包含 summary + 最近消息
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from crawagent.harness.types import (
    CrawlMessage, HookEvent, Phase, OperationRecord,
)
from crawagent.harness.session import CrawlSession
from crawagent.harness.hooks import CrawlHooks


class Compaction:
    """上下文压缩（P6-1 完善版）

    参考 pi Agent Harness：
    - 由 checkpoint 自动触发（token 数超过阈值）
    - 生成 compaction summary entry（append-only，旧消息不删）
    - **上下文重建**：`rebuild_context` 把最新 summary + 最近 N 条消息交给 LLM
    - **压缩计数**：避免连续反复压缩（压缩后窗口应该变小，直到再增长）

    压缩入口：
        1. 自动：`run_checkpoint(messages, lane_name)` — 在 LLM 调用前调用
        2. 手动：`compact(messages, lane_name)` — 直接执行一次
    """

    def __init__(
        self,
        session: CrawlSession,
        hooks: CrawlHooks,
        threshold: int = 80000,  # token 数阈值
        keep_recent: int = 10,  # 保留最近 N 条消息不压缩
        min_gap_between_compactions: int = 3,  # 两次压缩之间至少保留这么多条消息，避免反复压缩
    ):
        self.session = session
        self.hooks = hooks
        self.threshold = threshold
        self.keep_recent = keep_recent
        self.min_gap_between_compactions = min_gap_between_compactions
        # 统计
        self._compactions_run: int = 0
        self._last_compaction_message_count: int = 0

    # ------------------------------------------------------------------
    # Token 估算
    # ------------------------------------------------------------------

    def estimate_tokens(self, messages: List[CrawlMessage]) -> int:
        """估算消息列表的 token 数

        简单估算：中文约 2 字符/token，英文约 4 字符/token。取平均 3 字符/token。
        """
        total_chars = 0
        for m in messages:
            total_chars += len(m.content or "")
            if m.tool_calls:
                total_chars += len(json.dumps(m.tool_calls, ensure_ascii=False))
            if m.metadata:
                try:
                    total_chars += len(json.dumps(m.metadata, ensure_ascii=False)) // 4
                except Exception:
                    pass
        return total_chars // 3

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------

    async def should_compact(self, messages: List[CrawlMessage]) -> bool:
        """判断是否需要压缩（会触发 BEFORE_COMPACTION hook）。"""
        context: Dict[str, Any] = {
            "message_count": len(messages),
            "estimated_tokens": self.estimate_tokens(messages),
            "threshold": self.threshold,
            "last_compaction_count": self._last_compaction_message_count,
        }
        context = await self.hooks.fire(HookEvent.BEFORE_COMPACTION, context)

        if "force_compact" in context:
            return bool(context["force_compact"])
        if "skip_compaction" in context and context["skip_compaction"]:
            return False

        tokens = self.estimate_tokens(messages)
        if tokens <= self.threshold:
            return False

        # 保护：压缩后刚涨回来的消息太少不压缩（避免压缩 → 加 1 条 → 又超过 → 又压缩 …）
        if self._last_compaction_message_count > 0:
            grown = len(messages) - self._last_compaction_message_count
            if grown < self.min_gap_between_compactions:
                return False
        return True

    # ------------------------------------------------------------------
    # 核心：执行压缩
    # ------------------------------------------------------------------

    async def compact(
        self,
        messages: List[CrawlMessage],
        lane_name: str = "main",
        allow_merge_summary: bool = True,
    ) -> Optional[CrawlMessage]:
        """执行压缩，返回新的 summary entry（如果压缩发生）。

        - 旧消息 = 所有消息 **减去** 最近 `keep_recent` 条（可能已经包含 1 个旧 summary）
        - 如果旧消息里已经包含 compaction summary，会合并（allow_merge_summary=True），
          把旧 summary 的内容并入新 summary 的"前置上下文"，避免 LLM 丢信息。
        """
        if len(messages) <= self.keep_recent:
            return None

        # 分割：旧消息 / 最近消息
        recent_messages = messages[-self.keep_recent:]
        old_messages = messages[:-self.keep_recent]

        # 找旧 summary，把它移到最前作为"上下文种子"
        old_summaries = [m for m in old_messages if (m.metadata or {}).get("compaction")]
        if allow_merge_summary and old_summaries:
            last_old_summary = old_summaries[-1]
            others = [m for m in old_messages if m.id != last_old_summary.id]
            # 把 last_old_summary 提到最前，让 LLM 知道"之前已压缩过的摘要"
            ordered_old = [last_old_summary] + others
        else:
            ordered_old = list(old_messages)

        # 生成摘要
        summary_content = await self._generate_summary(ordered_old)

        # 创建 summary entry（parent = lane 最新 leaf）
        leaf = await self.session.get_leaf_entry(lane_name)
        summary_entry = CrawlMessage(
            id=uuid.uuid4().hex[:24],
            parent_id=leaf.id if leaf else None,
            role="system",
            content=(
                "[Compaction Summary]\n\n"
                f"{summary_content}\n\n"
                f"[End of Summary - {len(old_messages)} messages compressed]"
            ),
            metadata={
                "compaction": True,
                "compressed_count": len(old_messages),
                "run_index": self._compactions_run + 1,
                "lane": lane_name,
            },
        )

        await self.session.append_entry(summary_entry)
        # 更新 lane leaf 到新 summary（使后续新建的消息 parent 指向 summary）
        await self.session.update_lane_leaf(lane_name, summary_entry.id)

        # 触发 AFTER_COMPACTION hook
        after_ctx = {
            "lane": lane_name,
            "summary_entry": summary_entry,
            "compressed_count": len(old_messages),
            "old_estimated_tokens": self.estimate_tokens(old_messages),
            "new_estimated_tokens": self.estimate_tokens([summary_entry] + recent_messages),
        }
        await self.hooks.fire(HookEvent.AFTER_COMPACTION, after_ctx)

        # 记录状态
        self._compactions_run += 1
        self._last_compaction_message_count = 1 + len(recent_messages)  # summary + recent
        return summary_entry

    # ------------------------------------------------------------------
    # Checkpoint："超限 → 自动压缩 → 返回重建后的上下文"
    # ------------------------------------------------------------------

    async def run_checkpoint(
        self,
        messages: List[CrawlMessage],
        lane_name: str = "main",
    ) -> List[CrawlMessage]:
        """Checkpoint 入口：若超限则自动压缩，并返回**重建后的上下文**。

        调用方应该用返回值替换传给 LLM 的 messages 列表。
        即使没触发压缩，也会调用 `rebuild_context` 保证格式一致（把旧 summary + recent 取出来）。
        """
        if await self.should_compact(messages):
            await self.compact(messages, lane_name=lane_name)
        # 重建上下文（即使没压缩也统一走 rebuild，避免混入已压缩过的旧消息）
        return await self.rebuild_context(lane_name=lane_name, fallback_messages=messages)

    # ------------------------------------------------------------------
    # 上下文重建（重要！）：从 session 中拉 "最新一个 compaction summary + 后续消息"
    # ------------------------------------------------------------------

    async def rebuild_context(
        self,
        lane_name: str = "main",
        fallback_messages: Optional[List[CrawlMessage]] = None,
        max_recent: Optional[int] = None,
    ) -> List[CrawlMessage]:
        """从持久化 session 重建当前 lane 的上下文窗口。

        思路：
        1. 从 session 里把该 lane 的消息全部拉出来（通过 leaf -> parent 回溯，或直接全量取）
        2. 找到**最后一个** compaction=true 的 entry → 作为起点
        3. 之后的消息（含 recent）全部包含
        4. 如果没有 compaction，就全量 + 最近 N 条截断（保持 recent 上限 = max_recent / keep_recent）
        """
        # 取该 lane 叶子作为锚点：从叶子往前回溯（parent_id 链）不现实，我们直接全量 session entries 再按 lane 关联
        all_entries = await self.session.get_entries(limit=5000, order="asc")
        if not all_entries:
            return []

        # Lane 里的 entries 判定：通过 parent_id 链 + 创建顺序。
        # 简化做法：先按时间升序，找到 lane 的历史（从 create_lane 时的 leaf_id 开始）
        leaf = await self.session.get_leaf_entry(lane_name)
        lane_entry_ids: Set[str] = set()
        if leaf is not None:
            # 从 leaf 回溯 parent 链，拿到全部 chain
            by_id = {e.id: e for e in all_entries}
            cur: Optional[CrawlMessage] = leaf
            while cur is not None:
                lane_entry_ids.add(cur.id)
                cur = by_id.get(cur.parent_id) if cur.parent_id else None
        # lane_chain 按创建时间升序
        lane_chain = sorted(
            [e for e in all_entries if e.id in lane_entry_ids],
            key=lambda x: x.created_at,
        )
        if not lane_chain:
            # 没 lane 信息 → fallback：把所有 entries 当作 lane 上下文
            lane_chain = list(all_entries)

        # 找最后一个 compaction summary
        last_summary_idx: Optional[int] = None
        for idx in range(len(lane_chain) - 1, -1, -1):
            if (lane_chain[idx].metadata or {}).get("compaction"):
                last_summary_idx = idx
                break

        if last_summary_idx is not None:
            # 从该 summary 起，后续所有消息都拿（summary + 后续全部）
            window = lane_chain[last_summary_idx:]
        else:
            window = list(lane_chain)

        # 兜底：即使有 summary，后续条目也可能太多 → 做个 recent 上限
        limit = max_recent or self.keep_recent
        if last_summary_idx is not None:
            # summary 占 1 条位置，再保留 limit 条 recent
            if len(window) > 1 + limit:
                summary = window[0]
                rest = window[-(limit):]
                window = [summary] + rest
        else:
            if len(window) > limit:
                window = window[-limit:]
        return window

    # ------------------------------------------------------------------
    # 摘要生成
    # ------------------------------------------------------------------

    async def _generate_summary(self, messages: List[CrawlMessage]) -> str:
        """用 LLM 生成摘要；失败时退回纯规则摘要。"""
        if not messages:
            return "(空消息集合)"

        # 构造摘要请求（单条消息上限 2000 字符，避免过长 prompt）
        conversation_text_parts: List[str] = []
        total_chars = 0
        for m in messages:
            role = m.role
            content = (m.content or "")[:2000]
            extra = ""
            if m.tool_calls:
                extra = f"\n[Tool Calls: {json.dumps(m.tool_calls, ensure_ascii=False)[:500]}]"
            if (m.metadata or {}).get("compaction"):
                extra = "\n[Previous Compaction Summary - 请把这部分的关键信息并入新摘要]"
            piece = f"[{role}]: {content}{extra}\n"
            conversation_text_parts.append(piece)
            total_chars += len(piece)
            if total_chars > 60_000:
                conversation_text_parts.append(
                    f"\n... (后续 {len(messages) - len(conversation_text_parts)} 条消息截断，仅保留摘要首段)"
                )
                break
        conversation_text = "".join(conversation_text_parts)

        prompt = (
            "请将以下对话历史压缩为简洁的结构化摘要，保留：\n"
            "1. 已完成的关键决定 / 爬取目标\n"
            "2. 尚未完成的任务和 next action\n"
            "3. 关键工具调用结果（尤其是爬取数据、结构化输出）\n"
            "4. 遇到的错误和约束\n\n"
            f"{conversation_text}\n\n"
            "摘要（markdown 格式，控制在 2000 字以内）："
        )

        try:
            from crawagent.llm.factory import get_llm
            llm = get_llm()
            response = await llm.ainvoke([
                {"role": "system", "content": "你是对话摘要助手，输出简洁、准确、结构化的摘要。"},
                {"role": "user", "content": prompt},
            ])
            if hasattr(response, "content"):
                return response.content
            return str(response)
        except Exception as e:
            # LLM 失败：规则兜底摘要
            msgs = messages
            user_msgs = [m for m in msgs if m.role == "user"]
            assistant_msgs = [m for m in msgs if m.role == "assistant"]
            tool_msgs = [m for m in msgs if m.role in ("tool", "function") or (m.metadata or {}).get("tool_result")]
            lines = [
                f"- 压缩条数：{len(msgs)}（user={len(user_msgs)}, assistant={len(assistant_msgs)}, tool={len(tool_msgs)}）",
            ]
            if user_msgs:
                lines.append(f"- 最近用户消息：{user_msgs[-1].content[:300]}")
            if assistant_msgs:
                lines.append(f"- 最近助手回复：{assistant_msgs[-1].content[:300]}")
            lines.append(f"- LLM 摘要失败：{e}")
            return "\n".join(lines)

    # ------------------------------------------------------------------
    # 统计信息（给 tests / 上层 UI）
    # ------------------------------------------------------------------

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "compactions_run": self._compactions_run,
            "last_compaction_message_count": self._last_compaction_message_count,
            "threshold": self.threshold,
            "keep_recent": self.keep_recent,
        }
