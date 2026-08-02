from __future__ import annotations

import json
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from loguru import logger


class SearchResult(BaseModel):
    id: str
    title: str
    content: str
    score: float
    metadata: Dict[str, Any] = {}


class BaseRetriever(ABC):
    """检索器抽象基类"""

    @abstractmethod
    def add(self, docs: List[Dict[str, Any]]) -> None:
        pass

    @abstractmethod
    def search(self, query: str, top_k: int = 10) -> List[SearchResult]:
        pass

    @abstractmethod
    def delete(self, ids: List[str]) -> None:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass


class SQLiteFTS5Retriever(BaseRetriever):
    """
    基于 SQLite FTS5 的全文检索器
    - 零依赖、零运维
    - BM25 评分排序
    - 支持中文分词 (porter + unicode61)
    """

    def __init__(self, db_path: str = "data/retriever.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-32768")

        # FTS5 虚拟表
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                id UNINDEXED,
                url UNINDEXED,
                title,
                content,
                metadata,
                tokenize='porter unicode61'
            )
        """)
        self._conn.commit()

    def _normalize_query(self, query: str) -> str:
        """查询规范化：FTS5 语法转义"""
        # 简单转义特殊字符
        special = '"-+*(){}[]^'
        for ch in special:
            query = query.replace(ch, f"\\{ch}")
        return query

    def add(self, docs: List[Dict[str, Any]]) -> None:
        """批量添加文档"""
        if not docs:
            return

        now = time.time()
        rows = []
        for doc in docs:
            doc_id = doc.get("id") or f"doc_{int(now * 1000000)}_{hash(doc.get('url', '')) & 0xffff}"
            rows.append((
                doc_id,
                doc.get("url", ""),
                doc.get("title", ""),
                doc.get("content", ""),
                json.dumps(doc.get("metadata", {}), ensure_ascii=False),
            ))

        self._conn.executemany(
            "INSERT OR REPLACE INTO docs_fts (id, url, title, content, metadata) VALUES (?, ?, ?, ?, ?)",
            rows
        )
        self._conn.commit()

    def search(self, query: str, top_k: int = 10) -> List[SearchResult]:
        """全文检索，BM25 排序"""
        if not query or not query.strip():
            return []

        norm_query = self._normalize_query(query.strip())
        try:
            cursor = self._conn.execute(
                """SELECT id, url, title, content, metadata, bm25(docs_fts) as score
                   FROM docs_fts
                   WHERE docs_fts MATCH ?
                   ORDER BY score
                   LIMIT ?""",
                (norm_query, top_k)
            )
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS5 search error: {e}, falling back to LIKE")
            cursor = self._conn.execute(
                """SELECT id, url, title, content, metadata, 0 as score
                   FROM docs_fts
                   WHERE content LIKE ? OR title LIKE ?
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", top_k)
            )

        results = []
        for row in cursor.fetchall():
            results.append(SearchResult(
                id=row[0],
                title=row[2] or "",
                content=row[3] or "",
                score=float(row[5]),
                metadata=json.loads(row[4]) if row[4] else {},
            ))
        return results

    def delete(self, ids: List[str]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self._conn.execute(f"DELETE FROM docs_fts WHERE id IN ({placeholders})", ids)
        self._conn.commit()

    def clear(self) -> None:
        self._conn.execute("DELETE FROM docs_fts")
        self._conn.commit()

    def get_stats(self) -> Dict[str, int]:
        cursor = self._conn.execute("SELECT COUNT(*) FROM docs_fts")
        return {"total_docs": cursor.fetchone()[0]}

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# 工厂
def create_retriever(backend: str = "sqlite_fts5", **kwargs) -> BaseRetriever:
    """创建检索器实例。

    当前仅支持 sqlite_fts5（零依赖全文检索）。如未来引入向量检索，
    在此注册新 backend 即可。
    """
    if backend != "sqlite_fts5":
        raise ValueError(
            f"不支持的检索后端: {backend}（当前仅支持 'sqlite_fts5'）"
        )
    return SQLiteFTS5Retriever(**kwargs)