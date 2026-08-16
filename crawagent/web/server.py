"""CrawAgent WebUI — FastAPI + WebSocket 服务

启动方式：
    uv run --extra api python -m crawagent.web.server
    # 或已 sync 过 api 依赖： uv run python -m crawagent.web.server

设计要点：
    - 复用 crawagent.graph.agent.get_agent + SqliteSaver 会话持久化，
      与 CLI（main.py）共享 data/sessions.db，同一 session_id 两端互通。
    - Agent 的同步 stream 生成器跑在线程里，事件经 asyncio.Queue 桥接
      推送到 WebSocket，前端实时看到 工具调用 → 工具结果 → AI 回复 的全过程。
    - 每轮结束推送一条与终端同款的 metrics.status_line() 状态栏。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from crawagent.config.settings import get_settings
from crawagent.graph.agent import get_agent
from crawagent.observability.metrics import SessionMetrics, _fmt_tokens, usage_from_message

# Vue 工程（项目根目录 web/）的构建产物
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
TOOL_RESULT_PREVIEW = 600  # 推送给前端的工具结果预览长度
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

app = FastAPI(title="CrawAgent WebUI")

# ---- 全局单例：Agent 与 checkpointer 只建一次，跨会话/跨连接复用 ----
_agent = None
_checkpointer: SqliteSaver | None = None
_metrics: dict[str, SessionMetrics] = {}
_session_locks: dict[str, asyncio.Lock] = {}


def _get_checkpointer() -> SqliteSaver:
    """惰性创建 checkpointer（只连数据库，不依赖 API Key）。

    与 _get_agent 分离：会话列表/历史这类"纯数据库读"操作，
    Agent 构建失败（如缺 API Key）时也必须能工作。
    """
    global _checkpointer
    if _checkpointer is None:
        settings = get_settings()
        settings.sessions_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(settings.sessions_db_path), check_same_thread=False)
        _checkpointer = SqliteSaver(conn)
        _checkpointer.setup()
    return _checkpointer


def _get_agent():
    """惰性创建 Agent（首次对话时才初始化，避免无 API Key 时服务起不来）"""
    global _agent
    if _agent is None:
        _agent = get_agent(checkpointer=_get_checkpointer())
    return _agent


def _push(q: asyncio.Queue, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
    """从工作线程向事件循环的队列投递事件（线程安全）"""
    loop.call_soon_threadsafe(q.put_nowait, event)


def _run_turn(session_id: str, text: str, q: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
    """在工作线程中执行一轮对话，把流式事件逐个投递到队列。

    事件协议（JSON）：
        tool_call    {type, name, args}        一次工具调用请求
        tool_result  {type, content}           工具返回（截断预览）
        ai_thinking  {type, id, content}       推理/思考内容（折叠块，默认收起）
        ai_delta     {type, id, delta}         最终回答的 token 增量（流式）
        ai_done      {type, id}                该条流式回复结束
        ai           {type, content}           最终回答（非流式兜底）
        status       {type, line}              终端同款状态栏（前端常驻显示在输入框下方）
        done         {type}                    本轮正常结束
        error        {type, message}           本轮失败
    """
    try:
        agent = _get_agent()
    except Exception as e:  # Agent 初始化失败（如缺 API Key）
        _push(q, loop, {"type": "error", "message": f"Agent 初始化失败: {e}"})
        return

    config = {"configurable": {"thread_id": session_id}}
    metrics = _metrics.setdefault(session_id, SessionMetrics())

    # 先收集检查点里已存在的消息 id，避免把历史消息重放给前端
    shown_ids: set[str] = set()
    try:
        state = agent.get_state(config)
        for m in state.values.get("messages", []):
            if m.id:
                shown_ids.add(m.id)
    except Exception:
        pass

    seen_llm_ids: set[str] = set()
    ended_llm_ids: set[str] = set()  # 已通过 token_usage 结算过的 AIMessage.id
    started_llm_ids: set[str] = set()  # 已 on_llm_start 过的 AIMessage.id
    pending_tool_starts: dict[str, float] = {}
    streamed_ai: dict[str, str] = {}  # msg.id → 已通过 ai_delta 推送的文本（防 values 快照重发）
    thinking_sent: set[str] = set()  # msg.id → 已推送过 ai_thinking
    metrics.turn_begin()

    try:
        # 双模式流：messages 给 token 级增量，values 给工具调用/结果与最终状态
        for mode, payload in agent.stream(
            {"messages": [HumanMessage(content=text)]},
            config=config,
            stream_mode=["messages", "values"],
        ):
            # ---- token 级增量：AI 文本逐字推送 ----
            if mode == "messages":
                chunk, _meta = payload
                if isinstance(chunk, AIMessageChunk) and chunk.id and not chunk.tool_call_chunks:
                    # 首个 chunk 才是真实 LLM 起点（values 快照在整条流结束后才到）
                    if chunk.id not in started_llm_ids:
                        started_llm_ids.add(chunk.id)
                        metrics.on_llm_start()
                    if chunk.content:
                        delta = chunk.content if isinstance(chunk.content, str) else "".join(
                            p.get("text", "") for p in chunk.content if isinstance(p, dict)
                        )
                        if delta:
                            metrics.on_llm_first_token()
                            streamed_ai[chunk.id] = streamed_ai.get(chunk.id, "") + delta
                            _push(q, loop, {"type": "ai_delta", "id": chunk.id, "delta": delta})
                continue

            # ---- 状态快照：工具调用 / 工具结果 / 指标 ----
            event = payload
            for msg in event.get("messages", []):
                if msg.id in shown_ids:
                    continue
                shown_ids.add(msg.id)

                if isinstance(msg, HumanMessage):
                    continue

                if isinstance(msg, AIMessage):
                    if msg.id not in seen_llm_ids:
                        seen_llm_ids.add(msg.id)
                    # 非流式兜底或工具调用消息在 messages 模式无内容块 → 这里补记起点
                    if msg.id not in started_llm_ids:
                        started_llm_ids.add(msg.id)
                        metrics.on_llm_start()

                    # 推理内容（DeepSeek 等推理模型的 reasoning_content）—— 一次性发给前端折叠展示
                    reasoning = (msg.additional_kwargs or {}).get("reasoning_content", "")
                    if reasoning and msg.id not in thinking_sent:
                        thinking_sent.add(msg.id)
                        _push(q, loop, {
                            "type": "ai_thinking",
                            "id": msg.id,
                            "content": reasoning,
                        })

                    for tc in msg.tool_calls or []:
                        _push(q, loop, {
                            "type": "tool_call",
                            "name": tc["name"],
                            "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                        })
                        pending_tool_starts[tc["id"]] = time.perf_counter()

                    if msg.content:
                        metrics.on_llm_first_token()
                        if msg.id in streamed_ai:
                            # 已通过 ai_delta 流式推送过，通知前端收尾即可
                            streamed_ai.pop(msg.id, None)
                            _push(q, loop, {"type": "ai_done", "id": msg.id})
                        else:
                            # 非流式兜底（模型未走 token 流）
                            content = msg.content if isinstance(msg.content, str) else str(msg.content)
                            _push(q, loop, {"type": "ai", "content": content})

                    token_usage = usage_from_message(msg)
                    if token_usage:
                        metrics.on_llm_end(token_usage)
                        ended_llm_ids.add(msg.id)

                elif isinstance(msg, ToolMessage):
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    preview = content[:TOOL_RESULT_PREVIEW] + ("…" if len(content) > TOOL_RESULT_PREVIEW else "")
                    _push(q, loop, {"type": "tool_result", "content": preview})
                    start = pending_tool_starts.pop(msg.tool_call_id, None)
                    if start is not None:
                        metrics.tool_total_time += max(0.0, time.perf_counter() - start)
                    metrics.tool_call_count += 1

        metrics.turn_end()

        # 兜底：流式最后一条 AIMessage 若没带 token_usage，从 graph state 里补统计
        # （与 main.py 相同的策略：只补 token 数，不重复计调用数与耗时）
        if seen_llm_ids:
            try:
                final_state = agent.get_state(config)
                for m in reversed(final_state.values.get("messages", [])):
                    if isinstance(m, AIMessage) and m.id in seen_llm_ids and m.id not in ended_llm_ids:
                        tu = usage_from_message(m)
                        if tu:
                            metrics.input_tokens += int(tu.get("prompt_tokens") or 0)
                            metrics.output_tokens += int(tu.get("completion_tokens") or 0)
                            metrics.cache_hit_tokens += int(tu.get("prompt_cache_hit_tokens") or 0)
                            metrics.cache_miss_tokens += int(tu.get("prompt_cache_miss_tokens") or 0)
                        break
            except Exception:
                pass

        _push(q, loop, {"type": "status", "line": metrics.status_line()})
        _push(q, loop, {"type": "done"})

    except Exception as e:
        metrics.turn_end()
        _push(q, loop, {"type": "error", "message": str(e)})


async def _stream_turn(session_id: str, text: str, ws: WebSocket) -> None:
    """把同步的 _run_turn 桥接到异步 WebSocket：队列出队一条就推一条"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    fut = loop.run_in_executor(None, _run_turn, session_id, text, q, loop)
    try:
        while True:
            event = await q.get()
            await ws.send_text(json.dumps(event, ensure_ascii=False))
            if event["type"] in ("done", "error"):
                break
    finally:
        # 等待工作线程收尾；_run_turn 内部已兜底异常，这里再防一层意外
        try:
            await fut
        except Exception:
            pass


