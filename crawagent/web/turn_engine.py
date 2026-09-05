"""轮次事件泵 — _emit / _run_turn / _stream_turn。

从 server.py 抽出：Agent 的同步 stream 生成器跑在工作线程，事件经
asyncio.Queue 桥接到 WebSocket 订阅协程；轮次事件同时 append 进
EventLog，重连按 cursor 增量重放，保证 done/error 不因订阅者切换丢失。

三个函数的引用关系：
    _stream_turn（协程）→ run_in_executor(_run_turn)（线程）
    _run_turn → _emit（线程→队列+事件日志）
    server.chat_ws → _stream_turn

共享状态（_active_turns / _metrics / Agent 单例）仍在 crawagent/web/state.py，
本模块只读写不持有。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from fastapi import WebSocket
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, RemoveMessage, ToolMessage

from crawagent.observability.metrics import SessionMetrics, usage_from_message
from crawagent.tools.save_tool import set_current_session
from crawagent.web.event_log import EventLog
from crawagent.web.state import (
    _active_turns,
    _metrics,
    clear_session_error,
    get_agent,
    get_checkpointer,
    save_metrics,
    save_session_error,
    session_lock,
    warm_cache,
)


_AUTO_TITLE_PROMPT = (
    "请根据以下爬虫任务的用户指令和 AI 回复，生成一个 4-8 个字的中文标题，"
    "概括任务的核心目标（如爬什么网站、爬什么数据）。\n"
    "只输出标题正文，不要引号、不要标点、不要解释、不要换行。\n\n"
    "=== 用户指令 ===\n{user}\n\n=== AI 回复摘要 ===\n{ai}\n"
)

# 正在后台生成标题的会话，防止重复 spawn
_pending_titles: set[str] = set()


def _maybe_auto_title(session_id: str, agent: Any, config: dict, fallback_text: str = "") -> None:
    """轮次结束后检查：如果该会话还没有标题，后台调小模型自动生成。

    已有标题（手动改名或前轮已生成）→ 跳过。不覆盖用户的手动命名。
    fallback_text：检查点里读不到用户消息时的兜底素材（如本轮刚发出、
    尚未落库就失败的原始输入），保证失败轮次也能命名。
    """
    if session_id in _pending_titles:
        return
    conn = get_checkpointer().conn
    try:
        existing = conn.execute(
            "SELECT 1 FROM session_titles WHERE thread_id = ?", (session_id,)
        ).fetchone()
        if existing:
            return  # 已有标题，不覆盖
    except Exception:
        return  # 表不存在等异常 → 不阻断

    # 取首条用户消息 + 首条 AI 回复做素材
    try:
        state = agent.get_state(config)
        msgs = state.values.get("messages", [])
    except Exception:
        return
    first_user = ""
    first_ai = ""
    for m in msgs:
        if isinstance(m, HumanMessage) and not first_user:
            c = m.content if isinstance(m.content, str) else str(m.content)
            first_user = c.strip()[:300]
        elif isinstance(m, AIMessage) and not m.tool_calls and not first_ai:
            c = m.content if isinstance(m.content, str) else str(m.content)
            first_ai = c.strip()[:200]
        if first_user and first_ai:
            break
    if not first_user:
        first_user = (fallback_text or "").strip()[:300]
    if not first_user:
        return  # 没有用户消息，无法命名

    _pending_titles.add(session_id)
    threading.Thread(
        target=_generate_title,
        args=(session_id, first_user, first_ai),
        daemon=True,
    ).start()


def _generate_title(session_id: str, user_text: str, ai_text: str) -> None:
    """后台线程：调小模型生成标题写入 session_titles 表；LLM 失败时退回用户关键词兜底。"""
    try:
        title = ""
        try:
            from crawagent.llm.model import get_llm
            llm = get_llm(thinking=False, max_tokens=64)
            prompt = _AUTO_TITLE_PROMPT.format(user=user_text, ai=ai_text or "（无回复）")
            resp = llm.invoke(prompt)
            title = resp.content.strip() if isinstance(resp.content, str) else str(resp.content).strip()
            # 清理：去引号、去换行、限长
            title = title.strip("\"'""''「」【】 \n\t")
        except Exception as e:
            print(f"[AUTO_TITLE] {session_id} LLM 调用失败，改用用户关键词兜底: {e}")
        if not title:
            title = _fallback_title(user_text)
            if title:
                print(f"[AUTO_TITLE] {session_id} 兜底标题: {title}")
        if title and len(title) > 30:
            title = title[:30]
        if not title:
            return  # LLM 与兜底都提不出（用户输入为空）
        conn = get_checkpointer().conn
        conn.execute(
            "INSERT INTO session_titles (thread_id, title) VALUES (?, ?) "
            "ON CONFLICT(thread_id) DO NOTHING",  # DO NOTHING：不覆盖在此期间可能的手动改名
            (session_id, title),
        )
        conn.commit()
        print(f"[AUTO_TITLE] {session_id}: {title}")
    except Exception as e:
        print(f"[AUTO_TITLE] {session_id} 写入失败（忽略）: {e}")
    finally:
        _pending_titles.discard(session_id)


def _fallback_title(user_text: str) -> str:
    """LLM 命名失败时的兜底标题：取用户首条指令的第一行（去掉 markdown 符号）截短。"""
    lines = (user_text or "").strip().splitlines()
    if not lines:
        return ""
    return lines[0].lstrip("#>-*· ").strip()[:16]


def _emit(session_id: str, q: asyncio.Queue, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
    """worker（线程）记录一条轮次事件并唤醒订阅者（线程安全）。

    事件同时 append 到 _active_turns[session]["events"] 事件日志（EventLog）：
    订阅者按 cursor 从日志补发，重连时从 0 重放，保证 done 等收尾事件不因
    订阅者切换（页面刷新）而丢失——旧方案直接把事件塞队列，单消费者
    在 WebSocket 已死时会把事件"吞掉"，导致前端永远收不到 done。
    """
    entry = _active_turns.get(session_id)
    if entry is not None:
        entry["events"].append(event)
    loop.call_soon_threadsafe(q.put_nowait, event)


def _run_turn(session_id: str, text: str, q: asyncio.Queue, loop: asyncio.AbstractEventLoop, model: str | None = None) -> None:
    """在工作线程中执行一轮对话，把流式事件逐个投递到队列。

    model：前端本条消息指定的模型 ID（None 时用默认解析顺序）。

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
        agent = get_agent(model)
    except Exception as e:  # Agent 初始化失败（如缺 API Key）
        save_session_error(session_id, f"Agent 初始化失败: {e}")
        _emit(session_id, q, loop, {"type": "error", "message": f"Agent 初始化失败: {e}"})
        return

    # 缓存预热：把 system+tools 前缀同步写入 DeepSeek 服务端缓存。
    # 会话早期 LLM 决策步间隔只有几秒，不预热时上一步缓存未写完下一步已发出，
    # 连环部分命中（实测一个会话前 6 分钟产生 ~200K miss）。失败已在 warm_cache 内静默。
    warm_cache(model)

    # 注入当前会话 ID：save_record / list_crawled_resources 依赖它做会话隔离，
    # 保证记录不会串到其他会话（工具执行与本函数同线程，ContextVar 安全）
    set_current_session(session_id)

    # 重置 ScriptForcerMiddleware：每轮新对话开始时清空失败计数和循环检测状态，
    # 避免跨轮次累积导致误触发强制总结
    if hasattr(agent, "reset_turn_state"):
        agent.reset_turn_state()
    elif hasattr(agent, "_script_forcer"):
        agent._script_forcer._reset()

    config = {"configurable": {"thread_id": session_id}}
    metrics = _metrics.setdefault(session_id, SessionMetrics())

    # 修复悬空 tool_calls：任务在工具执行中被中断（如后端重启）会留下
    # "AIMessage 带 tool_calls 但无对应 ToolMessage"的非法历史，之后每轮
    # LLM 调用都会 400（insufficient tool messages）→ 任务看似"自动终止"。
    # 开局检测并移除这些悬空 AIMessage，会话即可恢复正常对话。
    try:
        state = agent.get_state(config)
        msgs = state.values.get("messages", [])
        dangling = []
        for i, m in enumerate(msgs):
            if isinstance(m, AIMessage) and m.tool_calls:
                answered = {t.tool_call_id for t in msgs[i + 1:] if isinstance(t, ToolMessage)}
                if any(tc["id"] not in answered for tc in m.tool_calls):
                    dangling.append(RemoveMessage(id=m.id))
        if dangling:
            agent.update_state(config, {"messages": dangling})
            print(f"[TURN_REPAIR] {session_id}: removed {len(dangling)} dangling tool_call message(s)")
    except Exception:
        pass  # 修复失败不阻断本轮，最坏情况是历史仍非法、LLM 报 400

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
        # 设置 ContextVar：middleware 用它拿 thread_id（比 LangGraph state 更可靠）
        from crawagent.graph.agent import ctx_session_id
        ctx_session_id.set(session_id)

        # 双模式流：messages 给 token 级增量，values 给工具调用/结果与最终状态
        for mode, payload in agent.stream(
            {"messages": [HumanMessage(content=text)]},
            config=config,
            stream_mode=["messages", "values"],
        ):
            # 用户点了"停止"→ 尽快退出（当前工具调用完成后生效）
            if _active_turns.get(session_id, {}).get("cancelled"):
                _emit(session_id, q, loop, {"type": "status", "line": metrics.status_line() + "  [已中断]"})
                break
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
                            _emit(session_id, q, loop, {"type": "ai_delta", "id": chunk.id, "delta": delta})
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
                    # 非推理模型：AIMessage 如果带 tool_calls，其 msg.content 往往就是工具调用前的
                    # "我需要调用 XXX 工具"计划（中文自然语言或 <think> 片段）。这里也算思考。
                    plan = ""
                    if not reasoning and msg.tool_calls and isinstance(msg.content, str):
                        plan = msg.content.strip()
                    # <think> 兼容：某些供应商把 reasoning 放在 <think>...</think> 包裹的 content 里
                    if not reasoning and isinstance(msg.content, str) and msg.content.startswith("<think>"):
                        end = msg.content.find("</think>")
                        if end > 0:
                            reasoning = msg.content[7:end].strip()
                    thinking_text = reasoning or plan
                    if thinking_text and msg.id not in thinking_sent:
                        thinking_sent.add(msg.id)
                        _emit(session_id, q, loop, {
                            "type": "ai_thinking",
                            "id": msg.id,
                            "content": thinking_text,
                        })
                        # 同步打印：便于后端观察每轮的思考量
                        print(f"[THINKING] len={len(thinking_text)} source={'reasoning_content' if reasoning else ('plan' if plan else 'think-tag')} id={str(msg.id)[:8]}")

                    for tc in msg.tool_calls or []:
                        _emit(session_id, q, loop, {
                            "type": "tool_call",
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                            "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                        })
                        pending_tool_starts[tc["id"]] = time.perf_counter()

                    # 关键去重：如果 msg 有 tool_calls 且 content == plan（思考文本），
                    # 说明这段内容已经通过 ai_thinking 推了，不要再作为 AI 回答推一次。
                    # 只有真正的最终 AI 回答（没 tool_calls，或 content 与 plan 不一致）
                    # 才推送 ai 事件作为独立卡片。
                    is_plan_content = bool(plan) and isinstance(msg.content, str) and (
                        msg.content.strip() == plan
                    )
                    if msg.tool_calls and is_plan_content:
                        # 这段已经是思考了，丢弃 ai 重复推送
                        # 如果有流式缓存（ai_delta 推过），也要清掉以避免留空卡片
                        if msg.id in streamed_ai:
                            streamed_ai.pop(msg.id, None)
                    elif msg.content:
                        metrics.on_llm_first_token()
                        if msg.id in streamed_ai:
                            # 已通过 ai_delta 流式推送过，通知前端收尾即可
                            streamed_ai.pop(msg.id, None)
                            _emit(session_id, q, loop, {"type": "ai_done", "id": msg.id})
                        else:
                            # 非流式兜底（模型未走 token 流）
                            content = msg.content if isinstance(msg.content, str) else str(msg.content)
                            _emit(session_id, q, loop, {"type": "ai", "content": content})

                    token_usage = usage_from_message(msg)
                    if token_usage:
                        metrics.on_llm_end(token_usage)
                        ended_llm_ids.add(msg.id)

                elif isinstance(msg, ToolMessage):
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    _emit(session_id, q, loop, {
                        "type": "tool_result",
                        "tool_call_id": msg.tool_call_id,
                        "content": content,
                    })
                    start = pending_tool_starts.pop(msg.tool_call_id, None)
                    if start is not None:
                        metrics.tool_total_time += max(0.0, time.perf_counter() - start)
                    metrics.tool_call_count += 1
                    # 长任务中每完成一个工具就推一次中间状态，状态栏实时跳动
                    _emit(session_id, q, loop, {"type": "status", "line": metrics.status_line()})

        metrics.turn_end()

        # 首轮后自动命名：没有标题的会话后台调小模型生成 4-8 字标题
        try:
            _maybe_auto_title(session_id, agent, config)
        except Exception:
            pass  # 自动命名失败不阻断轮次

        # 兜底：流式最后一条 AIMessage 若没带 token_usage，从 graph state 里补统计
        # （只补 token 数，不重复计调用数与耗时）
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

        _emit(session_id, q, loop, {"type": "status", "line": metrics.status_line()})
        clear_session_error(session_id)  # 本轮成功：清掉失败记录，刷新后不再恢复红条
        _emit(session_id, q, loop, {"type": "done"})
        # 落盘 metrics：server 重启后状态栏能恢复上次数据
        save_metrics(session_id, metrics)

    except Exception as e:
        metrics.turn_end()
        # 异常退出也落盘（保留已完成的统计，不丢）
        save_metrics(session_id, metrics)
        # 终端同步打印：轮次异常（如 429 配额耗尽）不能只在前端可见，
        # 否则排查时会被 warm_cache 静默失败的 429 误导
        print(f"[TURN_ERROR] session={session_id}: {type(e).__name__}: {e}")
        # 持久化失败原因：刷新页面后 history 接口仍能返回，红条不丢
        save_session_error(session_id, str(e) or type(e).__name__)
        _emit(session_id, q, loop, {"type": "error", "message": str(e)})
        # 失败轮次也要尝试命名（LLM 挂了就用用户输入兜底），否则会话永远是编码名
        try:
            _maybe_auto_title(session_id, agent, config, fallback_text=text)
        except Exception:
            pass
    finally:
        # 任务结束后清理 _active_turns，允许新的任务或重连接管
        loop.call_soon_threadsafe(lambda sid=session_id: _active_turns.pop(sid, None))


async def _stream_turn(session_id: str, text: str, ws: WebSocket, create: bool = True, model: str | None = None) -> None:
    """执行一轮对话并把事件流式推送到 WebSocket。

    事件模型：worker 把每条事件 append 到轮次的事件日志（entry["events"]），
    本协程按 cursor 从日志增量发送。重连时 cursor=0 全量重放，事件不会因
    订阅者切换（页面刷新）而丢失。create=False 时只订阅不创建任务。

    支持重连：如果同 session_id 有正在运行的任务，订阅其事件日志继续接收。
    """
    loop = asyncio.get_running_loop()
    lock = session_lock(session_id)

    async with lock:
        if session_id in _active_turns:
            # 重连：订阅正在运行的任务（事件日志从 cursor=0 全量重放，不丢事件）
            active = _active_turns[session_id]
            active["ws_id"] = id(ws)  # 标记当前订阅者，旧 _stream_turn 检测到后退出
            q = active["queue"]
        elif not create:
            # 重连但轮次已结束（worker 的 finally 已清理条目）。
            # 必须补发 done 让前端 endTurn，否则恢复过 resumed 的页面 typing 永远转下去。
            await ws.send_text(json.dumps({"type": "done"}, ensure_ascii=False))
            return
        else:
            # 创建新任务
            q = asyncio.Queue()
            _active_turns[session_id] = {
                "queue": q, "loop": loop, "ws_id": id(ws), "cancelled": False,
                "events": EventLog(),
            }
            fut = loop.run_in_executor(None, _run_turn, session_id, text, q, loop, model)
            _active_turns[session_id]["future"] = fut

        cursor = 0  # 事件日志已发送位置
        progress_heartbeat: float = 0.0
        events = None  # 本地引用：worker finally 清掉 entry 后，仍能把日志里的收尾事件（error/done）拉完
        try:
            while True:
                entry = _active_turns.get(session_id)
                if entry is not None:
                    events = entry["events"]
                elif events is None:
                    # 防御：轮次从未创建（正常路径走不到）
                    await ws.send_text(json.dumps({"type": "done"}, ensure_ascii=False))
                    return
                if entry is not None and entry.get("ws_id") != id(ws):
                    return
                batch, cursor = events.since(cursor)
                for ev in batch:
                    await ws.send_text(json.dumps(ev, ensure_ascii=False))
                    if ev["type"] in ("done", "error"):
                        return
                if entry is None:
                    # 轮次已结束且日志已拉完，却没看到收尾事件（worker 崩溃未发 done）→ 补发
                    await ws.send_text(json.dumps({"type": "done"}, ensure_ascii=False))
                    return
                # 心跳：每 ~2 秒扫描子 Agent 的进度队列。video_site_expert 等子工具
                # 会长时间同步阻塞（搜索+浏览+探测），用户需要"实时"进展感，而不能
                # 只看到"dig deep..."。
                now_ts = loop.time()
                if now_ts - progress_heartbeat > 2.0:
                    progress_heartbeat = now_ts
                    try:
                        from crawagent.tools.progress import consume_new
                        prog_events = consume_new()
                        for pe in prog_events:
                            # 只推本会话相关 + 仍在进行中的（减少噪音）
                            ws_ev = {"type": "progress", **pe}
                            await ws.send_text(json.dumps(ws_ev, ensure_ascii=False))
                            if pe["done"] and pe["error"]:
                                # 异常完成直接推一条 status 提示（UI 状态栏更显眼）
                                await ws.send_text(json.dumps({
                                    "type": "status",
                                    "line": f"子任务失败：{pe['error'][:60]}"
                                }, ensure_ascii=False))
                    except Exception as _pe:
                        pass  # 进度上报失败不影响主对话
                # 已追平事件日志，等 worker 的下一次唤醒通知（每 0.5s 会 wake 一次）
                try:
                    await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
        except Exception:
            # 发送失败（ws 已死）直接退出：事件仍留在日志里，新订阅者可全量重放
            return


