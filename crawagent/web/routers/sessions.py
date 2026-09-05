"""会话相关 API：会话列表 / 历史消息 / 删除（单个 + 批量）。"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Response
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from urllib.parse import quote

from crawagent.config.settings import get_settings
from crawagent.observability.metrics import _fmt_tokens
from crawagent.web.state import (
    TOOL_RESULT_PREVIEW,
    _active_turns,
    _metrics,
    _session_locks,
    get_agent,
    get_checkpointer,
    get_metrics,
    get_session_error,
)

router = APIRouter()


_ARCHIVE_SUMMARY_PROMPT = (
    "你是 CrawAgent 爬虫工具的会话总结器。请根据下面的完整会话记录，"
    "用中文输出一段结构化的简短总结，包含：\n"
    "1. 目标是什么（用户想爬什么 / 解决什么问题）\n"
    "2. 最终结果（成功/失败/部分成功）\n"
    "3. 关键站点（遇到了哪些域名、有什么特征）\n"
    "4. 遇到的挑战（验证码、反爬、权限等）\n"
    "5. 值得记住的技巧（如果有的话）\n\n"
    "只输出总结正文，不要 JSON 格式标记、不要代码块、不要开场白。\n"
    "控制在 300 字以内。\n\n"
    "=== 会话记录 ===\n"
)


def _reconstruct_status(messages) -> str | None:
    """从检查点消息重建状态栏（服务重启 / 无内存指标时兜底）。

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
    else:
        parts.append("缓存命中 —%")
    parts.append(f"输入 {_fmt_tokens(input_tokens)} tok · 输出 {_fmt_tokens(output_tokens)} tok")
    return "  |  ".join(parts)


