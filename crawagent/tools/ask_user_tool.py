"""ask_user — AI 向用户发起结构化选择题（通用 human-in-the-loop）。

机制：工具在轮次线程内阻塞等待用户点击；问题经 progress.emit_turn_event 推进
轮次事件日志（type=ask，重连重放后按钮仍可见可点）；用户点击经 WebSocket
（server.chat_ws → resolve_ask）写入答案并唤醒。超时/拒绝都显式返回，
由 AI 自行决定后续；工具调用本身对 AI 是普通的阻塞工具调用。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from langchain_core.tools import tool

from crawagent.tools.progress import emit_turn_event

_LOCK = threading.Lock()
_SEQ = 0
# ask_id → {"event": threading.Event, "value": str | None}
_PENDING: dict[str, dict[str, Any]] = {}

# 等待上限（秒）：防止轮次被一个被遗忘的问题挂死
_MAX_TIMEOUT = 1800
_MIN_TIMEOUT = 1


def resolve_ask(ask_id: str, value: str) -> bool:
    """用户在 UI 上点了选项：写入答案并唤醒阻塞中的 ask_user。

    重复回答 / 未知 ask_id（已超时清理）返回 False，天然幂等。
    """
    with _LOCK:
        entry = _PENDING.get(ask_id)
        if entry is None or entry["value"] is not None:
            return False
        entry["value"] = value
        entry["event"].set()
    return True


def pending_ask_ids() -> list[str]:
    with _LOCK:
        return list(_PENDING.keys())


@tool
def ask_user(question: str, options: list[str], timeout: int = 300) -> str:
    """向用户发起选择题并等待点击（通用的人机确认机制）。

    适用场景：需要用户授权或选择时——拉起 MCP 服务（anything-analyzer）、
    大体积批量下载、覆盖/删除文件、需要用户提供登录 Cookie 等。
    用户点击选项后，你将收到所选选项的原文，据此继续。

    注意：只用它问"需要用户决定"的问题；普通澄清直接在回复里问即可。
    超时视为用户暂缓——不要原样重复提问。

    参数：
        question: 一句话说清要决定什么（含上下文与后果）。
        options: 2-6 个短选项文案，如 ["打开 anything-analyzer", "暂不打开"]。
        timeout: 最长等待秒数（默认 300，上限 1800）。

    返回：
        用户点击的选项原文；超时/关闭返回提示语。
    """
    global _SEQ
    q = (question or "").strip()
    opts = [str(o).strip() for o in (options or []) if str(o).strip()]
    if not q:
        return "ask_user failed: question 不能为空"
    if not (2 <= len(opts) <= 6):
        return "ask_user failed: options 需要 2-6 个非空选项"
    try:
        wait_s = max(_MIN_TIMEOUT, min(int(timeout), _MAX_TIMEOUT))
    except (TypeError, ValueError):
        wait_s = 300

    with _LOCK:
        _SEQ += 1
        ask_id = f"ask_{int(time.time() * 1000)}_{_SEQ}"
        entry: dict[str, Any] = {"event": threading.Event(), "value": None}
        _PENDING[ask_id] = entry

    emit_turn_event({"type": "ask", "ask_id": ask_id, "question": q, "options": opts})
    entry["event"].wait(timeout=wait_s)
    with _LOCK:
        _PENDING.pop(ask_id, None)
        value = entry["value"]

    if value is not None:
        emit_turn_event({"type": "ask_answered", "ask_id": ask_id, "value": str(value)})
        return str(value)
    emit_turn_event({"type": "ask_answered", "ask_id": ask_id, "value": None})
    return (
        f"⏳ 用户在 {wait_s} 秒内未选择（视为暂缓）。"
        "不要原样重复提问；先继续做无需授权的部分，或直接给出你的建议。"
    )
