"""监控 Lane（P4-1）：与 main lane 并行运行的监控任务编排。

设计（对应 DEVELOPMENT_PLAN P4-1）：
- 每个 MonitorLane 实例绑定一个命名 lane（默认 "monitor"）
- 可绑定 harness session（在 session 中创建同名 lane，共享对话树/状态）
- run_parallel：同一 lane 内并行执行多个监控任务，互不干扰
- 调度：start() 后由 MonitorScheduler 按 interval/cron 触发

用法：
    lane = MonitorLane()
    result = await lane.run_parallel(["task1", "task2"])
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from crawagent.monitor.models import MonitorStore, get_monitor_store
from crawagent.monitor.scheduler import MonitorScheduler


class MonitorLane:
    """监控 lane：承载监控任务，与 main lane 并行。"""

    def __init__(
        self,
        store: Optional[MonitorStore] = None,
        scheduler: Optional[MonitorScheduler] = None,
        lane_name: str = "monitor",
        session: Any = None,
    ):
        self.store = store or get_monitor_store()
        self.scheduler = scheduler or MonitorScheduler(store=self.store)
        self.lane_name = lane_name
        self.session = session  # 可选：harness CrawlSession，用于共享 lane 状态
        self._running = False
        self._last_results: Dict[str, Dict[str, Any]] = {}

    async def ensure_lane(self) -> Optional[Any]:
        """在 harness session 中创建/确认本 lane 存在。"""
        if self.session is None:
            return None
        try:
            lane = await self.session.get_lane(self.lane_name)
            if lane is None:
                await self.session.create_lane(self.lane_name)
            return lane
        except Exception as e:
            logger.debug(f"[MonitorLane] 创建 lane 失败: {e}")
            return None

    async def run_once(self, task_id: str) -> Dict[str, Any]:
        """在当前 lane 上下文执行一次监控任务。"""
        await self.ensure_lane()
        result = await self.scheduler.run_task_once(task_id)
        self._last_results[task_id] = result
        logger.info(f"[MonitorLane] task={task_id} 执行完成 changed={result.get('changed')}")
        return result

    async def run_parallel(self, task_ids: List[str]) -> Dict[str, str]:
        """并行执行多个监控任务（同一 lane，互不干扰）。"""
        await self.ensure_lane()
        results = await asyncio.gather(
            *(self.run_once(tid) for tid in task_ids),
            return_exceptions=True,
        )
        summary: Dict[str, str] = {}
        for tid, res in zip(task_ids, results):
            if isinstance(res, Exception):
                summary[tid] = f"error: {res}"
            else:
                summary[tid] = "changed" if res.get("changed") else "ok"
        return summary

    async def start(self) -> None:
        """启动调度（APScheduler 可用时按任务配置定时触发）。"""
        await self.ensure_lane()
        await self.scheduler.start()
        self._running = self.scheduler._running

    async def stop(self) -> None:
        """停止调度。"""
        await self.scheduler.stop()
        self._running = False

    @property
    def last_results(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._last_results)


def get_monitor_lane() -> MonitorLane:
    """获取全局 MonitorLane 单例。"""
    global _lane
    if _lane is None:
        _lane = MonitorLane()
    return _lane


_lane: Optional[MonitorLane] = None
