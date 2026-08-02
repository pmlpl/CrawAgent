from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import aiosqlite
from loguru import logger
from crawagent.config.settings import get_settings


# URL 规范化
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "referrer", "_gl", "_ga",
}


def canonicalize_url(url: str) -> str:
    """URL 规范化：去除 tracking params、片段、统一大小写、排序查询参数"""
    parsed = urlparse(url.strip())

    # 去除 fragment
    fragment = ""

    # 过滤查询参数
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        k: v for k, v in query_params.items()
        if k.lower() not in TRACKING_PARAMS
    }

    # 排序查询参数
    sorted_query = urlencode(sorted(filtered.items()), doseq=True)

    # 重建 URL
    canonical = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        parsed.params,
        sorted_query,
        fragment,
    ))
    return canonical


def url_fingerprint(url: str) -> str:
    """生成 URL 指纹（用于去重）"""
    canonical = canonicalize_url(url)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def content_fingerprint(content: str) -> str:
    """内容指纹（用于内容去重）"""
    return hashlib.md5(content.encode()).hexdigest()[:16]


@dataclass
class URLRecord:
    """URL 记录"""
    url: str
    canonical_url: str
    fingerprint: str
    depth: int = 0
    parent_url: str = ""
    priority: int = 0
    status: str = "pending"  # pending, fetched, failed, duplicate
    retry_count: int = 0
    last_error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: str = "{}"


