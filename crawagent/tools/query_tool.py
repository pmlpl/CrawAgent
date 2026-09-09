"""元数据查询工具 — 查询历史抓取记录。

硬约束加分项：list_crawled_resources 让用户问"我之前抓过哪些东西"，
直接 SQL 查询返回列表，无需重新爬取。

会话隔离：默认只返回**当前会话**的记录（session_id 由服务层注入），
避免用户说"继续之前的任务"时查到其他会话的内容。
用户问"我总共/所有会话抓过什么"时才用 scope="all" 跨会话查询。
"""
import sqlite3
from langchain_core.tools import tool
from crawagent.tools.save_tool import (
    _init_db, _get_db_path, _infer_platform, _current_session,
)


@tool
def list_crawled_resources(
    platform: str = "",
    keyword: str = "",
    limit: int = 20,
    scope: str = "session",
) -> str:
    """Query previously crawled resources from the local database.

    Records are scoped by chat session by default. Use this when the user asks
    what they've crawled before, e.g.:
    - "我之前抓过哪些东西"
    - "列出我抓过的抖音视频"
    - "有没有抓过关于 LangGraph 的内容"

    Scope rules (IMPORTANT):
    - Default scope="session": ONLY records crawled in the CURRENT conversation.
      Use this when the user says "上次/刚才/这个任务/继续之前的爬取" — it keeps
      results relevant to this session instead of mixing in other sessions' data.
    - scope="all": records across ALL sessions. ONLY use when the user clearly
      asks about their whole history, e.g. "我总共抓过多少", "所有会话里抓过的".

    Args:
        platform: Optional platform filter (e.g. "番茄小说", "抖音", "B站"). Empty = all platforms.
        keyword: Optional keyword to search in title/url. Empty = no keyword filter.
        limit: Max number of records to return (default 20).
        scope: "session" (default, current conversation only) or "all" (cross-session).

    Returns:
        Formatted list of crawled resources:
        "Found 5 records (scope=session):
        1. [番茄小说] 标题 | url | 2024-01-15 14:30 | saved: /path/file.md
        2. ..."
        Or "No records found." if empty.
    """
    _init_db()
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)

    # 构建查询：platform / keyword / scope 都是可选过滤
    query = ("SELECT id, url, title, platform, save_path, created_at, session_id "
             "FROM crawl_records")
    conditions = []
    params = []

    if platform:
        # 支持模糊匹配平台名（用户可能说"抖音"但存的是"抖音"）
        conditions.append("platform LIKE ?")
        params.append(f"%{platform}%")

    if keyword:
        conditions.append("(title LIKE ? OR url LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    scope = "all" if str(scope).strip().lower() == "all" else "session"
    current_sid = _current_session.get("")
    if scope == "session":
        # 只看当前会话；未知会话（如 CLI 未注入）退化为空串标记的历史记录
        conditions.append("session_id = ?")
        params.append(current_sid)
        scope_desc = f"scope=session ({current_sid or '未指定'})"
    else:
        scope_desc = "scope=all"

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        filter_desc = [scope_desc]
        if platform:
            filter_desc.append(f"platform={platform}")
        if keyword:
            filter_desc.append(f"keyword={keyword}")
        filter_str = f" (filters: {', '.join(filter_desc)})"
        hint = ""
        if scope == "session":
            hint = ("\nHint: no record in this session. If the user means history "
                    "across sessions, retry with scope=\"all\".")
        return f"No records found{filter_str}.{hint}"

    lines = [f"Found {len(rows)} record(s) ({scope_desc}):"]
    for i, row in enumerate(rows, 1):
        rec_id, url, title, plat, save_path, created_at, rec_session = row
        # 时间格式化：去掉毫秒，只留到分钟
        time_str = created_at[:16].replace("T", " ") if created_at else ""
        plat_str = f"[{plat}]" if plat else "[未知平台]"
        title_str = title[:50] + ("..." if len(title or "") > 50 else "") if title else "(无标题)"
        save_str = f" | saved: {save_path}" if save_path else ""
        lines.append(f"{i}. {plat_str} {title_str} | {url[:80]} | {time_str}{save_str}")

    return "\n".join(lines)


def _fts_available() -> bool:
    """探测 crawl_records_fts 虚表是否就绪（FTS5 不可用时 _init_db 会跳过建表）。"""
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    try:
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='crawl_records_fts'"
        ).fetchone()
        return r is not None
    finally:
        conn.close()


