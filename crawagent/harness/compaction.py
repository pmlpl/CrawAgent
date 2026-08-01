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
    CrawlMessage, HookEvent, Phase,
)
from crawagent.harness.session import CrawlSession
from crawagent.harness.hooks import CrawlHooks


class Compaction:
    """上下文压缩
    
    当对话过长时，压缩早期消息为摘要，保留最近的消息。
    
    参考 pi 的 Compaction：
    - 由 checkpoint 自动触发（token 数超过阈值）
    - 生成 compaction summary entry
    - 旧消息不删除（append-only），但上下文窗口只包含 summary + 最近消息
    """
    
    def __init__(
        self,
        session: CrawlSession,
        hooks: CrawlHooks,
        threshold: int = 80000,  # token 数
        keep_recent: int = 10,  # 保留最近 N 条消息
    ):
        self.session = session
        self.hooks = hooks
        self.threshold = threshold
        self.keep_recent = keep_recent
    
    def estimate_tokens(self, messages: List[CrawlMessage]) -> int:
        """估算消息列表的 token 数
        
        简单估算：中文约 2 字符/token，英文约 4 字符/token。
        取平均 3 字符/token。
        """
        total_chars = sum(len(m.content) for m in messages)
        # 工具调用的参数也计入
        for m in messages:
            if m.tool_calls:
                total_chars += len(json.dumps(m.tool_calls, ensure_ascii=False))
        return total_chars // 3
    
    async def should_compact(self, messages: List[CrawlMessage]) -> bool:
        """判断是否需要压缩"""
        # fire BEFORE_COMPACTION hook
        context = {
            "message_count": len(messages),
            "estimated_tokens": self.estimate_tokens(messages),
            "threshold": self.threshold,
        }
        context = await self.hooks.fire(HookEvent.BEFORE_COMPACTION, context)
        
        # hook 可以覆盖决策
        if "force_compact" in context:
            return context["force_compact"]
        
        return self.estimate_tokens(messages) > self.threshold
    
    async def compact(self, messages: List[CrawlMessage], lane_name: str = "main") -> CrawlMessage:
        """执行压缩
        
        1. 将旧消息（除最近 N 条）发给 LLM 生成摘要
        2. 创建 compaction summary entry
        3. 返回 summary entry
        
        调用方负责将 summary entry 加入上下文窗口，
        并从上下文窗口中移除被压缩的旧消息。
        """
        if len(messages) <= self.keep_recent:
            # 不需要压缩
            return messages[-1] if messages else None
        
        # 分割：旧消息 vs 最近消息
        old_messages = messages[:-self.keep_recent]
        recent_messages = messages[-self.keep_recent:]
        
        # 生成摘要
        summary_content = await self._generate_summary(old_messages)
        
        # 创建 compaction summary entry
        leaf = await self.session.get_leaf_entry(lane_name)
        summary_entry = CrawlMessage(
            id=uuid.uuid4().hex[:24],
            parent_id=leaf.id if leaf else None,
            role="system",
            content=f"[Compaction Summary]\n\n{summary_content}\n\n[End of Summary - {len(old_messages)} messages compressed]",
            metadata={"compaction": True, "compressed_count": len(old_messages)},
        )
        
        await self.session.append_entry(summary_entry)
        
        return summary_entry
    
    async def _generate_summary(self, messages: List[CrawlMessage]) -> str:
        """用 LLM 生成摘要"""
        # 构造摘要请求
        conversation_text = ""
        for m in messages:
            role = m.role
            content = m.content[:500]  # 截断过长消息
            if m.tool_calls:
                content += f"\n[Tool Calls: {json.dumps(m.tool_calls, ensure_ascii=False)[:300]}]"
            conversation_text += f"[{role}]: {content}\n\n"
        
        prompt = (
            "请将以下对话历史压缩为简洁的摘要，保留关键信息：\n\n"
            f"{conversation_text}\n\n"
            "摘要："
        )
        
        try:
            from crawagent.llm.factory import get_llm
            llm = get_llm()
            response = await llm.ainvoke([
                {"role": "system", "content": "你是对话摘要助手，生成简洁准确的摘要。"},
                {"role": "user", "content": prompt}
            ])
            return response.content
        except Exception as e:
            # LLM 失败时，生成简单摘要
            return f"压缩了 {len(messages)} 条消息（LLM 摘要失败: {e}）"
