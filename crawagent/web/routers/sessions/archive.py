"""会话归档 API：归档 + 后台摘要 + 指标。

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




@router.get("/api/sessions/{session_id}/metrics")
async def get_session_metrics(session_id: str) -> dict[str, Any]:
    """返回某个会话的累积运行指标（token 总量、LLM 耗时、缓存命中率等）。"""
    metrics = get_metrics(session_id)
    if metrics is None:
        return {"ok": False, "error": "会话不存在或还没开始对话"}
    return {"ok": True, **metrics.to_dict()}


