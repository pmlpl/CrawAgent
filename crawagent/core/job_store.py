from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger
from crawagent.config.settings import get_settings


class JobStore:
    """任务持久化存储（SQLite）"""
    
    def __init__(self, db_path: str = None):
        settings = get_settings()
        self.db_path = Path(db_path) if db_path else Path(settings.data_dir) / "jobs.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    instruction TEXT,
                    seed_urls TEXT,
                    status TEXT DEFAULT 'pending',
                    progress_json TEXT DEFAULT '{}',
                    items_count INTEGER DEFAULT 0,
                    items_json TEXT DEFAULT '[]',
                    logs_json TEXT DEFAULT '[]',
                    error TEXT,
                    created_at REAL,
                    updated_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)")
            conn.commit()
        finally:
            conn.close()
    
    async def create_job(self, job_id: str, instruction: str, seed_urls: List[str]) -> Dict[str, Any]:
        """创建任务"""
        now = time.time()
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                "INSERT INTO jobs (job_id, instruction, seed_urls, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, instruction, json.dumps(seed_urls, ensure_ascii=False), "running", now, now)
            )
            await db.commit()
        
        return await self.get_job(job_id)
    
    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """获取任务详情"""
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                return self._row_to_dict(row)
    
    async def list_jobs(self, limit: int = 20, status: str = None) -> List[Dict[str, Any]]:
        """获取任务列表（按创建时间倒序）"""
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            if status:
                query = "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?"
                params = (status, limit)
            else:
                query = "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?"
                params = (limit,)
            
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                return [self._row_to_dict(row) for row in rows]
    
    async def update_job(self, job_id: str, **kwargs):
        """更新任务字段"""
        if not kwargs:
            return
        
        now = time.time()
        fields = []
        values = []
        for key, value in kwargs.items():
            if key == "job_id":
                continue
            if key in ("progress", "items", "logs"):
                field = f"{key}_json"
                fields.append(f"{field} = ?")
                values.append(json.dumps(value, ensure_ascii=False))
            elif key == "seed_urls":
                fields.append("seed_urls = ?")
                values.append(json.dumps(value, ensure_ascii=False))
            else:
                fields.append(f"{key} = ?")
                values.append(value)
        
        fields.append("updated_at = ?")
        values.append(now)
        values.append(job_id)
        
        sql = f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?"
        
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(sql, values)
            await db.commit()
    
    async def delete_job(self, job_id: str) -> bool:
        """删除任务"""
        async with aiosqlite.connect(str(self.db_path)) as db:
            result = await db.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            await db.commit()
            return result.rowcount > 0
    
    def _row_to_dict(self, row) -> Dict[str, Any]:
        """行转字典"""
        return {
            "job_id": row["job_id"],
            "instruction": row["instruction"],
            "seed_urls": json.loads(row["seed_urls"] or "[]"),
            "status": row["status"],
            "progress": json.loads(row["progress_json"] or "{}"),
            "items_count": row["items_count"] or 0,
            "items": json.loads(row["items_json"] or "[]"),
            "logs": json.loads(row["logs_json"] or "[]"),
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


# 单例
_job_store: Optional[JobStore] = None


def get_job_store() -> JobStore:
    global _job_store
    if _job_store is None:
        _job_store = JobStore()
    return _job_store

