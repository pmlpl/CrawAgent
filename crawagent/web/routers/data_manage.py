"""会话记忆数据管理 API（变更 039 —— 会话文件夹统计 + 长期记忆提炼归档）。

GET /api/data/sessions/overview：
    统计 data/sessions/ 下全部会话文件夹（标题反查 + 大小 + 最后活跃 +
    是否超期）。只读，不动任何文件；设置页「会话记忆管理」卡片的数据源。

POST /api/data/sessions/{sid}/distill：
    提炼并归档单个会话（用户在卡片上显式触发，批量动文件硬规则由前端
    两步确认兜住）：
        1. 读该会话文件夹全部 md（长内容按 3 万字符分段，最多 6 段）；
        2. LLM 蒸馏成知识条目（每段 3-8 条：主题 + 要点）；
        3. 单事务逐条入库（platform=长期记忆、session_id=来源会话，
           与 save_record 同表同 FTS 索引，search_knowledge 直接可检索）；
        4. 全部入库成功后整个文件夹移入 data/sessions/_archived/<sid>/
           （仅移动，永不物理删除）。
    任何一步失败（LLM 错误/超时/解析失败/入库失败）→ 文件夹保持原样，
    宁可留着下次再提炼，不可丢。

蒸馏调用走现有 LLM 通道（get_llm，thinking 关闭省预算）；路由用 sync def
让 FastAPI 自动进线程池，长蒸馏不堵事件循环。
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import time

from fastapi import APIRouter

from crawagent.storage.meta_store import get_session_title
from crawagent.tools import session_folder as sf

router = APIRouter()

# 蒸馏输入：单段字符数 / 总上限 / 最多段数（防 token 爆炸失控）
_CHUNK_CHARS = 30000
_MAX_TOTAL_CHARS = 180000
_MAX_CHUNKS = 6

# 046 蒸馏活跃态守卫：最近有对话的会话拒绝蒸馏——防 archive_session_folder
# 移走活文件夹破坏后续轮末写 conversation.md。
_DISTILL_ACTIVE_SECS = 24 * 3600

_DISTILL_SYSTEM = (
    "你是知识归档员。把一个爬虫会话留下的档案/记忆笔记蒸馏成可长期检索的"
    "知识条目。只输出 JSON，不要任何解释文字。"
)

_DISTILL_PROMPT = (
    "下面是一个爬虫会话的档案笔记（可能有多个文件，用 ===== 文件名 ===== 分隔）。\n"
    "请蒸馏出 3-8 条知识条目，每条包含 topic（主题，不超过 40 字）和 points"
    "（要点，字符串数组，3-6 条，保留具体事实：站点/接口/参数/路径/结论/坑）。\n"
    "忽略寒暄、过程性叙述和重复内容；只保留将来仍有复用价值的信息。\n"
    '输出格式：[{{"topic": "...", "points": ["...", "..."]}}]\n\n'
    "{content}"
)


def _overview_rows() -> list[dict]:
    """统计全部会话文件夹（标题反查 + 大小 + 最后活跃 + 是否超期）。"""
    now = time.time()
    threshold = sf.STALE_DAYS * 86400
    rows = []
    for p in sf.iter_session_folders():
        last = sf.folder_last_active(p)
        rows.append({
            "sid": p.name,
            "title": get_session_title(p.name) or "",
            "size_bytes": sf.folder_size(p),
            "last_active": datetime.datetime.fromtimestamp(last).isoformat(timespec="seconds"),
            "stale": (now - last) > threshold,
        })
    rows.sort(key=lambda r: r["last_active"])
    return rows


@router.get("/api/data/sessions/overview")
async def data_overview() -> dict:
    """会话文件夹统计（只读）：超期清单 + 归档计数，设置页卡片数据源。"""
    archived_root = sf.sessions_root() / sf.ARCHIVED_DIRNAME
    archived_count = (
        sum(1 for p in archived_root.iterdir() if p.is_dir())
        if archived_root.is_dir() else 0
    )
    return {
        "ok": True,
        "stale_days": sf.STALE_DAYS,
        "sessions": _overview_rows(),
        "archived_count": archived_count,
    }


def _parse_entries(raw: str) -> list[dict]:
    """解析 LLM 返回的 JSON 数组 [{topic, points}]；容错剥代码围栏。

    解析失败抛 ValueError（上层报「提炼失败」，文件夹保持原样）。
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("模型返回为空")
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("返回内容里找不到 JSON 数组")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("返回不是数组")
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or item.get("主题") or "").strip()
        points = item.get("points") or item.get("要点") or ""
        if isinstance(points, list):
            points = "\n".join(f"- {str(p).strip()}" for p in points if str(p).strip())
        points = str(points).strip()
        if topic and points:
            out.append({"topic": topic[:80], "content": points})
    return out


