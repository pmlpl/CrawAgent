"""监控调度器（P4-2）：APcheduler + 监控执行核心逻辑

核心流程（run_monitor_task）：
1. 抓取 URL（用 Fetcher）
2. 提取关注字段（用 CSS 选择器或 watch_fields）
3. 与基线对比（DiffDetector）
4. 若有变化 → 创建告警 + 通知（去抖：cooldown 内不重复）
5. 更新基线
6. 更新任务状态（last_run_at / consecutive_failures）

调度器：
- APScheduler 可选（缺包时降级为手动触发）
- 支持 interval / cron / once 三种调度类型
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger

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


class MonitorScheduler:
    """监控调度器 + 执行引擎

    用法：
        scheduler = MonitorScheduler()
        await scheduler.start()  # 启动调度器（若 APScheduler 可用）

        # 手动触发一次
        result = await scheduler.run_task_once(task_id)

        # 添加任务到调度器
        scheduler.schedule_task(task)
    """

    def __init__(
        self,
        store: Optional[MonitorStore] = None,
        baseline_store: Optional[BaselineStore] = None,
        detector: Optional[DiffDetector] = None,
        notifier: Optional[Notifier] = None,
    ):
        self.store = store or get_monitor_store()
        self.baseline = baseline_store or BaselineStore(store=self.store)
        self.detector = detector or DiffDetector(baseline_store=self.baseline)
        self.notifier = notifier or Notifier()
        self._scheduler = None
        self._running = False

    async def start(self):
        """启动 APScheduler 调度器（可选依赖）"""
        if self._running:
            return
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.interval import IntervalTrigger
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            logger.warning(
                "[MonitorScheduler] apscheduler 未安装，调度器未启动。"
                "请运行 pip install apscheduler 启用定时调度。"
                "仍可手动触发监控任务。"
            )
            return

        self._scheduler = AsyncIOScheduler()
        # 加载所有启用的任务
        tasks = self.store.list_tasks(enabled_only=True)
        for task in tasks:
            self._add_job_to_scheduler(task)
        self._scheduler.start()
        self._running = True
        logger.info(f"[MonitorScheduler] 已启动，加载 {len(tasks)} 个任务")

    async def stop(self):
        """停止调度器"""
        if self._scheduler and self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info("[MonitorScheduler] 已停止")

    def _add_job_to_scheduler(self, task: MonitorTask):
        """把任务加到 APScheduler"""
        if not self._scheduler:
            return
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger

        job_id = f"monitor_{task.id}"
        # 先移除已有 job（避免重复）
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass

        if task.schedule_type == ScheduleType.INTERVAL:
            trigger = IntervalTrigger(seconds=task.interval_seconds)
        elif task.schedule_type == ScheduleType.CRON and task.cron_expr:
            try:
                trigger = CronTrigger.from_crontab(task.cron_expr)
            except Exception as e:
                logger.error(f"[MonitorScheduler] cron 表达式无效: {task.cron_expr} ({e})")
                return
        else:
            # ONCE 或无 cron：不调度
            return

        self._scheduler.add_job(
            func=self._run_job_wrapper,
            trigger=trigger,
            args=[task.id],
            id=job_id,
            replace_existing=True,
        )

    def _run_job_wrapper(self, task_id: str):
        """APScheduler 调用的同步包装器（异步任务丢到事件循环）"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        if loop.is_running():
            asyncio.ensure_future(self.run_task_once(task_id), loop=loop)
        else:
            loop.run_until_complete(self.run_task_once(task_id))

    def schedule_task(self, task: MonitorTask):
        """添加/更新任务的调度"""
        if not self._scheduler or not self._running:
            logger.debug(f"[MonitorScheduler] 调度器未启动，跳过任务 {task.id} 调度")
            return
        self._add_job_to_scheduler(task)
        logger.info(f"[MonitorScheduler] 任务 {task.id} 已加入调度")

    def unschedule_task(self, task_id: str):
        """移除任务调度"""
        if not self._scheduler:
            return
        job_id = f"monitor_{task_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass

    async def run_task_once(self, task_id: str) -> Dict[str, Any]:
        """手动触发一次监控任务

        返回执行结果摘要
        """
        task = self.store.get_task(task_id)
        if not task:
            return {"success": False, "error": f"任务不存在: {task_id}"}

        logger.info(f"[Monitor] 执行任务 {task.id} ({task.name}) url={task.url}")
        task.last_run_at = time.time()

        # 1. 抓取 URL
        fetch_result = await self._fetch_url(task.url)
        if not fetch_result["success"]:
            # 抓取失败 → 记录连续失败 + 可能告警
            task.consecutive_failures += 1
            self.store.update_task(task)
            # 连续失败 3 次或 alert_on_status 开启时告警
            if task.alert_on_status or task.consecutive_failures >= 3:
                await self._maybe_alert(
                    task=task,
                    level=AlertLevel.ERROR if task.consecutive_failures >= 3 else AlertLevel.WARN,
                    title=f"监控目标不可达 ({fetch_result.get('status_code', 'N/A')})",
                    message=fetch_result["error"],
                    diff_summary="抓取失败",
                    old_value="",
                    new_value="",
                )
            return {
                "success": False,
                "error": fetch_result["error"],
                "status_code": fetch_result.get("status_code"),
            }

        # 重置连续失败计数
        task.consecutive_failures = 0

        # 2. 提取关注字段
        fields = await self._extract_fields(
            fetch_result["html"], task.watch_fields, task.css_selector
        )

        # 3. 与基线对比
        diff_result, old_baseline = self.detector.compare_with_baseline(
            task=task,
            new_content=fetch_result["html"],
            new_fields=fields,
        )

        # 4. 若有变化 → 告警
        if diff_result.has_changed and not diff_result.summary.startswith("首次运行"):
            # 字段级变化详情
            for change in diff_result.field_changes:
                level = AlertLevel.INFO
                if change.change_type == "removed" and task.alert_on_missing:
                    level = AlertLevel.WARN
                await self._maybe_alert(
                    task=task,
                    level=level,
                    title=f"监控字段变化: {change.field}",
                    message=diff_result.summary,
                    diff_summary=diff_result.summary,
                    old_value=str(change.old_value),
                    new_value=str(change.new_value),
                )
            # 整体内容变化但无字段变化 → 低级别告警
            if diff_result.content_hash_changed and not diff_result.field_changes:
                await self._maybe_alert(
                    task=task,
                    level=AlertLevel.INFO,
                    title="监控内容已更新",
                    message=diff_result.summary,
                    diff_summary=diff_result.summary,
                    old_value="",
                    new_value="",
                )

        # 5. 更新基线
        self.baseline.save_baseline(
            task_id=task.id,
            url=task.url,
            content=fetch_result["html"],
            fields=fields,
        )

        # 6. 更新任务状态
        self.store.update_task(task)

        return {
            "success": True,
            "changed": diff_result.has_changed,
            "summary": diff_result.summary,
            "fields": fields,
            "status_code": fetch_result.get("status_code"),
        }

    async def _fetch_url(self, url: str) -> Dict[str, Any]:
        """抓取 URL 内容"""
        try:
            from crawagent.core.fetcher import Fetcher
            fetcher = Fetcher(per_domain_rate=1.0)
            try:
                result = await fetcher.fetch(url)
                if result.success and result.html:
                    return {
                        "success": True,
                        "html": result.html,
                        "status_code": result.status_code,
                        "error": "",
                    }
                return {
                    "success": False,
                    "html": "",
                    "status_code": result.status_code,
                    "error": result.error or f"HTTP {result.status_code}",
                }
            finally:
                await fetcher.close()
        except Exception as e:
            return {
                "success": False,
                "html": "",
                "status_code": 0,
                "error": f"抓取异常: {type(e).__name__}: {e}",
            }

    async def _extract_fields(
        self,
        html: str,
        watch_fields: List[str],
        css_selector: str = "",
    ) -> Dict[str, Any]:
        """从 HTML 提取关注字段

        策略：
        1. 若有 css_selector → 提取该元素的文本
        2. 若有 watch_fields → 用 JsonLd/Meta 提取
        3. 默认：计算页面正文 hash
        """
        fields: Dict[str, Any] = {}
        if not html:
            return fields

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # CSS 选择器
            if css_selector:
                elements = soup.select(css_selector)
                if elements:
                    texts = [e.get_text(strip=True) for e in elements]
                    fields["selector_text"] = texts[0] if len(texts) == 1 else texts
                else:
                    fields["selector_text"] = ""

            # watch_fields：从 JSON-LD / Meta / 文本搜索
            if watch_fields:
                # JSON-LD
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        import json
                        data = json.loads(script.string or "{}")
                        if isinstance(data, dict):
                            for fname in watch_fields:
                                if fname in data:
                                    fields[fname] = data[fname]
                    except Exception:
                        pass

                # Meta 标签
                for fname in watch_fields:
                    if fname not in fields:
                        meta = soup.find("meta", attrs={"name": fname.lower()})
                        if not meta:
                            meta = soup.find("meta", attrs={"property": fname.lower()})
                        if meta:
                            fields[fname] = meta.get("content", "")

                # 价格常见模式（price / amount / 价格）
                for fname in watch_fields:
                    if fname.lower() in ("price", "amount", "价格") and fname not in fields:
                        # 找 class 含 price 的元素
                        price_el = soup.select_one(".price, [class*='price'], [data-price]")
                        if price_el:
                            fields[fname] = price_el.get_text(strip=True)

            # 无字段提取时，记录整体内容 hash（用于检测任何变化）
            if not fields and not css_selector:
                # 取 body 文本作为整体内容
                body = soup.body or soup
                text = body.get_text(strip=True)
                fields["content_length"] = len(text)
                fields["content_preview"] = text[:200]

        except Exception as e:
            logger.debug(f"[Monitor] 字段提取失败: {e}")
            fields["error"] = str(e)

        return fields

    async def _maybe_alert(
        self,
        task: MonitorTask,
        level: AlertLevel,
        title: str,
        message: str,
        diff_summary: str,
        old_value: str,
        new_value: str,
    ) -> Optional[AlertRecord]:
        """创建告警（带去抖）+ 发送通知

        去抖逻辑：
        - 若 task.last_alert_at 存在且距上次告警 < task.alert_cooldown，跳过
        - 验收标准：503 持续 10 分钟，cooldown=3600s 时只触发一次
        """
        now = time.time()
        if task.last_alert_at is not None:
            elapsed = now - task.last_alert_at
            if elapsed < task.alert_cooldown:
                logger.debug(
                    f"[Monitor] 去抖：任务 {task.id} 距上次告警 {int(elapsed)}s "
                    f"< cooldown {task.alert_cooldown}s，跳过"
                )
                return None

        alert = AlertRecord(
            task_id=task.id,
            level=level,
            title=title,
            message=message,
            url=task.url,
            diff_summary=diff_summary,
            old_value=old_value,
            new_value=new_value,
        )
        self.store.create_alert(alert)

        # 发送通知
        notify_result = await self.notifier.notify(task, alert)
        alert.notified = notify_result["success"]
        alert.notify_error = notify_result.get("error", "")

        # 更新任务 last_alert_at
        task.last_alert_at = now
        self.store.update_task(task)

        logger.info(
            f"[Monitor] 告警已创建 task={task.id} level={level} "
            f"title={title} notified={alert.notified}"
        )
        return alert


# 单例
_scheduler: Optional[MonitorScheduler] = None


def get_scheduler() -> MonitorScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MonitorScheduler()
    return _scheduler
