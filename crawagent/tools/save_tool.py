"""存储工具 — 将提取的内容保存到 SQLite 数据库"""
import sqlite3
import json
from datetime import datetime
from langchain_core.tools import tool
from crawagent.config.settings import get_settings


# 已知平台的 URL 域名映射（用于从 URL 自动推断 platform 字段）
_PLATFORM_DOMAINS = {
    "fanqienovel.com": "番茄小说",
    "douyin.com": "抖音",
    "iesdouyin.com": "抖音",
    "bilibili.com": "B站",
    "b23.tv": "B站",
    "youku.com": "优酷",
    "v.qq.com": "腾讯视频",
    "iqiyi.com": "爱奇艺",
    "news.ycombinator.com": "Hacker News",
    "github.com": "GitHub",
    "zhihu.com": "知乎",
    "weibo.com": "微博",
    "xiaohongshu.com": "小红书",
    "jianshu.com": "简书",
    "csdn.net": "CSDN",
}


def _infer_platform(url: str) -> str:
    """从 URL 推断平台名称。未知平台返回空字符串。"""
    if not url:
        return ""
    url_lower = url.lower()
    for domain, name in _PLATFORM_DOMAINS.items():
        if domain in url_lower:
            return name
    return ""


def _get_db_path() -> str:
    """确保数据库目录存在并返回路径"""
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(settings.db_path)


def _init_db() -> None:
    """初始化数据库表 + 兼容迁移（已有表加新字段）"""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawl_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            content TEXT,
            extra_data TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # 兼容迁移：给已有表加 platform / save_path 字段（SQLite ADD COLUMN 无损）
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(crawl_records)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "platform" not in existing_cols:
        conn.execute("ALTER TABLE crawl_records ADD COLUMN platform TEXT DEFAULT ''")
    if "save_path" not in existing_cols:
        conn.execute("ALTER TABLE crawl_records ADD COLUMN save_path TEXT DEFAULT ''")
    conn.commit()
    conn.close()


@tool
def save_record(url: str, title: str, content: str, extra_data: str = "", platform: str = "", save_path: str = "") -> str:
    """Save extracted content to the local database.

    Only call this tool when the extracted content is valid and complete.
    Do NOT save if the content is empty, garbled, or failed to extract due to anti-crawl measures.

    Args:
        url: The URL of the crawled webpage
        title: The page title
        content: The extracted body text — must be meaningful content, not empty or error messages
        extra_data: Optional additional data (JSON string), e.g. link lists, image lists
        platform: Optional platform name (e.g. "番茄小说", "抖音", "B站"). If omitted, inferred from URL.
        save_path: Optional local file path if content was also saved to a file

    Returns:
        On success: "Saved successfully! Record ID: <id>, URL: <url>, Title: <title>, Content length: <N> chars".
        On failure: database error message.
    """
    _init_db()
    db_path = _get_db_path()
    # platform 未指定则从 URL 推断
    if not platform:
        platform = _infer_platform(url)

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO crawl_records (url, title, content, extra_data, created_at, platform, save_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (url, title, content, extra_data, datetime.now().isoformat(), platform, save_path),
        )
        record_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    return f"Saved successfully! Record ID: {record_id}, URL: {url}, Title: {title}, Content length: {len(content)} chars"