def _session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


# ---- 路由 ----

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIST / "index.html")


def _reconstruct_status(messages) -> str | None:
    """从检查点消息重建状态栏（服务重启 / CLI 会话没有内存指标时兜底）。

    耗时类数据（LLM/工具时长、首 token、tok/s）无法从 DB 恢复，
    只展示轮数、步数与 token 统计。
    """
    turn_count = 0
    llm_count = 0
    tool_count = 0
    input_tokens = output_tokens = 0
    cache_hit = cache_miss = 0
    for m in messages:
        if isinstance(m, HumanMessage):
            turn_count += 1
        elif isinstance(m, ToolMessage):
            tool_count += 1
        elif isinstance(m, AIMessage):
            llm_count += 1
            tu = (m.response_metadata or {}).get("token_usage") if isinstance(m.response_metadata, dict) else None
            if tu:
                prompt_tokens = int(tu.get("prompt_tokens") or 0)
                completion_tokens = int(tu.get("completion_tokens") or 0)
                hit = int(tu.get("prompt_cache_hit_tokens") or 0)
                miss = int(tu.get("prompt_cache_miss_tokens") or 0)
                if prompt_tokens == 0 and (hit or miss):
                    prompt_tokens = hit + miss
                input_tokens += prompt_tokens
                output_tokens += completion_tokens
                cache_hit += hit
                cache_miss += miss
    if turn_count == 0 and llm_count == 0 and tool_count == 0:
        return None
    parts = [f"{turn_count} 轮 · {llm_count + tool_count} 步"]
    total_cache = cache_hit + cache_miss
    if total_cache > 0:
        parts.append(f"缓存命中 {cache_hit / total_cache * 100:.0f}%")
    parts.append(f"输入 {_fmt_tokens(input_tokens)} tok · 输出 {_fmt_tokens(output_tokens)} tok")
    return "  |  ".join(parts)


