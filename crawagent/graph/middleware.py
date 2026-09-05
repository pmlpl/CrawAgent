"""历史消息裁剪中间件 — 治理 Token 膨胀，不污染 checkpointer 持久化状态。

设计动机：
- LangGraph checkpointer 持久化完整 state（append-only），保证 DeepSeek 缓存前缀稳定
- 但传给 LLM 的 messages 不能无限膨胀，否则几轮就 API 超时
- 解决：用 wrap_model_call 拦截 model 调用，在调用前裁剪 request.messages
- 关键：只改传给 LLM 的输入，不改 state → checkpointer 持久化的仍是完整历史

裁剪策略（两层）：
1. 滑动窗口：保留最近 N 轮完整对话（HumanMessage + AIMessage + 中间 ToolMessage）
2. 工具结果裁剪：超长的 ToolMessage 内容截断为摘要，保留首尾

智能压缩（P2）：
水位淘汰时，把被淘汰的旧消息用轻量 LLM 调用做摘要，
摘要结果作为 SystemMessage 插入窗口起点。失败时 fallback 到暴力截断。

注意：ToolMessage 必须与其配对的 AIMessage.tool_calls 一起保留，
否则 OpenAI API 会报 tool_call_id 不匹配错误。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from threading import Lock

from langchain_core.messages import (
    AnyMessage,
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from langchain.agents.middleware import AgentMiddleware, ModelRequest


# ── 模块级：每次 LLM 调用后的 token 计数（供前端余量环读取） ──────
# key = thread_id (session_id), value = {"used": int, "limit": int}
_last_context_tokens: dict[str, dict] = {}
_ctx_lock = Lock()


def get_context_info(thread_id: str) -> dict | None:
    """给 API 层读取：某个 session 最近一次 LLM 调用的上下文用量。"""
    with _ctx_lock:
        return _last_context_tokens.get(thread_id)


def _store_context(thread_id: str, used: int, limit: int) -> None:
    with _ctx_lock:
        _last_context_tokens[thread_id] = {"used": used, "limit": limit}


def reconstruct_context(
    thread_id: str, agent, model_name: str | None = None
) -> dict | None:
    """从检查点持久化的消息重建上下文用量（server 重启 / 内存缓存空时兜底）。

    口径与 wrap_model_call 里 _store_context 一致：先裁剪超长 ToolMessage
    内容，再求和（淘汰前的总量）。limit 按 context_window × 70% 动态算，
    和 middleware _resolve_max_tokens 同一公式。顺带统计轮数（HumanMessage 数）。

    Returns: {"used": int, "limit": int, "turn_count": int} 或 None（无检查点 / 读取失败）。
    """
    try:
        config = {"configurable": {"thread_id": thread_id}}
        state = agent.get_state(config)
        messages = state.values.get("messages", [])
    except Exception:
        return None
    if not messages:
        return None
    # 裁剪超长 ToolMessage（和 wrap_model_call 第 1 步一致）
    trimmed = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            trimmed.append(_truncate_tool_message(msg, 2000))
        else:
            trimmed.append(msg)
    used = sum(_msg_token_count(m) for m in trimmed)
    turn_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    # limit：和 _resolve_max_tokens 同一公式
    try:
        from crawagent.llm.model import get_context_window

        limit = int(get_context_window(model_name) * 0.70)
    except Exception:
        limit = 32000
    return {"used": used, "limit": limit, "turn_count": turn_count}


def _extract_thread_id(request) -> str | None:
    """从 LangGraph middleware 的 ModelRequest 里尝试拿到 thread_id。

    主路径是 ContextVar（server.py 调 agent 前设好），这里只是兜底扫描。
    """
    cfg = getattr(request, "config", None)
    state = getattr(request, "state", None)

    # 方式 1: request.config（dict 或 ConfigDict）
    if cfg:
        try:
            conf = (
                cfg.get("configurable", {})
                if isinstance(cfg, dict)
                else getattr(cfg, "configurable", {})
            )
            tid = (
                conf.get("thread_id")
                if isinstance(conf, dict)
                else getattr(conf, "thread_id", None)
            )
            if tid:
                return str(tid)
        except Exception:
            pass

    # 方式 2: request.state 里有 __runnable_config
    if isinstance(state, dict):
        rc = state.get("__runnable_config", {})
        conf = rc.get("configurable", {}) if isinstance(rc, dict) else {}
        tid = conf.get("thread_id") if isinstance(conf, dict) else None
        if tid:
            return str(tid)

    return None


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
    total = _approx_token_count(
        msg.content if isinstance(msg.content, str) else str(msg.content)
    )
    if isinstance(msg, AIMessage) and msg.tool_calls:
        for tc in msg.tool_calls:
            total += _approx_token_count(str(tc.get("args", {})))
    return total


def _truncate_tool_message(msg: ToolMessage, max_chars: int = 500) -> ToolMessage:
    """裁剪 ToolMessage 内容：保留头部 max//2 + 尾部 max//4 字符，中间用省略标记替代。"""
    content = msg.content
    if not isinstance(content, str):
        content = str(content)
    if len(content) <= max_chars:
        return msg
    head = max_chars // 2
    tail = max_chars // 4
    truncated = (
        content[:head]
        + f"\n... [truncated {len(content) - head - tail} chars] ...\n"
        + content[-tail:]
    )
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
    1. 先裁剪所有超长 ToolMessage 内容（保留首尾）
    2. 如果总 token 仍超 max_tokens，按"轮"滑动窗口保留最近 N 轮
    3. 始终保留最后一条 HumanMessage（本轮用户输入）

    Context window 动态适配：
    - 每次 wrap_model_call 从当前 LLM 的 model_name 查 context window 表
    - 裁剪水位 = context_window × 70%（留 30% 给 LLM 输出）
    - 不同模型上下文差巨大（32K → 1M），硬编码 32K 会导致 GLM 1M 被浪费

    不修改 checkpointer 持久化的 state，只改 LLM 输入。
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        keep_recent_turns: int = 3,
        tool_result_max_chars: int = 2000,
    ):
        """Args:
        max_tokens: 手动覆盖裁剪水位（token）。None 时按模型 context_window × 70% 动态算。
        keep_recent_turns: 滑动窗口保留的最近完整轮数（1 轮 = Human + AI + 中间 Tool）
        tool_result_max_chars: 单条 ToolMessage 内容裁剪阈值
        """
        self._fixed_max_tokens = max_tokens  # None = 动态
        self.keep_recent_turns = keep_recent_turns
        self.tool_result_max_chars = tool_result_max_chars
        # 运行时最新裁剪水位（供 get_context_info 读取）
        self.max_tokens = max_tokens or 32000

    def _resolve_max_tokens(self, llm_model_name: str | None) -> int:
        """按当前 LLM 模型名动态算裁剪水位（context_window × 70%）。"""
        if self._fixed_max_tokens:
            return self._fixed_max_tokens
        try:
            from crawagent.llm.model import get_context_window

            # request.model_name 在 LangGraph 某些版本下为 None → 用配置里的模型名兜底
            if not llm_model_name:
                from crawagent.llm.registry import resolve_model

                llm_model_name = resolve_model()[0]
            cw = get_context_window(llm_model_name)
            # 水位 = 窗口 70%，留 30% 给 LLM 输出
            return int(cw * 0.70)
        except Exception:
            return 32000  # 兜底

    @staticmethod
    def _scan_thread_id(request) -> str | None:
        """读 ContextVar 拿 thread_id（最可靠的方式）。

        server.py 在调 agent.stream 前用 ctx_session_id.set(session_id) 设好，
        middleware 直接读。比暴力扫描 state 更快更准。
        """
        # 优先读 ContextVar（由 server.py 在调用前设置）
        try:
            from crawagent.graph.agent import ctx_session_id

            sid = ctx_session_id.get(None)
            if sid:
                return sid
        except (ImportError, LookupError):
            pass

        # 兜底：暴力扫 state（万一有人不用 server.py 直接调 agent）
        state = getattr(request, "state", None)
        if not isinstance(state, dict):
            return None

        def _walk(obj, depth=0):
            if depth > 8:
                return None
            if isinstance(obj, dict):
                tid = obj.get("thread_id") or obj.get("threadId")
                if tid and isinstance(tid, str):
                    return tid
                for v in obj.values():
                    r = _walk(v, depth + 1)
                    if r:
                        return r
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    r = _walk(item, depth + 1)
                    if r:
                        return r
            return None

        return _walk(state)

    def wrap_model_call(self, request, handler):
        """裁剪 request.messages 后再调 handler。不修改 request.state。"""
        print(f"[MIDDLEWARE] wrap_model_call ENTER", flush=True)

        original_messages = request.messages
        if not original_messages:
            return handler(request)

        # ── 1. 暴力扫描 state 找 thread_id ──
        # LangGraph 把 configurable.thread_id 藏在 state 的某个嵌套位置里，
        # Runtime 对象没有 get_config()。递归搜 state 字典。
        thread_id = self._scan_thread_id(request)
        print(f"[MIDDLEWARE] ✅ thread_id = {thread_id!r}", flush=True)

        # ── 2. 动态刷新 max_tokens（按当前模型 context_window × 70%）──
        llm_model_name = getattr(request, "model_name", None)
        if not llm_model_name:
            llm_model_name = getattr(request, "model_id", None)
        if not llm_model_name:
            # 与 _resolve_max_tokens 同一兜底口径：request 拿不到模型名时用注册表
            # 第一个可用模型。这里先补上再打印，避免日志出现 "model=None 但数值有值"
            # 的误导（此前 max_tokens 按兜底模型算、名字却打印原始 None）。
            from crawagent.llm.registry import resolve_model

            llm_model_name = resolve_model()[0]
        self.max_tokens = self._resolve_max_tokens(llm_model_name)
        print(
            f"[MIDDLEWARE] max_tokens = {self.max_tokens:,} (model={llm_model_name})",
            flush=True,
        )

        # 第 3 步：裁剪超长 ToolMessage 内容
        # （确定性截断：同一条消息每次裁剪结果相同，不会破坏缓存前缀）
        trimmed = []
        for msg in original_messages:
            if isinstance(msg, ToolMessage):
                trimmed.append(_truncate_tool_message(msg, self.tool_result_max_chars))
            else:
                trimmed.append(msg)

        # 第 2 步：计算总 token，未超限直接用（消息只追加 → 前缀稳定 → 缓存命中）
        total_tokens = sum(_msg_token_count(m) for m in trimmed)
        _store_context(thread_id or "unknown", total_tokens, self.max_tokens)

        if total_tokens <= self.max_tokens:
            # 预算内不淘汰，但超长 ToolMessage 仍需就地裁剪并保留。
            # 用 request.messages 而非 request.override()：与被淘汰分支一致，
            # 让裁剪结果对下游（含测试的 SimpleNamespace 桩）可见。
            request.messages = trimmed
            return handler(request)

        # 第 3 步：水位淘汰（DeepSeek 前缀缓存友好）。
        # 旧策略"固定保留最近 N 轮"每轮都移动窗口起点 → 历史前缀永远无法命中缓存
        # （实测命中率仅 37.5%，只有 system prompt + 工具定义能命中）。
        # 新策略：超限时一次性淘汰到半水位（max/2），此后消息只追加不修改，
        # 前缀稳定，缓存命中率随对话持续上升；再增长 max/2 才会再次淘汰（摊销）。
        # keep_recent_turns 作为下限：至少保留最近 N 轮，保证当前任务上下文完整。
        turn_boundaries = [
            i for i, m in enumerate(trimmed) if isinstance(m, HumanMessage)
        ]
        if not turn_boundaries:
            request.messages = trimmed
            return handler(request)

        target = self.max_tokens // 2
        # 起点（淘汰边界）不得晚于倒数第 keep_recent_turns 轮 —— 保证至少保留 N 轮上下文
        limit = (
            turn_boundaries[-self.keep_recent_turns]
            if len(turn_boundaries) >= self.keep_recent_turns
            else turn_boundaries[0]
        )
        # 在 limit 及之前的边界里，选最晚的满足半水位的（淘汰得最狠但不少 N 轮）。
        # 若 N 轮本身就超水位，退回 limit（宁超预算不缺上下文）。
        start_idx = limit
        for idx in reversed(turn_boundaries):
            if idx > limit:
                continue
            if sum(_msg_token_count(m) for m in trimmed[idx:]) <= target:
                start_idx = idx
                break

        # ── P2 智能压缩：尝试用 LLM 摘要被淘汰的旧消息 ─────────────
        # （在裁剪窗口之前尝试，成功则用 SystemMessage 摘要替代旧消息）
        compressed = False  # 标记是否成功做了智能压缩
        if start_idx > 3:  # 至少有几条旧消息才值得摘要
            discarded = trimmed[3:start_idx]  # 跳过 system prompt（通常在 index 0-2）
            if discarded:
                summary = _try_llm_summarize(discarded)
                if summary:
                    # 摘要成功：把旧消息换成一条 SystemMessage 摘要
                    trimmed = (
                        trimmed[:3]
                        + [SystemMessage(content=summary)]
                        + trimmed[start_idx:]
                    )
                    compressed = True
                    print(
                        f"[TRIM] ✅ 智能压缩: {len(discarded)} 条旧消息 → "
                        f"摘要 {len(summary)} 字符, 保留 {start_idx} 起的消息"
                    )
                else:
                    # 摘要失败：fallback 到暴力截断
                    print(
                        f"[TRIM] 水位淘汰触发（压缩失败 fallback）: {self.max_tokens} -> "
                        f"保留 {start_idx} 起的 "
                        f"{sum(_msg_token_count(m) for m in trimmed[start_idx:])} tok"
                    )
            else:
                print(
                    f"[TRIM] 水位淘汰触发: {self.max_tokens} -> 保留 {start_idx} 起的 "
                    f"{sum(_msg_token_count(m) for m in trimmed[start_idx:])} tok"
                )
        else:
            print(
                f"[TRIM] 水位淘汰触发: {self.max_tokens} -> 保留 {start_idx} 起的 "
                f"{sum(_msg_token_count(m) for m in trimmed[start_idx:])} tok"
            )

        # 最终决定传给 LLM 的消息列表
        if compressed:
            # 智能压缩成功：trimmed 已经被替换为摘要 + 保留窗口
            request.messages = trimmed
        else:
            # 暴力截断：从 start_idx 开始取
            request.messages = trimmed[start_idx:]

        _store_context(
            thread_id or "unknown",
            sum(_msg_token_count(m) for m in request.messages),
            self.max_tokens,
        )
        return handler(request)

    async def awrap_model_call(self, request, handler):
        """异步版裁剪 — LangGraph 在 async 上下文（WebSockets/asyncio）自动走这条路径。

        sync 和 async 必须**同时实现**，否则 create_agent 只注册有实现的那个，
        另一条路径会报 NotImplementedError。
        """
        print(f"[ASYNC-MW] awrap_model_call ENTER", flush=True)
        # 深度打印 state
        state = getattr(request, "state", None)
        if isinstance(state, dict):
            print(f"[ASYNC-MW] state keys = {list(state.keys())}", flush=True)
            for k, v in state.items():
                if k.startswith("_") or k in ("messages",):
                    print(f"  state[{k!r}] = {repr(v)[:200]}", flush=True)
        cfg = getattr(request, "config", None)
        print(f"[ASYNC-MW] request.config = {cfg!r}", flush=True)
        print(f"[ASYNC-MW] request type = {type(request).__name__}", flush=True)

        original_messages = request.messages
        if not original_messages:
            return await handler(request)

        # —— 动态 context window 刷新 ——
        llm_model_name = getattr(request, "model_name", None)
        if not llm_model_name:
            llm_model_name = getattr(request, "model_id", None)
        if not llm_model_name:
            cfg = getattr(request, "config", None)
            if isinstance(cfg, dict):
                llm_model_name = cfg.get("model_name")
        self.max_tokens = self._resolve_max_tokens(llm_model_name)

        thread_id = _extract_thread_id(request)

        # 1. 裁剪超长 ToolMessage
        trimmed = []
        for msg in original_messages:
            if isinstance(msg, ToolMessage):
                trimmed.append(_truncate_tool_message(msg, self.tool_result_max_chars))
            else:
                trimmed.append(msg)

        # 2. 计算 token
        total_tokens = sum(_msg_token_count(m) for m in trimmed)
        _store_context(thread_id or "unknown", total_tokens, self.max_tokens)

        if total_tokens <= self.max_tokens:
            request.messages = trimmed
            return await handler(request)

        # 3. 水位淘汰（sync 版逻辑相同）
        turn_boundaries = [
            i for i, m in enumerate(trimmed) if isinstance(m, HumanMessage)
        ]
        if not turn_boundaries:
            request.messages = trimmed
            return await handler(request)

        target = self.max_tokens // 2
        limit = (
            turn_boundaries[-self.keep_recent_turns]
            if len(turn_boundaries) >= self.keep_recent_turns
            else turn_boundaries[0]
        )
        start_idx = limit
        for idx in reversed(turn_boundaries):
            if idx > limit:
                continue
            if sum(_msg_token_count(m) for m in trimmed[idx:]) <= target:
                start_idx = idx
                break

        # P2 智能压缩（async 版也做）
        compressed = False
        if start_idx > 3:
            discarded = trimmed[3:start_idx]
            if discarded:
                summary = _try_llm_summarize(discarded)
                if summary:
                    trimmed = (
                        trimmed[:3]
                        + [SystemMessage(content=summary)]
                        + trimmed[start_idx:]
                    )
                    compressed = True
                    print(
                        f"[TRIM] ✅ 智能压缩 (async): {len(discarded)} 条 → 摘要 {len(summary)} 字符",
                        flush=True,
                    )

        if compressed:
            request.messages = trimmed
        else:
            request.messages = trimmed[start_idx:]

        _store_context(
            thread_id or "unknown",
            sum(_msg_token_count(m) for m in request.messages),
            self.max_tokens,
        )
        return await handler(request)


