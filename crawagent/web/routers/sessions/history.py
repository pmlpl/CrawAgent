"""会话历史 API：checkpoint 重放 + 状态重建。

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
                "media": extract_media(content),
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