def _distill_entries(text: str) -> list[dict]:
    """内容分段调 LLM 蒸馏，返回去重后的 [{topic, content}]。

    分段：每段 _CHUNK_CHARS 字符（LLM 单次预算可控），最多 _MAX_CHUNKS 段；
    每段 3-8 条，跨段按主题去重（保留先出现的）。
    """
    from crawagent.llm.model import get_llm
    llm = get_llm(thinking=False, max_tokens=4096)
    chunks = [text[i:i + _CHUNK_CHARS] for i in range(0, len(text), _CHUNK_CHARS)]
    if len(chunks) > _MAX_CHUNKS:
        chunks = chunks[:_MAX_CHUNKS]
    entries: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        prompt = _DISTILL_PROMPT.format(content=chunk)
        resp = llm.invoke([("system", _DISTILL_SYSTEM), ("human", prompt)])
        for ent in _parse_entries(getattr(resp, "content", "") or ""):
            key = ent["topic"].lower()
            if key in seen:
                continue
            seen.add(key)
            entries.append(ent)
    return entries


def _distill_and_archive(sid: str) -> dict:
    """提炼并归档单个会话（同步函数，路由线程池里跑）。

    失败一律不动文件夹（宁留勿丢）；入库走单事务全有全无。
    """
    sid = (sid or "").strip()
    if not sid:
        return {"ok": False, "error": "缺少会话 ID"}
    folder = sf.session_folder_path(sid)
    if not folder.is_dir():
        return {"ok": False, "error": "会话文件夹不存在（可能已归档或从未写入）"}

    # 046 蒸馏活跃态守卫：最近有对话的会话拒绝蒸馏——防 archive_session_folder
    # 移走活文件夹破坏后续轮末写 conversation.md。判据用 conversation.md mtime
    # （每轮写，最准）；无 conversation.md 时退回 folder mtime 兜底。
    last = sf.conversation_md_last_active(sid) or sf.folder_last_active(folder)
    if last and (time.time() - last) < _DISTILL_ACTIVE_SECS:
        return {"ok": False, "error": "会话仍活跃（24 小时内有对话），归档请先等其冷却。"}

    # 046 蒸馏输入集排除 conversation.md（原始轮次日志非 curated 快照，不进蒸馏）
    md_files = [
        (rel, text) for rel, text in sf.folder_md_texts(folder)
        if rel != sf.CONVERSATION_MD_NAME
    ]
    if not md_files:
        return {"ok": False, "error": "文件夹内没有可提炼的 md 文件，已保持原样。"}

    parts: list[str] = []
    total = 0
    truncated = False
    for rel, text in md_files:
        if total >= _MAX_TOTAL_CHARS:
            truncated = True
            break
        piece = f"\n\n===== {rel} =====\n\n{text}"
        parts.append(piece)
        total += len(piece)
    full_text = "".join(parts)
    if truncated:
        full_text += "\n\n（内容过长，超出部分未纳入本次提炼）"

    try:
        entries = _distill_entries(full_text)
    except Exception as e:
        return {"ok": False, "error": f"提炼失败：{e}。会话文件夹保持原样，可稍后重试。"}
    if not entries:
        return {"ok": False, "error": "提炼结果为空，会话文件夹保持原样未动。"}

    # 全有全无入库（与 save_record 同表同 FTS 触发器，免五关——蒸馏条目
    # 是 LLM 知识摘要不是爬取正文，长度/信噪比关卡会误杀短条目）
    from crawagent.tools.save_tool import _get_db_path, _init_db, _insert_record
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    try:
        for ent in entries:
            _insert_record(
                conn,
                url=f"session://{sid}",
                title=ent["topic"],
                content=f"{ent['content']}\n\n（来源：会话 {sid} 归档提炼，"
                        f"{datetime.date.today().isoformat()}）",
                extra_data=json.dumps({"source": "session_distill"}, ensure_ascii=False),
                platform="长期记忆",
                session_id=sid,
            )
        conn.commit()
    except Exception as e:
        return {"ok": False, "error": f"入库失败：{e}。会话文件夹保持原样，可稍后重试。"}
    finally:
        conn.close()

    archived_to = sf.archive_session_folder(sid)
    return {
        "ok": True,
        "records": len(entries),
        "archived_to": str(archived_to),
        "message": f"已提炼 {len(entries)} 条长期记忆并归档到 {archived_to}",
    }


@router.post("/api/data/sessions/{sid}/distill")
async def distill_session(sid: str) -> dict:
    """提炼并归档单个会话（用户显式触发；前端两步确认后才调用）。"""
    return _distill_and_archive(sid)


def log_stale_sessions() -> int:
    """启动时统计超期会话文件夹（只统计不自动清），打印人话提示。

    MCP_AUTOSTART 教训：任何批量动文件的操作必须用户显式触发——启动期
    只报数，提炼归档由用户在设置页点按钮。
    """
    try:
        rows = _overview_rows()
        stale = [r for r in rows if r["stale"]]
        if stale:
            print(
                f"[data] {len(stale)} 个会话文件夹超过 {sf.STALE_DAYS} 天未活跃，"
                "可在设置页「高级 → 会话记忆管理」提炼归档（不会自动清理）"
            )
        return len(stale)
    except Exception as e:
        print(f"[data] 会话文件夹统计失败（非致命）: {e}")
        return 0