def _reconstruct_messages(messages, *, truncate=True):
    """从检查点消息列表重建结构化消息序列（history 与 export 共用）。

    truncate=True  时 tool_result 超过 10KB 截断（前端渲染用，完整内容在日志里）；
    truncate=False 时保留全文（导出用，用户要拿到完整对话记录）。
    """
    items: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            items.append({"role": "user", "content": content})
        elif isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                items.append({
                    "role": "tool_call",
                    "tool_call_id": tc["id"],
                    "name": tc["name"],
                    "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                })
            reasoning = (msg.additional_kwargs or {}).get("reasoning_content", "")
            plan = ""
            if not reasoning and msg.tool_calls and isinstance(msg.content, str):
                plan = msg.content.strip()
            if not reasoning and isinstance(msg.content, str) and msg.content.startswith("<think>"):
                end = msg.content.find("</think>")
                if end > 0:
                    reasoning = msg.content[7:end].strip()
            thinking_text = reasoning or plan
            if thinking_text:
                items.append({"role": "thinking", "content": thinking_text})
            if msg.content:
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                # dream 包裹时只展示 river 之后的 AI 回答正文
                if content.startswith("<think>"):
                    end_tag = content.find("</think>")
                    if end_tag > 0:
                        content = content[end_tag + 8:].lstrip()
                # 只有 tool_calls 时，AIMessage.content 已经作为 plan thinking 输出过，不再重复作为"空AI回答"
                if not (msg.tool_calls and content == plan):
                    items.append({"role": "ai", "content": content})
        elif isinstance(msg, ToolMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if truncate and len(content) > 10 * 1024:
                content = content[:10 * 1024] + f"\n... [tool_result 截断，原始 {len(content)} 字符]"
            items.append({
                "role": "tool_result",
                "tool_call_id": msg.tool_call_id,
                "content": content,
            })
    return items


@router.get("/api/history/{session_id}")
async def history(session_id: str) -> dict[str, Any]:
    """读取指定会话的历史消息与状态栏（供刷新页面/切换会话后恢复视图）"""
    def _load() -> dict[str, Any]:
        last_error = get_session_error(session_id)
        try:
            agent = get_agent()
        except Exception:
            return {"messages": [], "status": None, "last_error": last_error}
        config = {"configurable": {"thread_id": session_id}}
        try:
            state = agent.get_state(config)
        except Exception:
            return {"messages": [], "status": None, "last_error": last_error}
        messages = state.values.get("messages", [])
        items = _reconstruct_messages(messages, truncate=True)
        # 状态栏：优先用内存里的会话指标（含耗时/性能），
        # 内存没有就从磁盘恢复（server 重启后），都没有才从检查点重建
        metrics = get_metrics(session_id)
        status = metrics.status_line() if metrics is not None else _reconstruct_status(messages)
        return {"messages": items, "status": status, "last_error": last_error}

    return await asyncio.to_thread(_load)


def _export_json(session_id: str, title: str | None, ts: str, items: list[dict]) -> tuple[str, bytes, str]:
    """JSON 导出：完整数据 + 元数据，机器可读。"""
    data = {
        "session_id": session_id,
        "title": title,
        "exported_at": ts,
        "message_count": len(items),
        "messages": items,
    }
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    safe_name = (title or session_id)[:20]
    return "application/json; charset=utf-8", body, f"{safe_name}.json"


def _export_markdown(session_id: str, title: str | None, ts: str, items: list[dict]) -> tuple[str, bytes, str]:
    """Markdown 导出：人类可读的对话纪要。"""
    lines = [
        f"# 会话导出：{title or session_id}",
        "",
        f"- **会话 ID**: `{session_id}`",
        f"- **导出时间**: {ts}",
        f"- **消息条数**: {len(items)}",
        "",
        "---",
        "",
    ]
    for item in items:
        role = item.get("role")
        content = item.get("content", "")
        if role == "user":
            lines += ["## 👤 用户", "", content, ""]
        elif role == "ai":
            lines += ["## 🤖 AI", "", content, ""]
        elif role == "thinking":
            lines += ["<details><summary>💭 思考过程</summary>", "", content, "", "</details>", ""]
        elif role == "tool_call":
            name = item.get("name", "?")
            args = item.get("args", "{}")
            lines += [f"### 🔧 工具调用：`{name}`", "", "```json", args, "```", ""]
        elif role == "tool_result":
            tid = item.get("tool_call_id", "")
            lines += [f"<details><summary>📦 工具结果 ({tid})</summary>", "", "```", content, "```", "", "</details>", ""]
    body = "\n".join(lines).encode("utf-8")
    safe_name = (title or session_id)[:20]
    return "text/markdown; charset=utf-8", body, f"{safe_name}.md"


@router.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str, format: str = "json"):
    """导出会话完整对话记录（消息 + 工具调用 + 结果）为可下载文件。

    format=json: 机器可读的完整 JSON（含元数据）
    format=md:   人类可读的 Markdown 对话纪要
    tool_result 不截断——用户导出就是要拿完整记录。
    """
    def _build() -> tuple[str, bytes, str]:
        try:
            agent = get_agent()
            config = {"configurable": {"thread_id": session_id}}
            state = agent.get_state(config)
            messages = state.values.get("messages", [])
        except Exception:
            messages = []

        items = _reconstruct_messages(messages, truncate=False)

        # 查会话标题（有则用作文件名，无则用 session_id）
        title = None
        try:
            row = get_checkpointer().conn.execute(
                "SELECT title FROM session_titles WHERE thread_id = ?", (session_id,)
            ).fetchone()
            if row:
                title = row[0]
        except Exception:
            pass

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if format == "md":
            return _export_markdown(session_id, title, ts, items)
        return _export_json(session_id, title, ts, items)

    content_type, body, filename = await asyncio.to_thread(_build)
    filename_encoded = quote(filename, safe="")
    # filename= 用 ASCII 兜底（HTTP 头只能 latin-1），真实 CJK 文件名走 filename*=
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii")
    return Response(
        content=body,
        media_type=content_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_fallback}\"; "
                f"filename*=UTF-8''{filename_encoded}"
            ),
        },
    )


