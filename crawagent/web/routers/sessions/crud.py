"""会话 CRUD API：导出 / 列表 / 删除 / 批量删 / 上下文。

从 routers/sessions.py 拆出（027），router 独立但 URL 路径不变。
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Response
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from crawagent.config.settings import get_settings
from crawagent.observability.metrics import _fmt_tokens
from crawagent.web.media import extract_media
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
from crawagent.storage.meta_store import get_meta_conn
from crawagent.storage.checkpoint_view import get_checkpoint_view

router = APIRouter()

from .archive import _archive_session_sync, _background_summarize
from .history import _reconstruct_messages

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
            row = get_meta_conn().execute(
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
    """列出全部会话（含最新消息预览），供侧栏展示与切换

    后端无关：通过 get_checkpoint_view().list_thread_ids() 抽象，
    sqlite 查 checkpoints 表，redis 走 SMEMBERS 索引。
    """
    def _load() -> list[dict[str, Any]]:
        try:
            view = get_checkpoint_view()
        except Exception:
            return []
        try:
            rows = view.list_thread_ids(limit=20)
        except Exception:
            return []

        agent = None
        try:
            agent = get_agent()
        except Exception:
            pass

        # 一次性拉取全部会话标题（thread_id → title），侧栏直接查内存字典
        # session_titles 表在 meta.db，与 checkpointer 后端解耦
        try:
            _titles = dict(get_meta_conn().execute(
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

    # 3. 删除检查点（checkpoints/writes 表仅在 sqlite 后端存在；
    #    redis 后端无 .conn 属性，try/except 兜底跳过）
    checkpointer = get_checkpointer()
    conn = getattr(checkpointer, "conn", None)
    if conn is not None:
        for table in ("checkpoints", "writes"):
            try:
                conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (session_id,))
            except Exception:
                pass
        try:
            conn.commit()
        except Exception:
            pass
    # 标题与失败记录在 meta.db（独立连接，与 checkpointer 后端无关）
    meta = get_meta_conn()
    for table in ("session_titles", "session_errors"):
        try:
            meta.execute(f"DELETE FROM {table} WHERE thread_id = ?", (session_id,))
        except Exception:
            pass  # 表尚未创建（老库）等情况，不阻断删除
    meta.commit()
    _metrics.pop(session_id, None)
    _session_locks.pop(session_id, None)

    return {"id": session_id, "archived": archive_path is not None, "archive_path": archive_path}


@router.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """重命名会话：upsert session_titles 表（meta.db）。空标题 = 删除自定义名。"""
    title = (body.get("title") or "").strip()
    def _upsert() -> None:
        conn = get_meta_conn()
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