def _try_llm_summarize(messages: list[BaseMessage], max_chars: int = 600) -> str | None:
    """用轻量 LLM 调用把一批旧消息压缩成一段摘要。

    失败时返回 None（fallback 到暴力截断）。
    设计：thinking=False, max_tokens=512 —— 把摘要本身的 token 成本压到最低，
    避免"为了省 token 反而多花 token"。
    """
    try:
        from crawagent.llm.model import get_llm
        from langchain_core.messages import HumanMessage as HMsg

        # 把消息序列化成可读文本
        lines: list[str] = []
        for m in messages:
            role = (
                "用户"
                if isinstance(m, HumanMessage)
                else "AI"
                if isinstance(m, AIMessage)
                else "工具"
            )
            content = m.content if isinstance(m.content, str) else str(m.content or "")
            # 截断单条过长内容
            if len(content) > 300:
                content = content[:280] + "...(truncated)"
            lines.append(f"[{role}] {content}")
        history_text = "\n".join(lines)

        llm = get_llm(thinking=False, max_tokens=512)
        result = llm.invoke(
            [
                HMsg(
                    content=(
                        "请用一段简洁的中文摘要概括以下对话历史的关键信息：\n"
                        "包括：用户的主要目标、已尝试的方法、遇到的问题、关键发现、当前进度。\n"
                        f"控制在 {max_chars} 字以内，只写摘要本身，不要其他说明。\n\n"
                        f"--- 对话历史 ---\n{history_text}"
                    )
                )
            ]
        )

        summary = (
            result.content
            if isinstance(result.content, str)
            else str(result.content or "")
        )
        summary = summary.strip()
        if summary and len(summary) > 20:  # 最低质量门槛
            return f"[以下是早期对话的摘要压缩，原始内容已省略]\n{summary}"
        return None

    except Exception as e:
        # 任何异常（网络/API Key/超时等）都静默 fallback，不能因为压缩失败导致对话挂掉
        print(
            f"[TRIM] 智能压缩失败（静默 fallback）: {e.__class__.__name__}: {str(e)[:80]}"
        )
        return None
