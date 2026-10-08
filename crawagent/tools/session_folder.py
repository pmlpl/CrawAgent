"""会话文件夹 — data/sessions/<sid>/ 会话内容主盘 + 记忆/档案的物理落点。

存储裁决（ADR-0009 修正 ADR-0008）：
    - 会话内容主盘 = conversation.md：每轮追加（用户消息 + 工具调用 + AI 回复），
      人可读、可备份、删目录即清主盘。046 起会话创建即初始化空头。
    - 运行时状态索引 = 数据库（checkpointer state）：保留最近 N 轮用于断点续跑，
      老内容已在 conversation.md 兜底，定期清理 + VACUUM 收缩。
    - 长期记忆 = 数据库（008 FTS5 knowledge 库，save_record 通道），
      提炼产物入库后 search_knowledge 可检索；archive/*.md 是归档快照副本。
一句话：文件夹管活会话的每轮内容，数据库管运行时状态 + 筛选的档案/元信息索引。

路径：data/sessions/<sid>/（sid = ctx_session_id 全值，非 014 的 sid[:8]
截断——本层是系统资产必须全局唯一）。046 起会话创建即建（chat_ws 连接时
显式调 ensure_session_folder + init_conversation_md），不再惰性等 save_record。
结构：<sid>/conversation.md（主盘，每轮追加）、<sid>/meta.json（可读副本）、
<sid>/archive/<主题>.md（档案快照）、<sid>/memory/（记忆快照，预留）。
每个 md 头部写元信息，保证脱离库也能读懂。

归档：提炼入库成功后整个 <sid>/ 移入 data/sessions/_archived/<sid>/
（仅移动，永不物理删除——删除永远由用户手动执行）。039 蒸馏端点对活跃
会话（conversation.md 最近写入 < 24h）拒绝，避免移走活文件夹破坏后续轮末写。
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
    """创建会话文件夹（含 archive/ 子目录），返回根路径。

    039 起惰性创建（首次写档案快照时才 mkdir）；046 起会话创建即调
    （chat_ws 连接时显式调用），不再等 save_record 触发。mkdir exist_ok
    天然幂等，重连/重复调安全。
    """
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


# ---- 046 会话内容主盘 ----
# conversation.md 是会话内容主盘：会话创建即初始化空头，每轮完成追加该轮
# （用户消息 + 工具调用 + AI 回复）。O(1) 追加不重写全文件；文件夹被 039
# archive 移走时静默 skip（不重建——避免与归档语义冲突，checkpointer 兜底）。
# meta.json 是可读副本：创建时快照 title/work_dir，轮末只刷 last_active；
# meta_store 仍是唯一真源（不双向同步，避免双写一致性）。

CONVERSATION_MD_NAME = "conversation.md"
META_JSON_NAME = "meta.json"


def conversation_md_path(sid: str, settings=None) -> Path:
    """conversation.md 路径（不创建）。"""
    return session_folder_path(sid, settings) / CONVERSATION_MD_NAME


def init_conversation_md(sid: str, settings=None) -> Path:
    """初始化 conversation.md（写头：会话 id + 创建时间 + 空正文待追加）。

    幂等：已存在则原样返回，不重写头（重连安全）。文件夹不存在先 ensure。
    创建同时写 meta.json 快照（title/work_dir 从 meta_store 读，last_active=创建时间）。
    """
    root = ensure_session_folder(sid, settings)
    path = root / CONVERSATION_MD_NAME
    if path.exists():
        return path
    now = datetime.datetime.now().isoformat(timespec="seconds")
    header = (
        f"# 会话 {sid}\n\n"
        f"> 创建时间: {now}\n"
        f"> 会话 ID: {sid}\n"
        f"> 说明: 每轮对话追加于下方；数据库仅保留运行时状态索引用于断点续跑。\n"
        f">       备份本文件即备份会话内容主盘。\n\n---\n\n"
    )
    path.write_text(header, encoding="utf-8")
    _write_meta_json(sid, now, settings)
    return path


def _write_meta_json(sid: str, created_at: str, settings=None) -> None:
    """写 meta.json 可读副本（创建时快照 title/work_dir + 时间戳）。

    meta_store 仍是唯一真源——这里只作人可读副本，不双向同步。title/work_dir
    快照于创建时刻，后续用户改名/换绑不会回写本文件（避免双写一致性）。
    """
    import json

    from crawagent.storage.meta_store import get_session_title, get_work_dir

    root = session_folder_path(sid, settings)
    title = ""
    work_dir = ""
    try:
        title = get_session_title(sid, settings) or ""
    except Exception:
        pass
    try:
        work_dir = get_work_dir(sid, settings) or ""
    except Exception:
        pass
    data = {
        "sid": sid,
        "title": title,
        "work_dir": work_dir,
        "created_at": created_at,
        "last_active": created_at,
    }
    (root / META_JSON_NAME).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _touch_meta_active(sid: str, settings=None) -> None:
    """轮末更新 meta.json 的 last_active（只更这一个字段，不重写快照）。"""
    import json

    path = session_folder_path(sid, settings) / META_JSON_NAME
    if not path.exists():
        return  # meta.json 未建（init 没调过）→ 不补建，避免与归档语义冲突
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    data["last_active"] = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def write_turn_snapshot(sid: str, turn: dict, settings=None) -> Path | None:
    """追加一轮到 conversation.md（O(1) 追加）。

    turn = {
        "turn_no": int,
        "user_text": str,
        "tool_calls": [{"name","args","result"}],  # 可空
        "ai_text": str,  # 可空
    }

    文件夹不存在静默 skip 返回 None（不重建——与 039 archive 移夹语义一致；
    checkpointer 兜底，conversation.md 出现断带但不丢数据）。追加后刷 meta.json
    的 last_active。失败抛异常由调用方独立 try/except 兜底（不阻断主流程）。
    """
    root = session_folder_path(sid, settings)
    if not root.is_dir():
        return None  # 文件夹被移走/删除 → 静默 skip
    path = root / CONVERSATION_MD_NAME
    if not path.exists():
        # conversation.md 不存在（init 没调过或被删）→ 兜底初始化
        init_conversation_md(sid, settings)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    turn_no = turn.get("turn_no") or 0
    user_text = (turn.get("user_text") or "").strip()
    tool_calls = turn.get("tool_calls") or []
    ai_text = (turn.get("ai_text") or "").strip()

    parts: list[str] = [f"## 轮次 {turn_no} — {ts}\n\n"]
    if user_text:
        parts.append(f"### 👤 用户\n\n{user_text}\n\n")
    if tool_calls:
        parts.append(f"### 🔧 工具调用（{len(tool_calls)} 次）\n\n")
        for tc in tool_calls:
            name = tc.get("name") or "?"
            args = (tc.get("args") or "")[:200]
            result = (tc.get("result") or "")[:300]
            parts.append(f"- **{name}**\n")
            if args:
                parts.append(f"  - 参数: `{args}`\n")
            if result:
                parts.append(f"  - 结果: {result}\n")
        parts.append("\n")
    if ai_text:
        parts.append(f"### 🤖 AI\n\n{ai_text}\n\n")
    parts.append("---\n\n")

    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(parts))
    _touch_meta_active(sid, settings)
    return path


def conversation_md_last_active(sid: str, settings=None) -> float | None:
    """conversation.md 的 mtime（用于 039 蒸馏守卫判活跃态）；不存在返回 None。

    046 每轮写 conversation.md，mtime 即「最近一轮完成时间」，比 meta_store
    的 work_dir_ts（仅换绑时刷）更准。文件夹被移走/未建时返回 None。
    """
    path = conversation_md_path(sid, settings)
    try:
        return path.stat().st_mtime if path.exists() else None
    except OSError:
        return None