class SQLiteFrontier:
    """
    SQLite + FTS5 爬取前沿
    - 队列管理（优先级 + 深度）
    - URL 去重（规范化 + 指纹）
    - 内容去重（MD5 指纹）
    - 断点续爬
    - 状态持久化
    """

    def __init__(self, db_path: str = "data/crawl_frontier.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """异步初始化数据库"""
        async with self._init_lock:
            if self._initialized:
                return

            self._conn = await aiosqlite.connect(
                self.db_path,
                isolation_level=None,  # 自动提交
                timeout=30.0,
            )
            # 启用 WAL 模式
            await self._conn.execute("PRAGMA journal_mode=WAL;")
            await self._conn.execute("PRAGMA busy_timeout=5000;")
            await self._conn.execute("PRAGMA synchronous=NORMAL;")

            await self._create_tables()
            self._initialized = True
            logger.info(f"Frontier initialized at {self.db_path}")

    async def _create_tables(self) -> None:
        """创建表结构"""
        await self._conn.executescript("""
            -- URL 队列主表
            CREATE TABLE IF NOT EXISTS urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                canonical_url TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL,
                depth INTEGER DEFAULT 0,
                parent_url TEXT DEFAULT '',
                priority INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_urls_status_priority
                ON urls (status, priority DESC, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_urls_fingerprint
                ON urls (fingerprint);
            CREATE INDEX IF NOT EXISTS idx_urls_parent
                ON urls (parent_url);

            -- 内容指纹表（内容去重）
            CREATE TABLE IF NOT EXISTS content_hashes (
                hash TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                created_at REAL DEFAULT 0
            );

            -- 爬取任务元信息
            CREATE TABLE IF NOT EXISTS crawl_jobs (
                job_id TEXT PRIMARY KEY,
                plan TEXT NOT NULL,
                site_analysis TEXT,
                status TEXT DEFAULT 'pending',
                stats TEXT DEFAULT '{}',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            );

            -- 提取结果
            CREATE TABLE IF NOT EXISTS extracted_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT DEFAULT '',
                content TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}',
                created_at REAL DEFAULT 0,
                FOREIGN KEY (job_id) REFERENCES crawl_jobs (job_id)
            );

            -- 事件日志（JSONL 风格）
            CREATE TABLE IF NOT EXISTS crawl_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                event_type TEXT NOT NULL,
                event_data TEXT NOT NULL,
                timestamp REAL DEFAULT 0
            );

            -- FTS5 全文搜索虚拟表
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                url UNINDEXED,
                title,
                content,
                metadata,
                tokenize='porter unicode61'
            );
        """)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            self._initialized = False

    # ==================== URL 队列操作 ====================

    async def add_url(
        self,
        url: str,
        depth: int = 0,
        parent_url: str = "",
        priority: int = 0,
        metadata: Dict = None,
    ) -> bool:
        """添加 URL 到队列，返回是否成功添加（去重后）"""
        if not self._initialized:
            await self.initialize()

        canonical = canonicalize_url(url)
        fingerprint = url_fingerprint(url)
        now = time.time()

        # 先检查是否已存在
        existing = await self._conn.execute(
            "SELECT id, status FROM urls WHERE fingerprint = ?",
            (fingerprint,)
        )
        row = await existing.fetchone()
        if row:
            # 已存在，可选：更新优先级或深度
            if row[1] == "failed":
                await self._conn.execute(
                    "UPDATE urls SET status='pending', priority=?, retry_count=0, last_error='', updated_at=? WHERE id=?",
                    (priority, time.time(), row[0])
                )
                await self._conn.commit()
                return True
            return False

        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        await self._conn.execute(
            """INSERT INTO urls (url, canonical_url, fingerprint, depth, parent_url,
                priority, status, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (url, canonical, fingerprint, depth, parent_url, priority, now, now, metadata_json)
        )
        await self._conn.commit()
        return True

    async def add_urls_batch(
        self,
        urls: List[Dict[str, Any]],
        default_priority: int = 0,
    ) -> int:
        """批量添加 URL"""
        if not urls:
            return 0

        added = 0
        now = time.time()
        for u in urls:
            url = u.get("url") or u.get("link") or ""
            if not url:
                continue
            if await self.add_url(
                url=url,
                depth=u.get("depth", 0),
                parent_url=u.get("parent_url", ""),
                priority=u.get("priority", default_priority),
                metadata=u.get("metadata"),
            ):
                added += 1
        return added

    async def pop_next(self, limit: int = 1) -> List[URLRecord]:
        """弹出待爬取的 URL（优先级高、深度浅优先）"""
        if not self._initialized:
            await self.initialize()

        records = []
        async with self._conn.execute(
            """SELECT id, url, canonical_url, fingerprint, depth, parent_url,
                priority, status, retry_count, last_error, created_at, updated_at, metadata
                FROM urls
                WHERE status = 'pending'
                ORDER BY priority DESC, depth ASC, created_at ASC
                LIMIT ?""",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            record = URLRecord(
                url=row[1],
                canonical_url=row[2],
                fingerprint=row[3],
                depth=row[4],
                parent_url=row[5],
                priority=row[6],
                status=row[7],
                retry_count=row[8],
                last_error=row[9],
                created_at=row[10],
                updated_at=row[11],
                metadata=row[12],
            )
            # 标记为 fetching
            await self._conn.execute(
                "UPDATE urls SET status='fetching', updated_at=? WHERE id=?",
                (time.time(), row[0])
            )
            records.append(record)

        if records:
            await self._conn.commit()

        return records

    async def mark_done(
        self,
        url: str,
        success: bool = True,
        error: str = "",
        new_urls: List[Dict] = None,
    ) -> None:
        """标记 URL 完成"""
        if not self._initialized:
            await self.initialize()

        canonical = canonicalize_url(url)
        fingerprint = url_fingerprint(url)
        now = time.time()

        if success:
            await self._conn.execute(
                "UPDATE urls SET status='fetched', updated_at=? WHERE fingerprint=?",
                (now, fingerprint)
            )
        else:
            await self._conn.execute(
                """UPDATE urls
                    SET status='failed', retry_count=retry_count+1, last_error=?, updated_at=?
                    WHERE fingerprint=?""",
                (error, now, fingerprint)
            )

        # 添加新发现的 URL
        if new_urls:
            await self.add_urls_batch(new_urls)

        await self._conn.commit()

    async def retry_failed(self, max_retries: int = 3) -> int:
        """将失败且未超重试次数的 URL 重新放入队列"""
        if not self._initialized:
            await self.initialize()

        cursor = await self._conn.execute(
            """UPDATE urls
                SET status='pending', retry_count=0, last_error='', updated_at=?
                WHERE status='failed' AND retry_count < ?""",
            (time.time(), max_retries)
        )
        await self._conn.commit()
        return cursor.rowcount

    # ==================== 任务快照 / 断点续抓（P1-4） ====================

    async def snapshot_job(self, job_id: str = "") -> Dict[str, int]:
        """任务快照：统计各状态 URL 数量（断点续抓基础）。

        Args:
            job_id: 按任务过滤（URL 的 metadata 中含 job_id 时有效）；空则统计全部。

        Returns:
            {"pending": n, "fetching": n, "fetched": n, "failed": n}
        """
        if not self._initialized:
            await self.initialize()

        statuses: Dict[str, int] = {}
        for st in ("pending", "fetching", "fetched", "failed"):
            if job_id:
                cursor = await self._conn.execute(
                    "SELECT COUNT(*) FROM urls WHERE status=? AND metadata LIKE ?",
                    (st, f'%"{job_id}"%'),
                )
            else:
                cursor = await self._conn.execute(
                    "SELECT COUNT(*) FROM urls WHERE status=?", (st,)
                )
            statuses[st] = (await cursor.fetchone())[0]
        return statuses

    async def resume_job(self, max_retries: int = 3, job_id: str = "") -> int:
        """断点续抓：把失败未超重试次数 + 卡在 fetching（上次中断遗留）的 URL 重新置为 pending。

        Returns:
            重新入队的 URL 数量（failed → pending 部分）。
        """
        if not self._initialized:
            await self.initialize()

        now = time.time()
        if job_id:
            cur = await self._conn.execute(
                """UPDATE urls
                    SET status='pending', last_error='', updated_at=?
                    WHERE status='failed' AND retry_count < ? AND metadata LIKE ?""",
                (now, max_retries, f'%"{job_id}"%'),
            )
        else:
            cur = await self._conn.execute(
                """UPDATE urls
                    SET status='pending', last_error='', updated_at=?
                    WHERE status='failed' AND retry_count < ?""",
                (now, max_retries),
            )
        # 中断遗留的 fetching → 重新排队（上次进程退出时未标记完成）
        await self._conn.execute(
            "UPDATE urls SET status='pending', updated_at=? WHERE status='fetching'",
            (now,),
        )
        await self._conn.commit()
        return cur.rowcount

    # ==================== 内容去重 ====================

    async def is_duplicate_content(self, content_hash: str) -> bool:
        """检查内容是否重复"""
        if not self._initialized:
            await self.initialize()

        cursor = await self._conn.execute(
            "SELECT 1 FROM content_hashes WHERE hash=?",
            (content_hash,)
        )
        return await cursor.fetchone() is not None

    async def add_content_hash(self, content_hash: str, url: str) -> bool:
        """添加内容指纹"""
        if not self._initialized:
            await self.initialize()

        try:
            await self._conn.execute(
                "INSERT INTO content_hashes (hash, url, created_at) VALUES (?, ?, ?)",
                (content_hash, url, time.time())
            )
            await self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    # ==================== 任务管理 ====================

    async def create_job(self, job) -> str:
        """创建爬取任务"""
        if not self._initialized:
            await self.initialize()

        job_id = job.id
        await self._conn.execute(
            """INSERT INTO crawl_jobs (job_id, plan, site_analysis, status, stats, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                job.plan.model_dump_json(),
                job.site_analysis.model_dump_json() if job.site_analysis else "{}",
                job.status,
                json.dumps(job.stats),
                job.created_at,
                job.updated_at,
            )
        )
        await self._conn.commit()
        return job_id

    async def update_job(self, job) -> None:
        """更新任务状态"""
        if not self._initialized:
            await self.initialize()

        await self._conn.execute(
            """UPDATE crawl_jobs
                SET plan=?, site_analysis=?, status=?, stats=?, updated_at=?
                WHERE job_id=?""",
            (
                job.plan.model_dump_json(),
                job.site_analysis.model_dump_json() if job.site_analysis else "{}",
                job.status,
                json.dumps(job.stats),
                time.time(),
                job.id,
            )
        )
        await self._conn.commit()

    async def get_job(self, job_id: str):
        """获取任务"""
        if not self._initialized:
            await self.initialize()

        cursor = await self._conn.execute(
            "SELECT * FROM crawl_jobs WHERE job_id=?",
            (job_id,)
        )
        return await cursor.fetchone()

    # ==================== 结果存储 ====================

    async def save_items(self, job_id: str, items: List[Any]) -> int:
        """保存提取结果"""
        if not self._initialized:
            await self.initialize()

        if not items:
            return 0

        now = time.time()
        count = 0
        for item in items:
            await self._conn.execute(
                """INSERT INTO extracted_items (job_id, url, title, content, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    getattr(item, "url", ""),
                    getattr(item, "title", ""),
                    getattr(item, "content", ""),
                    json.dumps(getattr(item, "metadata", {}), ensure_ascii=False),
                    now,
                )
            )
            count += 1

        await self._conn.commit()
        return count

    async def get_items(self, job_id: str, limit: int = 100, offset: int = 0) -> List[Dict]:
        """获取提取结果"""
        if not self._initialized:
            await self.initialize()

        cursor = await self._conn.execute(
            """SELECT url, title, content, metadata, created_at
                FROM extracted_items
                WHERE job_id=?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?""",
            (job_id, limit, offset)
        )
        rows = await cursor.fetchall()
        return [
            {
                "url": r[0],
                "title": r[1],
                "content": r[2],
                "metadata": json.loads(r[3]),
                "created_at": r[4],
            }
            for r in rows
        ]

    # ==================== FTS5 全文搜索 ====================

    async def index_document(self, url: str, title: str, content: str, metadata: Dict = None) -> None:
        """索引文档到 FTS5"""
        if not self._initialized:
            await self.initialize()

        meta = json.dumps(metadata or {}, ensure_ascii=False)
        await self._conn.execute(
            "INSERT INTO docs_fts (url, title, content, metadata) VALUES (?, ?, ?, ?)",
            (url, title, content[:50000], meta)  # 限制内容长度
        )
        await self._conn.commit()

    async def search(self, query: str, limit: int = 10) -> List[Dict]:
        """全文搜索"""
        if not self._initialized:
            await self.initialize()

        cursor = await self._conn.execute(
            """SELECT url, title, snippet(docs_fts, 1, '<mark>', '</mark>', '...', 20) as snippet,
                metadata, bm25(docs_fts) as rank
                FROM docs_fts
                WHERE docs_fts MATCH ?
                ORDER BY rank
                LIMIT ?""",
            (query, limit)
        )
        rows = await cursor.fetchall()
        return [
            {
                "url": r[0],
                "title": r[1],
                "snippet": r[2],
                "metadata": json.loads(r[3]) if r[3] else {},
                "score": r[4],
            }
            for r in rows
        ]

    # ==================== 事件日志 ====================

    async def log_event(self, job_id: str, event_type: str, data: Dict) -> None:
        """记录事件"""
        if not self._initialized:
            await self.initialize()

        await self._conn.execute(
            "INSERT INTO crawl_events (job_id, event_type, event_data, timestamp) VALUES (?, ?, ?, ?)",
            (job_id, event_type, json.dumps(data, ensure_ascii=False), time.time())
        )
        await self._conn.commit()

    # ==================== 统计与维护 ====================

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        if not self._initialized:
            await self.initialize()

        stats = {}
        queries = {
            "pending": "SELECT COUNT(*) FROM urls WHERE status='pending'",
            "fetching": "SELECT COUNT(*) FROM urls WHERE status='fetching'",
            "fetched": "SELECT COUNT(*) FROM urls WHERE status='fetched'",
            "failed": "SELECT COUNT(*) FROM urls WHERE status='failed'",
            "total_urls": "SELECT COUNT(*) FROM urls",
            "content_hashes": "SELECT COUNT(*) FROM content_hashes",
            "jobs": "SELECT COUNT(*) FROM crawl_jobs",
            "items": "SELECT COUNT(*) FROM extracted_items",
        }

        for key, sql in queries.items():
            cursor = await self._conn.execute(sql)
            stats[key] = (await cursor.fetchone())[0]

        return stats

    async def vacuum(self) -> None:
        """整理数据库"""
        if not self._initialized:
            await self.initialize()
        await self._conn.execute("VACUUM;")
        logger.info("Frontier database vacuumed")