@router.get("/api/sessions")
async def sessions() -> dict[str, Any]:
    """列出 sessions.db 里的全部会话（含最新消息预览），供侧栏展示与切换

    性能优化：
      - 只拉最新 20 个会话（侧栏够用了；更多历史会话通过历史归档查看）
      - 跳过 checkpoint BLOB > 30MB 的会话（反序列化极慢，preview 直接留空）
    """
    def _load() -> list[dict[str, Any]]:
        try:
            checkpointer = get_checkpointer()
        except Exception:
            return []
        try:
            rows = checkpointer.conn.execute(
                "SELECT thread_id, MAX(rowid) AS latest, "
                "MAX(LENGTH(checkpoint)) AS ckpt_size FROM checkpoints "
                "GROUP BY thread_id ORDER BY latest DESC LIMIT 20"
            ).fetchall()
        except Exception:
            return []

        agent = None
        try:
            agent = get_agent()
        except Exception:
            pass

        # 一次性拉取全部会话标题（thread_id → title），侧栏直接查内存字典
        try:
            _titles = dict(checkpointer.conn.execute(
                "SELECT thread_id, title FROM session_titles"
            ).fetchall())
        except Exception:
            _titles = {}

        # 反序列化大 checkpoint 开销很大（100MB+ 需要几百 ms 到几秒），
        # 超过这个阈值就跳过 preview，避免侧栏加载卡成 PPT
        MAX_CKPT_SIZE_FOR_PREVIEW = 30 * 1024 * 1024  # 30 MB

        result: list[dict[str, Any]] = []
        for (thread_id, _latest, ckpt_size) in rows:
            preview = ""
            if agent is not None and (ckpt_size or 0) < MAX_CKPT_SIZE_FOR_PREVIEW:
                try:
                    state = agent.get_state({"configurable": {"thread_id": thread_id}})
                    for m in reversed(state.values.get("messages", [])):
                        if isinstance(m, (HumanMessage, AIMessage)) and m.content:
                            c = m.content if isinstance(m.content, str) else str(m.content)
                            preview = c.replace("\n", " ").strip()[:60]
                            break
                except Exception:
                    pass
            result.append({"id": thread_id, "title": _titles.get(thread_id), "preview": preview})
        return result

    return {"sessions": await asyncio.to_thread(_load)}


def _archive_session_sync(session_id: str) -> str | None:
    """删除前归档：规则筛选 → 写 placeholder（压缩 dump） → 返回路径。

    LLM 总结统一交给 _background_summarize 后台线程异步补全。
    Returns: 归档文件路径；None = 不值得保存或出错。
    """
    try:
        agent = get_agent()
        state = agent.get_state({"configurable": {"thread_id": session_id}})
        messages = state.values.get("messages", [])
        if not messages:
            return None
    except Exception:
        return None

    # ---- 规则筛选：不值得归档直接跳过 ----
    real_msgs = []
    ai_with_tools = 0
    last_human = ""
    dup_human = 0
    for m in messages:
        kind = getattr(m, "type", m.__class__.__name__)
        content = m.content if isinstance(m.content, str) else str(m.content) if m.content else ""
        if kind == "RemoveMessage" or not content.strip():
            continue
        if kind == "human":
            if content.strip() == last_human:
                dup_human += 1
                continue
            last_human = content.strip()
        if kind == "ai" and hasattr(m, "tool_calls") and m.tool_calls:
            ai_with_tools += 1
        real_msgs.append((kind, content, getattr(m, "tool_calls", None)))

    if len(real_msgs) < 5 or ai_with_tools == 0:
        return None

    # ---- 压缩 + 安全阈值 ----
    condensed = []
    for kind, content, tool_calls in real_msgs:
        if kind == "human":
            condensed.append(f"[USER] {content[:500]}")
        elif kind == "ai":
            if tool_calls:
                tool_names = [tc.get("name", "?") for tc in tool_calls]
                condensed.append(f"[AI] {content[:200]} (工具调用: {', '.join(tool_names)})")
            else:
                condensed.append(f"[AI] {content[:500]}")
        elif kind == "tool":
            condensed.append(f"[TOOL] {content[:300]}")

    # ---- 安全阈值：condensed 过长 → 均匀采样 ----
    MAX_CONDENSED_CHARS = 15000
    condensed_text = "\n".join(condensed)
    if len(condensed_text) > MAX_CONDENSED_CHARS:
        target_count = MAX_CONDENSED_CHARS // 200
        step = max(1, len(condensed) // target_count)
        condensed = condensed[::step][:target_count]

    # ---- 写 placeholder（只有 compressed_dump，LLM 总结由后台补）----
    logs_dir = get_settings().log_dir
    logs_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = logs_dir / f"{session_id}_{ts}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({
            "session_id": session_id,
            "archived_at": ts,
            "_compressed_dump": condensed,
            "original_msg_count": len(messages),
            "deduped": dup_human,
        }, f, ensure_ascii=False, indent=2)
    return str(filepath)