@tool
def search_knowledge(query: str, limit: int = 10, scope: str = "session") -> str:
    """Full-text search the local knowledge base (crawled content stored via save_record).

    THE RETRIEVE-FIRST TOOL: before crawling a URL/topic, call this. If a hit covers
    what you need, answer from the KB — do NOT re-crawl. Only on miss (or insufficient)
    do you go fetch, and after a successful fetch you save_record so the KB grows.

    Searches title + content, ranks by relevance (bm25), returns a snippet of the
    matching passage for each hit so you can cite the right segment.

    Scope rules (same as list_crawled_resources):
    - scope="session" (default): only the current conversation's records.
    - scope="all": across ALL sessions — use when the user asks "之前/历史/有没有抓过".

    Args:
        query: search terms. Natural language works; for multi-term AND just space-separate.
        limit: max hits (default 10).
        scope: "session" (default) or "all".

    Returns:
        "Found N段 (scope=...):\n1. [平台] 标题 | url\n   …匹配文段摘录…\n2. ..."
        Or "No match for '...'. 可出门抓取并 save_record 入库。"
    """
    _init_db()
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    scope = "all" if str(scope).strip().lower() == "all" else "session"
    current_sid = _current_session.get("")
    scope_desc = f"scope=all" if scope == "all" else f"scope=session ({current_sid or '未指定'})"

    rows: list = []
    # 1) FTS5 trigram 主检索（英文按词、中文 ≥3 字短语命中）
    if _fts_available():
        try:
            sql = (
                "SELECT r.id, r.url, r.title, r.platform, "
                "snippet(crawl_records_fts, 1, '[', ']', '...', 16) AS excerpt "
                "FROM crawl_records_fts f JOIN crawl_records r ON r.id = f.rowid "
                "WHERE crawl_records_fts MATCH ? "
            )
            params: list = [query]
            if scope == "session":
                sql += "AND r.session_id = ? "
                params.append(current_sid)
            sql += "ORDER BY bm25(crawl_records_fts) LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            rows = []  # FTS 查询语法异常（如纯符号）→ 走 LIKE 兜底

    # 2) LIKE 兜底：FTS 无命中或查询过短（trigram 需 ≥3 字符）→ content LIKE 子串匹配
    if not rows:
        sql = ("SELECT id, url, title, platform, substr(content, "
               "max(1, instr(content, ?) - 40), 120) FROM crawl_records "
               "WHERE content LIKE ? ")
        like_q = f"%{query}%"
        params = [query, like_q]
        if scope == "session":
            sql += "AND session_id = ? "
            params.append(current_sid)
        sql += "ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            rows = []
    conn.close()

    if not rows:
        return (f"No match for '{query}' ({scope_desc}).\n"
                "库中无相关内容 —— 可出门抓取（crawl_webpage / browse_and_crawl），"
                "成功后用 save_record 入库，下次即可命中。")

    lines = [f"Found {len(rows)}段 ({scope_desc}):"]
    for i, row in enumerate(rows, 1):
        rec_id, url, title, plat, excerpt = row
        plat_str = f"[{plat}]" if plat else "[未知]"
        title_str = (title[:50] + "...") if title and len(title) > 50 else (title or "(无标题)")
        excerpt_str = (excerpt or "").replace("\n", " ").strip()
        if len(excerpt_str) > 160:
            excerpt_str = excerpt_str[:160] + "..."
        lines.append(f"{i}. {plat_str} {title_str} | {url[:80]}")
        lines.append(f"   …{excerpt_str}…")
    return "\n".join(lines)
