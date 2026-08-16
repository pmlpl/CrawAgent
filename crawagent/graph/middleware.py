"""历史消息裁剪中间件 — 治理 Token 膨胀，不污染 checkpointer 持久化状态。

设计动机：
- LangGraph checkpointer 持久化完整 state（append-only），保证 DeepSeek 缓存前缀稳定
- 但传给 LLM 的 messages 不能无限膨胀，否则几轮就 API 超时
- 解决：用 wrap_model_call 拦截 model 调用，在调用前裁剪 request.messages
- 关键：只改传给 LLM 的输入，不改 state → checkpointer 持久化的仍是完整历史

裁剪策略（两层）：
1. 滑动窗口：保留最近 N 轮完整对话（HumanMessage + AIMessage + 中间 ToolMessage）
2. 工具结果裁剪：超长的 ToolMessage 内容截断为摘要，保留首尾

注意：ToolMessage 必须与其配对的 AIMessage.tool_calls 一起保留，
否则 OpenAI API 会报 tool_call_id 不匹配错误。
"""
from __future__ import annotations

from collections.abc import Callable
from langchain_core.messages import (
    AnyMessage,
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
)
from langchain.agents.middleware import AgentMiddleware, ModelRequest


def _approx_token_count(text: str) -> int:
    """粗略 token 估算：中文≈1.5 字/token，英文≈4 字符/token。混合取折中。"""
    if not text:
        return 0
    # 中文字符数
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    non_cjk = len(text) - cjk
    return int(cjk * 1.5 + non_cjk / 4)


def _msg_token_count(msg: BaseMessage) -> int:
    """单条消息的 token 估算（含 content + tool_calls 参数）"""
    total = _approx_token_count(msg.content if isinstance(msg.content, str) else str(msg.content))
    if isinstance(msg, AIMessage) and msg.tool_calls:
        for tc in msg.tool_calls:
            total += _approx_token_count(str(tc.get("args", {})))
    return total


def _truncate_tool_message(msg: ToolMessage, max_chars: int = 500) -> ToolMessage:
    """裁剪 ToolMessage 内容：保留首尾，中间用省略标记替代。"""
    content = msg.content
    if not isinstance(content, str):
        content = str(content)
    if len(content) <= max_chars:
        return msg
    head = max_chars // 2
    tail = max_chars // 4
    truncated = content[:head] + f"\n... [truncated {len(content) - head - tail} chars] ...\n" + content[-tail:]
    # 用同样的 metadata 创建新消息，不改原对象
    return ToolMessage(
        content=truncated,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
        id=msg.id,
    )


class TrimHistoryMiddleware(AgentMiddleware):
    """滑动窗口 + 工具结果裁剪中间件。

    在 wrap_model_call 里裁剪传给 LLM 的 messages：
    1. 先裁剪所有超长 ToolMessage 内容（保留首尾 500 字符）
    2. 如果总 token 仍超 max_tokens，按"轮"滑动窗口保留最近 N 轮
    3. 始终保留最后一条 HumanMessage（本轮用户输入）

    不修改 checkpointer 持久化的 state，只改 LLM 输入。
    """

    def __init__(
        self,
        max_tokens: int = 8000,
        keep_recent_turns: int = 3,
        tool_result_max_chars: int = 500,
    ):
        """Args:
            max_tokens: 传给 LLM 的历史 messages token 上限（不含 system prompt）
            keep_recent_turns: 滑动窗口保留的最近完整轮数（1 轮 = Human + AI + 中间 Tool）
            tool_result_max_chars: 单条 ToolMessage 内容裁剪阈值
        """
        self.max_tokens = max_tokens
        self.keep_recent_turns = keep_recent_turns
        self.tool_result_max_chars = tool_result_max_chars

    def wrap_model_call(self, request, handler):
        """裁剪 request.messages 后再调 handler。不修改 request.state。"""
        original_messages = request.messages
        if not original_messages:
            return handler(request)

        # 第 1 步：裁剪超长 ToolMessage 内容
        trimmed = []
        for msg in original_messages:
            if isinstance(msg, ToolMessage):
                trimmed.append(_truncate_tool_message(msg, self.tool_result_max_chars))
            else:
                trimmed.append(msg)

        # 第 2 步：计算总 token，超限则滑动窗口
        total_tokens = sum(_msg_token_count(m) for m in trimmed)
        if total_tokens <= self.max_tokens:
            request.messages = trimmed
            return handler(request)

        # 滑动窗口：从后往前找 keep_recent_turns 轮的边界
        # 一轮 = 一个 HumanMessage（含其后所有 AI/Tool 消息直到下个 HumanMessage）
        turn_boundaries: list[int] = []  # 每个 HumanMessage 的 index
        for i, msg in enumerate(trimmed):
            if isinstance(msg, HumanMessage):
                turn_boundaries.append(i)

        if len(turn_boundaries) <= self.keep_recent_turns:
            # 轮数本身不多，是单轮内工具结果太长，已裁剪过，直接用
            request.messages = trimmed
            return handler(request)

        # 保留最后 keep_recent_turns 轮
        start_idx = turn_boundaries[-self.keep_recent_turns]
        windowed = trimmed[start_idx:]

        # 再检查一次 token，如果仍超限，激进裁剪：只留最后一轮
        total_after_window = sum(_msg_token_count(m) for m in windowed)
        if total_after_window > self.max_tokens and len(turn_boundaries) > 1:
            start_idx = turn_boundaries[-1]
            windowed = trimmed[start_idx:]

        request.messages = windowed
        return handler(request)