def _delete_session_sync(session_id: str, *, do_archive: bool = True) -> dict[str, Any]:
    """同步删除单个会话。

    do_archive=True  → 先归档 placeholder 到 logs/ → 再删除检查点（"归档"操作）
    do_archive=False → 直接删除检查点，不保留任何数据（"彻底删除"操作）

    LLM 总结由调用方统一触发后台线程补全（仅归档路径触发）。
    """
    # 1. 标记正在运行的任务为已取消
    active = _active_turns.pop(session_id, None)
    if active:
        active["cancelled"] = True
        active["ws_id"] = 0
        try:
            fut = active.get("future")
            if fut and not fut.done():
                # ponytail: 对已 run_in_executor 的 future，cancel() 只能取消尚未排队的；
                # 正在跑的线程不可中断 → 真正生效的是上面那行 cancelled 标志位
                # （_run_turn 循环每次迭代检查它后退出）。这行只是尽力而为的兜底。
                fut.cancel()
        except Exception:
            pass

    # 2. 归档 placeholder（仅 do_archive=True 时；只写 compressed_dump，LLM 总结等后台线程）
    archive_path = _archive_session_sync(session_id) if do_archive else None

    # 3. 删除检查点
    checkpointer = get_checkpointer()
    for table in ("checkpoints", "writes"):
        checkpointer.conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (session_id,))
    # 标题与失败记录一并清掉：会话没了，残留数据就是垃圾
    for table in ("session_titles", "session_errors"):
        try:
            checkpointer.conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (session_id,))
        except Exception:
            pass  # 表尚未创建（老库）等情况，不阻断删除
    checkpointer.conn.commit()
    _metrics.pop(session_id, None)
    _session_locks.pop(session_id, None)

    return {"id": session_id, "archived": archive_path is not None, "archive_path": archive_path}