@app.get("/api/history/{session_id}")
async def history(session_id: str) -> dict[str, Any]:
    """读取指定会话的历史消息与状态栏（供刷新页面/切换会话后恢复视图）"""
    def _load() -> dict[str, Any]:
        try:
            agent = _get_agent()
        except Exception:
            return {"messages": [], "status": None}
        config = {"configurable": {"thread_id": session_id}}
        try:
            state = agent.get_state(config)
        except Exception:
            return {"messages": [], "status": None}
        messages = state.values.get("messages", [])
        items: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                items.append({"role": "user", "content": content})
            elif isinstance(msg, AIMessage):
                for tc in msg.tool_calls or []:
                    items.append({
                        "role": "tool_call",
                        "name": tc["name"],
                        "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                    })
                reasoning = (msg.additional_kwargs or {}).get("reasoning_content", "")
                if reasoning:
                    items.append({"role": "thinking", "content": reasoning})
                if msg.content:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    items.append({"role": "ai", "content": content})
            elif isinstance(msg, ToolMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                preview = content[:TOOL_RESULT_PREVIEW] + ("…" if len(content) > TOOL_RESULT_PREVIEW else "")
                items.append({"role": "tool_result", "content": preview})
        # 状态栏：优先用内存里的会话指标（含耗时/性能），否则从检查点重建
        metrics = _metrics.get(session_id)
        status = metrics.status_line() if metrics is not None else _reconstruct_status(messages)
        return {"messages": items, "status": status}

    return await asyncio.to_thread(_load)


@app.get("/api/sessions")
async def sessions() -> dict[str, Any]:
    """列出 sessions.db 里的全部会话（含最新消息预览），供侧栏展示与切换。

    CLI（main.py）与 WebUI 共享同一个 sessions.db，所以终端里聊过的会话
    也会出现在这里，可直接切过去继续。
    """
    def _load() -> list[dict[str, Any]]:
        try:
            checkpointer = _get_checkpointer()
        except Exception:
            return []
        try:
            rows = checkpointer.conn.execute(
                "SELECT thread_id, MAX(rowid) AS latest FROM checkpoints "
                "GROUP BY thread_id ORDER BY latest DESC LIMIT 50"
            ).fetchall()
        except Exception:
            return []

        # 预览需要 Agent.get_state；Agent 构建失败（如缺 API Key）时
        # 会话列表仍正常展示（预览留空），不因 Key 问题连列表都看不到
        agent = None
        try:
            agent = _get_agent()
        except Exception:
            pass

        result: list[dict[str, Any]] = []
        for (thread_id, _latest) in rows:
            preview = ""
            if agent is not None:
                try:
                    state = agent.get_state({"configurable": {"thread_id": thread_id}})
                    for m in reversed(state.values.get("messages", [])):
                        if isinstance(m, (HumanMessage, AIMessage)) and m.content:
                            c = m.content if isinstance(m.content, str) else str(m.content)
                            preview = c.replace("\n", " ").strip()[:60]
                            break
                except Exception:
                    pass
            result.append({"id": thread_id, "preview": preview})
        return result

    return {"sessions": await asyncio.to_thread(_load)}


# ---- 设置（API Key / 模型） ----

def _mask_api_key(key: str) -> str:
    """API Key 脱敏：保留首尾各 4 位，中间用星号"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * min(12, len(key) - 8)}{key[-4:]}"


def _read_env_lines() -> list[str]:
    """读取 .env 原始行（保留注释与空行），用于改写时尽量不破坏格式"""
    if not ENV_FILE.exists():
        return []
    return ENV_FILE.read_text(encoding="utf-8").splitlines()


def _save_env_updates(updates: dict[str, str]) -> None:
    """把 updates 写回 .env：保留注释与其他键，更新已存在的键、追加新键"""
    lines = _read_env_lines()
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


@app.get("/api/settings")
async def get_settings_route() -> dict[str, Any]:
    """返回当前配置（API Key 脱敏），供设置面板展示

    若存储的 Key 含星号（被占位符覆盖等异常），视为未配置，避免把
    掩码回显给前端、再被原样写回造成二次破坏。
    """
    s = get_settings()
    raw = s.openai_api_key
    valid = bool(raw) and "*" not in raw
    return {
        "openai_api_key": _mask_api_key(raw) if valid else "",
        "openai_api_key_set": valid,
        "openai_base_url": s.openai_base_url,
        "default_model": s.default_model,
    }


@app.post("/api/settings")
async def post_settings_route(payload: dict = Body(...)) -> dict[str, Any]:
    """保存设置到 .env，并失效 Agent 单例（下次对话用新配置重建）"""
    updates: dict[str, str] = {}
    key = payload.get("openai_api_key")
    if isinstance(key, str) and key.strip() and "*" not in key:
        # 真实 API Key 不含星号；掩码/占位串一律忽略，防止覆盖原值
        updates["OPENAI_API_KEY"] = key.strip()
    for env_key, payload_key in (
        ("OPENAI_BASE_URL", "openai_base_url"),
        ("DEFAULT_MODEL", "default_model"),
    ):
        v = payload.get(payload_key)
        if isinstance(v, str) and v.strip():
            updates[env_key] = v.strip()

    if updates:
        _save_env_updates(updates)

    # 失效 Agent + checkpointer，下次对话时按新配置重建
    global _agent, _checkpointer
    _agent = None
    _checkpointer = None

    s = get_settings()
    return {
        "ok": True,
        "saved": list(updates.keys()),
        "openai_api_key_set": bool(s.openai_api_key),
        "openai_base_url": s.openai_base_url,
        "default_model": s.default_model,
    }


@app.post("/api/settings/test")
async def test_settings_route(payload: dict = Body(...)) -> dict[str, Any]:
    """用给定的配置对一个 ping 调用做连通性测试（不影响当前 Agent）"""
    try:
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        s = get_settings()
        api_key = payload.get("openai_api_key") or s.openai_api_key
        base_url = payload.get("openai_base_url") or s.openai_base_url
        model = payload.get("default_model") or s.default_model

        if not api_key or "*" in api_key:
            return {"ok": False, "error": "缺少 API Key"}

        llm = ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=0,
            timeout=30,
            max_retries=0,
        )

        def _invoke() -> str:
            result = llm.invoke([HumanMessage(content="ping")])
            return result.content if isinstance(result.content, str) else str(result.content)

        content = await asyncio.to_thread(_invoke)
        return {"ok": True, "response": (content or "").strip()[:80] or "（空响应）"}
    except Exception as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__}


@app.websocket("/ws/{session_id}")
async def chat_ws(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if payload.get("type") != "message":
                continue
            text = str(payload.get("content", "")).strip()
            if not text:
                continue

            lock = _session_lock(session_id)
            if lock.locked():
                await ws.send_text(json.dumps(
                    {"type": "error", "message": "上一任务仍在爬行中，请稍候…"}, ensure_ascii=False))
                continue

            async with lock:
                await _stream_turn(session_id, text, ws)

    except WebSocketDisconnect:
        pass


def main() -> None:
    import uvicorn

    from fastapi.staticfiles import StaticFiles

    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    # Vue 构建产物的静态资源目录（存在才挂载，挂载晚于 API/WS 路由注册）
    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
