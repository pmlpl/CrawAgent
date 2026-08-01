"""监控场景模块（P4）：定时爬取 + 变化检测 + 告警

模块组成：
- models.py       : 监控任务/告警记录 Pydantic 模型 + SQLite 存储（MonitorStore）
- baseline.py     : 基线快照存储（按 URL 保存上次内容 hash + 提取字段）
- diff_detector.py: 变化检测（内容 hash / 字段对比 / 结构化 diff）
- notifier.py      : 告警通知（Webhook / 飞书 / 邮件）
- scheduler.py    : APScheduler 调度器（cron / interval）
- monitor_lane.py : harness 的 monitor lane 集成

设计原则：
- 调度器可选依赖（APScheduler 缺包时降级为手动触发）
- 存储用 SQLite（与 JobStore 一致，降低部署门槛）
- 告警去抖：cooldown 间隔内同 URL 只告警一次（验收标准：503 持续 10 分钟只触发一次）
"""
from crawagent.monitor.models import (
    MonitorTask,
    AlertRecord,
    MonitorStore,
    ScheduleType,
    AlertLevel,
    get_monitor_store,
)
from crawagent.monitor.baseline import BaselineStore
from crawagent.monitor.diff_detector import DiffDetector, DiffResult
from crawagent.monitor.notifier import Notifier
from crawagent.monitor.scheduler import MonitorScheduler

__all__ = [
    "MonitorTask",
    "AlertRecord",
    "MonitorStore",
    "ScheduleType",
    "AlertLevel",
    "get_monitor_store",
    "BaselineStore",
    "DiffDetector",
    "DiffResult",
    "Notifier",
    "MonitorScheduler",
]