@router.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """重命名会话：upsert session_titles 表。空标题 = 删除自定义名（恢复 ID 显示）。"""
    title = (body.get("title") or "").strip()
    def _upsert() -> None:
        conn = get_checkpointer().conn
        if title:
            conn.execute(
                "INSERT INTO session_titles (thread_id, title) VALUES (?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET title = excluded.title",
                (session_id, title),
            )
        else:
            conn.execute("DELETE FROM session_titles WHERE thread_id = ?", (session_id,))
        conn.commit()
    try:
        await asyncio.to_thread(_upsert)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": session_id, "title": title or None}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    """彻底删除一个会话：不归档，直接从检查点数据库移除，不可恢复。"""
    try:
        result = await asyncio.to_thread(_delete_session_sync, session_id, do_archive=False)
    except Exception:
        return {"ok": False}
    return {"ok": True, **result}


@router.post("/api/sessions/{session_id}/archive")
async def archive_session(session_id: str) -> dict[str, Any]:
    """归档一个会话：导出到 logs/ 目录（含 LLM 总结），然后从会话列表移除。"""
    try:
        result = await asyncio.to_thread(_delete_session_sync, session_id, do_archive=True)
    except Exception:
        return {"ok": False}
    # 触发后台总结（如果归档成功）
    if result.get("archive_path"):
        threading.Thread(
            target=_background_summarize,
            args=([result["archive_path"]],),
            daemon=True,
        ).start()
    return {"ok": True, **result}


@router.post("/api/sessions/bulk-delete")
async def bulk_delete_sessions(body: dict[str, list[str]]) -> dict[str, Any]:
    """批量删除会话：彻底删除，不归档。正在运行的会话会被跳过。"""
    ids = [id for id in (body.get("ids") or []) if id and isinstance(id, str)]
    if not ids:
        return {"ok": False, "deleted": 0, "message": "没有要删除的会话"}

    # 跳过正在运行的会话
    safe_ids = [sid for sid in ids if sid not in _active_turns]
    skipped = len(ids) - len(safe_ids)

    try:
        def _bulk_delete() -> int:
            for sid in safe_ids:
                _delete_session_sync(sid, do_archive=False)
            return len(safe_ids)
        deleted = await asyncio.to_thread(_bulk_delete)
    except Exception:
        return {"ok": False, "deleted": 0}

    return {"ok": True, "deleted": deleted, "skipped_active": skipped}


def _background_summarize(placeholder_paths: list[str]) -> None:
    """后台线程：逐个读取 placeholder 归档文件，调 LLM 生成总结并覆盖。

    placeholder 文件里有 _compressed_dump（原始 condensed 消息），
    直接读它喂给 LLM 比从 checkpointer 重取更轻。
    """
    try:
        from crawagent.llm.model import get_llm
        llm = get_llm(thinking=False, max_tokens=1024)
    except Exception as e:
        print(f"[archive] 后台总结：LLM 初始化失败，跳过: {e}")
        return

    for path in placeholder_paths:
        try:
            p = Path(path)
            if not p.exists():
                continue
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            condensed = data.get("_compressed_dump")
            if not condensed:
                continue  # 规则筛选阶段就跳过了，没写 placeholder
            condensed_text = "\n".join(condensed)

            # 同样的安全阈值
            MAX_CONDENSED_CHARS = 15000
            if len(condensed_text) > MAX_CONDENSED_CHARS:
                target_count = MAX_CONDENSED_CHARS // 200
                step = max(1, len(condensed) // target_count)
                condensed_text = "\n".join(condensed[::step][:target_count])

            resp = llm.invoke(_ARCHIVE_SUMMARY_PROMPT + condensed_text)
            summary = resp.content.strip() if isinstance(resp.content, str) else str(resp.content)
            if summary:
                data["summary"] = summary
                data.pop("_compressed_dump", None)  # 删掉冗余的原始 dump
                data["llm_summarized_at"] = datetime.now().strftime("%Y%m%d_%H%M%S")
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[archive] 后台总结失败 {path}: {e}")


@router.get("/api/sessions/{session_id}/context")
async def get_session_context(session_id: str) -> dict[str, Any]:
    """返回某个会话的上下文用量（供前端余量环显示）。

    limit 来自 middleware 最后一次裁剪水位（已按当前模型 context_window × 70% 动态算好）。
    会话还没开始对话时，兜底 32K。

    server 重启 / 该会话在本进程还没对话过时，内存缓存为空，会从检查点
    持久化的消息重建用量（口径与 middleware 一致），避免余量环显示 0%。
    """
    from crawagent.graph.middleware import get_context_info as _get_ctx, reconstruct_context

    ctx = _get_ctx(session_id)
    if not ctx:
        # 内存缓存空 → 从检查点重建（刷新页面 / 切换会话时余量环立即有数据）
        try:
            agent = get_agent()
            from crawagent.llm.registry import resolve_model
            model_name = resolve_model()[0]
            ctx = reconstruct_context(session_id, agent, model_name) or {}
        except Exception:
            ctx = {}
    used = int(ctx.get("used") or 0)
    limit = int(ctx.get("limit") or 32000)
    pct = round(used / limit * 100, 1) if limit > 0 else 0.0

    # 轮数：优先用 metrics（内存 → 磁盘兜底），没有就用检查点重建出来的
    metrics = get_metrics(session_id)
    turn_count = metrics.turn_count if metrics else int(ctx.get("turn_count") or 0)

    return {
        "used": used,
        "limit": limit,
        "percentage": pct,
        "turn_count": turn_count,
    }


@router.get("/api/sessions/{session_id}/metrics")
async def get_session_metrics(session_id: str) -> dict[str, Any]:
    """返回某个会话的累积运行指标（token 总量、LLM 耗时、缓存命中率等）。"""
    metrics = get_metrics(session_id)
    if metrics is None:
        return {"ok": False, "error": "会话不存在或还没开始对话"}
    return {"ok": True, **metrics.to_dict()}
