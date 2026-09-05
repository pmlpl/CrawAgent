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
