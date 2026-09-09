"""存储工具 — 将提取的内容保存到 SQLite 数据库"""
import sqlite3
import json
from contextvars import ContextVar
from datetime import datetime
from langchain_core.tools import tool
from crawagent.config.settings import get_settings

# 当前会话上下文：由 web 服务层（server.py 的 _run_turn）在任务线程开始时设置，
# 工具执行与 agent.stream 在同一线程，可安全读取。
# save_record 自动带上 session_id，避免依赖 LLM 传参；CLI 场景为空串（全局记录）。
_current_session: ContextVar[str] = ContextVar("crawagent_current_session", default="")


def set_current_session(session_id: str) -> None:
    """设置当前执行线程所属的会话 ID（供 save_record/list_crawled_resources 使用）"""
    _current_session.set(session_id or "")


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
    # 兼容迁移：给已有表加 platform / save_path / session_id 字段（SQLite ADD COLUMN 无损）
    # 并发首启时另一线程可能已完成迁移，duplicate column 直接忽略
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(crawl_records)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    for col in ("platform", "save_path", "session_id"):
        if col not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE crawl_records ADD COLUMN {col} TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass

    # ── FTS5 全文索引（变更 008）──────────────────────────────
    # external-content 模式：FTS 只存索引不重复存正文，查询时 JOIN 回主表取 url 等。
    # trigram 分词：英文按词、中文按 3-gram 子串（≥3 字短语命中；短词由 search_knowledge
    # 的 LIKE 兜底）。触发器让主表 INSERT/UPDATE/DELETE 自动同步 FTS。
    fts_available = _ensure_fts5_index(conn)
    conn.commit()
    conn.close()


def _ensure_fts5_index(conn: sqlite3.Connection) -> bool:
    """建 FTS5 虚表 + 触发器 + 回填存量（幂等）。FTS5 不可用则跳过返回 False。"""
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS crawl_records_fts USING fts5("
            "title, content, content='crawl_records', content_rowid='id', tokenize='trigram')"
        )
        for stmt in (
            "CREATE TRIGGER IF NOT EXISTS cr_fts_ai AFTER INSERT ON crawl_records "
            "BEGIN INSERT INTO crawl_records_fts(rowid,title,content) "
            "VALUES(new.id,new.title,new.content); END",
            "CREATE TRIGGER IF NOT EXISTS cr_fts_ad AFTER DELETE ON crawl_records "
            "BEGIN DELETE FROM crawl_records_fts WHERE rowid=old.id; END",
            "CREATE TRIGGER IF NOT EXISTS cr_fts_au AFTER UPDATE ON crawl_records "
            "BEGIN DELETE FROM crawl_records_fts WHERE rowid=old.id; "
            "INSERT INTO crawl_records_fts(rowid,title,content) "
            "VALUES(new.id,new.title,new.content); END",
        ):
            conn.execute(stmt)
        # 回填存量（主表有但 FTS 没有的行；触发器只管新行，老库首次迁要补）
        conn.execute(
            "INSERT INTO crawl_records_fts(rowid, title, content) "
            "SELECT id, title, content FROM crawl_records "
            "WHERE id NOT IN (SELECT rowid FROM crawl_records_fts)"
        )
        return True
    except sqlite3.OperationalError as e:
        # FTS5 未编译进 sqlite（罕见）→ 降级，search_knowledge 走纯 LIKE
        print(f"[save] FTS5 不可用，检索降级为 LIKE: {e}")
        return False


@tool
def save_record(url: str, title: str, content: str, extra_data: str = "", platform: str = "", save_path: str = "") -> str:
    """把抽取到的内容保存进本地 SQLite 数据库。

    **必须在确认内容正确且完整后再调**。
    如果内容空、乱码、或被反爬挡住没抽出来 → 严禁调用它存一条垃圾记录。

    参数：
        url: 被爬页面的 URL
        title: 页面标题
        content: 抽取到的正文 —— 必须是实际可读内容，不准是空或报错文字
        extra_data: 可选附加数据（JSON 字串），如链接列表、图片清单
        platform: 可选平台名，例 "番茄小说" / "抖音" / "B站"；为空则从 URL 推断
        save_path: 可选。如果内容也同时存了本地文件，这里填对应绝对路径

    返回：
        成功："保存成功! 记录 ID: <id>, URL: <url>, 标题: <title>, 内容长度: <N>字"
        失败：数据库错误信息字串。
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
            "INSERT INTO crawl_records (url, title, content, extra_data, created_at, platform, save_path, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (url, title, content, extra_data, datetime.now().isoformat(), platform, save_path,
             _current_session.get("")),
        )
        record_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    return f"Saved successfully! Record ID: {record_id}, URL: {url}, Title: {title}, Content length: {len(content)} chars"
