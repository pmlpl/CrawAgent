"""会话产物子目录 — 从 ctx_session_id 解析当前会话的产物子目录名。

变更 014（会话级产物目录）：save_to_file / run_custom_script 的产物默认落
output/<会话名>/，会话间隔离。会话名取 meta.db 里的会话标题（可读性好），
无标题退回 session_id 前 8 位；两者都空（CLI/分布式 worker/直调工具）返回
空串，行为与改动前一致（落 output/ 根）。
"""
from __future__ import annotations

import re

# Windows 目录名非法字符（含控制符）；<> 等由调用方数据触发，需清洗为 _
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_dirname(name: str, max_len: int = 40) -> str:
    """把会话标题清洗成安全目录名：非法字符转 _、去首尾空白与点、限长。"""
    cleaned = _INVALID_CHARS.sub("_", (name or "")).strip(". ")
    return cleaned[:max_len].strip(". ")


def session_subdir() -> str:
    """当前会话的产物子目录名；无会话上下文时返回空串（落 output/ 根兜底）。

    刻意吞掉所有异常（import 失败 / DB 打不开）——产物路径解析是写文件的
    前置步骤，这里失败不应杀死工具调用，退回空串即改前行为。
    """
    try:
        from crawagent.graph.agent import ctx_session_id
        sid = ctx_session_id.get("") or ""
    except Exception:
        return ""
    if not sid:
        return ""
    try:
        from crawagent.storage.meta_store import get_session_title
        title = get_session_title(sid)
    except Exception:
        title = ""
    name = sanitize_dirname(title)
    if not name:
        name = sid[:8]
    return name
