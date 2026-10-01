"""会话产物子目录 — 从 ctx_session_id 解析当前会话的产物子目录名。

变更 014（会话级产物目录）：save_to_file / run_custom_script 的产物默认落
output/<会话名>/，会话间隔离。会话名取 meta.db 里的会话标题（可读性好），
无标题退回 session_id 前 8 位；两者都空（CLI/分布式 worker/直调工具）返回
空串，行为与改动前一致（落 output/ 根）。
"""
from __future__ import annotations

import re
from pathlib import Path

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


def current_work_dir() -> str:
    """当前会话的工作文件夹（变更 034）；未配置 / 无会话 / 任何异常返回空串。

    非空时它是该会话全部产物（文本/媒体/脚本）的落点基准，也是路径安全
    校验的边界——用户选的文件夹就是边界，AI 不得写出到它之外。
    防御策略与 session_subdir() 同款：落点解析失败不应杀死工具调用。
    """
    try:
        from crawagent.graph.agent import ctx_session_id
        sid = ctx_session_id.get("") or ""
    except Exception:
        return ""
    if not sid:
        return ""
    try:
        from crawagent.storage.meta_store import get_work_dir
        return get_work_dir(sid) or ""
    except Exception:
        return ""


def work_dir_unavailable() -> str:
    """当前会话 work_dir 已绑定但目录在磁盘上不存在 → 返回人话错误串；否则空串。

    变更 038 路径失效防护：save_to_file / download_social_media /
    download_images / run_custom_script 四个写产物工具落盘前统一调用，
    非空即原样返回错误（不 mkdir、不写入——用户删文件夹表达的是
    「不想要该路径」，工具静默重建等于违背意图）。检查时机 = 工具调用时
    现查 Path.exists() 不缓存，文件夹建回来无需重启自动恢复。

    未绑定 work_dir 时恒空串——output/downloads 默认落点由项目自身保证
    存在，不属本变更防护范围。防御策略同 session_subdir()：解析失败
    不杀死工具调用，退空串即放行。
    """
    try:
        work_dir = current_work_dir()
        if not work_dir:
            return ""
        if Path(work_dir).exists():
            return ""
        return (
            f"项目文件夹 {work_dir} 已不存在（可能被删除或移动）。"
            "请恢复该文件夹，或新建会话重新选择项目。本次未写入任何文件。"
        )
    except Exception:
        return ""
