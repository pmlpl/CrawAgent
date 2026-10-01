"""会话文件夹 — data/sessions/<sid>/ 会话级记忆/档案的物理落点（变更 039）。

混合存储裁决（ADR-0004）：
    - 会话热数据 = 文件夹：人可在资源管理器直接看、可整个拷走备份；
      agent 用现成 read_file/save_to_file 读写（零新工具）；清理 = 删目录。
    - 长期记忆 = 数据库：沿用 008 FTS5 knowledge 库（save_record 通道），
      提炼产物入库后 search_knowledge 可检索。
一句话：文件夹管活着的会话，数据库管死去会话留下的精华。

路径：data/sessions/<sid>/（sid = ctx_session_id 全值，非 014 的 sid[:8]
截断——本层是系统资产必须全局唯一）。惰性创建：首次写档案快照时 mkdir，
不搞会话启动即建空目录。结构：<sid>/archive/<主题>.md（档案快照）、
<sid>/memory/（记忆快照，预留）。每个 md 头部写元信息（会话 id、归档
时间、来源），保证脱离库也能读懂。

归档：提炼入库成功后整个 <sid>/ 移入 data/sessions/_archived/<sid>/
（仅移动，永不物理删除——删除永远由用户手动执行）。
"""
from __future__ import annotations

import datetime
import shutil
import time
from pathlib import Path

from crawagent.config.settings import get_settings
from crawagent.tools.session_dir import sanitize_dirname

# 归档目录名（_ 前缀避开 sid 命名空间）
ARCHIVED_DIRNAME = "_archived"

# 超过该天数不活跃的会话文件夹列入「可清理」清单（初值 180 天 = 半年）
STALE_DAYS = 180


def sessions_root(settings=None) -> Path:
    """data/sessions/ 根目录。"""
    if settings is None:
        settings = get_settings()
    return Path(settings.project_root) / "data" / "sessions"


def session_folder_path(sid: str, settings=None) -> Path:
    """会话文件夹路径（不创建）。

    sid 防御性清洗（id 本身安全，沿用 014 sanitize_dirname 思路）——
    非法字符（含路径分隔符）替换为 _，路径穿越自然失效。
    """
    clean = sanitize_dirname(sid or "", max_len=120)
    return sessions_root(settings) / (clean or "unknown")


def ensure_session_folder(sid: str, settings=None) -> Path:
    """惰性创建会话文件夹（含 archive/ 子目录），返回根路径。"""
    root = session_folder_path(sid, settings)
    (root / "archive").mkdir(parents=True, exist_ok=True)
    return root


def write_archive_snapshot(
    sid: str, topic: str, content: str, source_url: str = "", settings=None
) -> Path:
    """档案快照落盘：<sid>/archive/<清洗主题>.md，重名序号 -1/-2（与上传同款）。

    md 头部写元信息（会话 id、归档时间、来源），保证脱离库也能读懂；
    正文为 save_record 入库的同一份内容（双写，库为主盘为副本）。
    """
    root = ensure_session_folder(sid, settings)
    safe_topic = sanitize_dirname(topic or "", max_len=60) or "未命名档案"
    archive_dir = root / "archive"
    path = archive_dir / f"{safe_topic}.md"
    n = 1
    while path.exists():
        path = archive_dir / f"{safe_topic}-{n}.md"
        n += 1
    header = (
        f"# {topic or safe_topic}\n\n"
        f"> 会话: {sid}\n"
        f"> 归档时间: {datetime.datetime.now().isoformat(timespec='seconds')}\n"
        f"> 来源: {source_url or '会话档案'}\n\n"
    )
    path.write_text(header + (content or ""), encoding="utf-8")
    return path


def iter_session_folders(settings=None) -> list[Path]:
    """data/sessions/ 下所有会话文件夹（跳过 _archived/ 与非目录项）。"""
    root = sessions_root(settings)
    if not root.is_dir():
        return []
    out = []
    for p in root.iterdir():
        if p.is_dir() and p.name != ARCHIVED_DIRNAME:
            out.append(p)
    return out


def folder_last_active(p: Path) -> float:
    """最后活跃时间：夹内全部文件（递归）的最新 mtime；空夹用夹自身 mtime。

    选 mtime 方案（另一案是 meta_store 加 last_active_ts 列）：会话文件夹
    的活跃 = 快照写入（039 本层唯一写入口），文件 mtime 恰好就是它的准确
    语义，且零 schema 变更、统计函数离线可测。读文件不更新 mtime 不影响
    ——「活跃」只看写入。
    """
    latest = 0.0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                latest = max(latest, f.stat().st_mtime)
        except OSError:
            continue
    if not latest:
        try:
            latest = p.stat().st_mtime
        except OSError:
            latest = time.time()
    return latest


def folder_size(p: Path) -> int:
    """文件夹总字节数（递归）。"""
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def folder_md_texts(p: Path) -> list[tuple[str, str]]:
    """读文件夹内全部 md（递归），返回 [(相对路径, 正文)]，按相对路径排序。

    蒸馏输入源；读不到/解码失败的文件跳过（errors=replace 兜底防崩）。
    """
    out = []
    for f in sorted(p.rglob("*.md")):
        try:
            out.append((f.relative_to(p).as_posix(), f.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return out


def archive_session_folder(sid: str, settings=None) -> Path:
    """把 <sid>/ 移入 _archived/<sid>/（提炼入库全部成功后调用）。

    目标同名（恢复过文件夹后二次提炼）时加序号 -1/-2，绝不覆盖。
    仅移动，永不删除。
    """
    src = session_folder_path(sid, settings)
    dst_base = sessions_root(settings) / ARCHIVED_DIRNAME / src.name
    dst = dst_base
    n = 1
    while dst.exists():
        dst = dst_base.parent / f"{dst_base.name}-{n}"
        n += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst
