"""Phase 3.3 — 定时调度器。

最简 cron 调度器：持久化到 data/schedules.json，后台线程每分钟 tick。
每次 tick 检查哪些 job 该跑 → 用 requests GET 一次 → 算 content hash →
跟上次比有变化才推 webhook 通知。

cron 表达式（5 字段，分钟粒度）：
    * * * * *
    │ │ │ │ │
    │ │ │ │ └─ weekday (0=Sun, 6=Sat 或 1=Mon, 7=Sun)
    │ │ │ └── month (1-12)
    │ │ └──── day (1-31)
    │ └────── hour (0-23)
    └──────── minute (0-59)

支持：*  */N  M,N,O  M-N  M-N/S
"""
from .cron_scheduler import (
    CronJob,
    cron_should_run,
    register_cron_job,
    list_cron_jobs,
    delete_cron_job,
    toggle_job,
    add_notify_cb,
    start_engine,
    stop_engine,
)

__all__ = [
    "CronJob",
    "cron_should_run",
    "register_cron_job",
    "list_cron_jobs",
    "delete_cron_job",
    "toggle_job",
    "add_notify_cb",
    "start_engine",
    "stop_engine",
]
