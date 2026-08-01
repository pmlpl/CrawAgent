"""监控数据模型 + SQLite 存储（P4-5 基线 + 任务/告警持久化）

三张表：
- monitor_tasks    : 监控任务定义（URL + 调度 + 字段 + 通知配置）
- monitor_baselines: 基线快照（URL → 内容 hash + 提取字段 + 上次检查时间）
- monitor_alerts   : 告警记录（任务 ID + 变化描述 + 时间 + 去抖状态）
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class ScheduleType(str, Enum):
    """调度类型"""
    INTERVAL = "interval"  # 间隔（秒）
    CRON = "cron"           # cron 表达式
    ONCE = "once"           # 一次性


class AlertLevel(str, Enum):
    """告警级别"""
    INFO = "info"       # 内容变化
    WARN = "warn"       # 状态码异常（403/429）
    ERROR = "error"     # 目标不可达（连续失败）
    CRITICAL = "critical"  # 价格大幅波动等


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class MonitorTask(BaseModel):
    """监控任务定义"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    url: str
    # 调度
    schedule_type: ScheduleType = ScheduleType.INTERVAL
    interval_seconds: int = 21600  # 默认 6 小时
    cron_expr: str = ""             # cron 模式
    enabled: bool = True
    # 提取字段（监控哪些字段的变化）
    watch_fields: List[str] = Field(default_factory=list)  # 如 ["price", "stock"]
    css_selector: str = ""          # 监控特定元素的 CSS 选择器
    # 告警配置
    alert_webhook: str = ""         # Webhook URL
    alert_cooldown: int = 3600      # 告警冷却（秒），同 URL 冷却内不重复告警
    alert_on_status: bool = True     # HTTP 状态码非 200 也告警
    alert_on_missing: bool = True    # 字段消失也告警
    # 元数据
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_run_at: Optional[float] = None
    # 运行时状态
    consecutive_failures: int = 0
    last_alert_at: Optional[float] = None


class AlertRecord(BaseModel):
    """告警记录"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    task_id: str
    level: AlertLevel = AlertLevel.INFO
    title: str = ""
    message: str = ""
    url: str = ""
    # 变化详情
    diff_summary: str = ""
    old_value: str = ""
    new_value: str = ""
    # 通知状态
    notified: bool = False
    notify_error: str = ""
    created_at: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# SQLite 存储
# ---------------------------------------------------------------------------

class MonitorStore:
    """监控任务 + 告警记录 + 基线快照的 SQLite 存储

    表结构：
    - monitor_tasks: 任务定义
    - monitor_baselines: 基线快照（URL → 内容 hash + 字段）
    - monitor_alerts: 告警记录
    """

    def __init__(self, db_path: str = "data/monitor.db"):
        self.db_path = str(Path(db_path).expanduser())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS monitor_tasks (
                    id TEXT PRIMARY KEY,
                    name TEXT DEFAULT '',
                    url TEXT NOT NULL,
                    schedule_type TEXT DEFAULT 'interval',
                    interval_seconds INTEGER DEFAULT 21600,
                    cron_expr TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    watch_fields TEXT DEFAULT '[]',
                    css_selector TEXT DEFAULT '',
                    alert_webhook TEXT DEFAULT '',
                    alert_cooldown INTEGER DEFAULT 3600,
                    alert_on_status INTEGER DEFAULT 1,
                    alert_on_missing INTEGER DEFAULT 1,
                    created_at REAL,
                    updated_at REAL,
                    last_run_at REAL,
                    consecutive_failures INTEGER DEFAULT 0,
                    last_alert_at REAL
                );

                CREATE TABLE IF NOT EXISTS monitor_baselines (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    content_hash TEXT,
                    fields_json TEXT DEFAULT '{}',
                    raw_content TEXT DEFAULT '',
                    checked_at REAL,
                    UNIQUE(task_id, url)
                );

                CREATE TABLE IF NOT EXISTS monitor_alerts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    level TEXT DEFAULT 'info',
                    title TEXT DEFAULT '',
                    message TEXT DEFAULT '',
                    url TEXT DEFAULT '',
                    diff_summary TEXT DEFAULT '',
                    old_value TEXT DEFAULT '',
                    new_value TEXT DEFAULT '',
                    notified INTEGER DEFAULT 0,
                    notify_error TEXT DEFAULT '',
                    created_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_alerts_task ON monitor_alerts(task_id);
                CREATE INDEX IF NOT EXISTS idx_alerts_created ON monitor_alerts(created_at);
                CREATE INDEX IF NOT EXISTS idx_baselines_task ON monitor_baselines(task_id);
            """)

    # ---- 任务 CRUD ----

    def create_task(self, task: MonitorTask) -> MonitorTask:
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO monitor_tasks
                (id, name, url, schedule_type, interval_seconds, cron_expr, enabled,
                 watch_fields, css_selector, alert_webhook, alert_cooldown,
                 alert_on_status, alert_on_missing, created_at, updated_at,
                 last_run_at, consecutive_failures, last_alert_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.id, task.name, task.url, task.schedule_type.value,
                task.interval_seconds, task.cron_expr, int(task.enabled),
                json.dumps(task.watch_fields, ensure_ascii=False),
                task.css_selector, task.alert_webhook, task.alert_cooldown,
                int(task.alert_on_status), int(task.alert_on_missing),
                task.created_at, task.updated_at, task.last_run_at,
                task.consecutive_failures, task.last_alert_at,
            ))
            conn.commit()
        return task

    def get_task(self, task_id: str) -> Optional[MonitorTask]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM monitor_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self, enabled_only: bool = False) -> List[MonitorTask]:
        with self._get_conn() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM monitor_tasks WHERE enabled = 1 ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM monitor_tasks ORDER BY created_at DESC"
                ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def update_task(self, task: MonitorTask) -> bool:
        task.updated_at = time.time()
        with self._get_conn() as conn:
            cur = conn.execute("""
                UPDATE monitor_tasks SET
                    name = ?, url = ?, schedule_type = ?, interval_seconds = ?,
                    cron_expr = ?, enabled = ?, watch_fields = ?, css_selector = ?,
                    alert_webhook = ?, alert_cooldown = ?, alert_on_status = ?,
                    alert_on_missing = ?, updated_at = ?, last_run_at = ?,
                    consecutive_failures = ?, last_alert_at = ?
                WHERE id = ?
            """, (
                task.name, task.url, task.schedule_type.value,
                task.interval_seconds, task.cron_expr, int(task.enabled),
                json.dumps(task.watch_fields, ensure_ascii=False),
                task.css_selector, task.alert_webhook, task.alert_cooldown,
                int(task.alert_on_status), int(task.alert_on_missing),
                task.updated_at, task.last_run_at,
                task.consecutive_failures, task.last_alert_at, task.id,
            ))
            conn.commit()
            return cur.rowcount > 0

    def delete_task(self, task_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM monitor_tasks WHERE id = ?", (task_id,))
            conn.execute("DELETE FROM monitor_baselines WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM monitor_alerts WHERE task_id = ?", (task_id,))
            conn.commit()
            return cur.rowcount > 0

    def _row_to_task(self, row: sqlite3.Row) -> MonitorTask:
        return MonitorTask(
            id=row["id"],
            name=row["name"],
            url=row["url"],
            schedule_type=ScheduleType(row["schedule_type"]),
            interval_seconds=row["interval_seconds"],
            cron_expr=row["cron_expr"],
            enabled=bool(row["enabled"]),
            watch_fields=json.loads(row["watch_fields"] or "[]"),
            css_selector=row["css_selector"],
            alert_webhook=row["alert_webhook"],
            alert_cooldown=row["alert_cooldown"],
            alert_on_status=bool(row["alert_on_status"]),
            alert_on_missing=bool(row["alert_on_missing"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_run_at=row["last_run_at"],
            consecutive_failures=row["consecutive_failures"],
            last_alert_at=row["last_alert_at"],
        )

    # ---- 基线快照 ----

    def get_baseline(self, task_id: str, url: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM monitor_baselines WHERE task_id = ? AND url = ?",
                (task_id, url),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "url": row["url"],
            "content_hash": row["content_hash"],
            "fields": json.loads(row["fields_json"] or "{}"),
            "raw_content": row["raw_content"],
            "checked_at": row["checked_at"],
        }

    def save_baseline(
        self,
        task_id: str,
        url: str,
        content_hash: str,
        fields: Dict[str, Any],
        raw_content: str = "",
    ) -> None:
        baseline_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO monitor_baselines
                (id, task_id, url, content_hash, fields_json, raw_content, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, url) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    fields_json = excluded.fields_json,
                    raw_content = excluded.raw_content,
                    checked_at = excluded.checked_at
            """, (
                baseline_id, task_id, url, content_hash,
                json.dumps(fields, ensure_ascii=False), raw_content, now,
            ))
            conn.commit()

    # ---- 告警记录 ----

    def create_alert(self, alert: AlertRecord) -> AlertRecord:
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO monitor_alerts
                (id, task_id, level, title, message, url, diff_summary,
                 old_value, new_value, notified, notify_error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alert.id, alert.task_id, alert.level.value, alert.title,
                alert.message, alert.url, alert.diff_summary,
                alert.old_value, alert.new_value, int(alert.notified),
                alert.notify_error, alert.created_at,
            ))
            conn.commit()
        return alert

    def list_alerts(self, task_id: str = "", limit: int = 50) -> List[AlertRecord]:
        with self._get_conn() as conn:
            if task_id:
                rows = conn.execute(
                    "SELECT * FROM monitor_alerts WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM monitor_alerts ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_alert(r) for r in rows]

    def _row_to_alert(self, row: sqlite3.Row) -> AlertRecord:
        return AlertRecord(
            id=row["id"],
            task_id=row["task_id"],
            level=AlertLevel(row["level"]),
            title=row["title"],
            message=row["message"],
            url=row["url"],
            diff_summary=row["diff_summary"],
            old_value=row["old_value"],
            new_value=row["new_value"],
            notified=bool(row["notified"]),
            notify_error=row["notify_error"],
            created_at=row["created_at"],
        )


# 单例
_monitor_store: Optional[MonitorStore] = None


def get_monitor_store() -> MonitorStore:
    global _monitor_store
    if _monitor_store is None:
        from crawagent.config.settings import get_settings
        s = get_settings()
        _monitor_store = MonitorStore(db_path="data/monitor.db")
    return _monitor_store
