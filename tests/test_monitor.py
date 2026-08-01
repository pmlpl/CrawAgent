"""P4 监控验收测试：存储 / 变化检测 / 告警去抖。"""

import time

import pytest

from crawagent.monitor.baseline import BaselineStore
from crawagent.monitor.diff_detector import DiffDetector
from crawagent.monitor.models import (
    AlertLevel,
    AlertRecord,
    MonitorStore,
    MonitorTask,
    ScheduleType,
)
from crawagent.monitor.notifier import Notifier
from crawagent.monitor.scheduler import MonitorScheduler


def _store(tmp_path) -> MonitorStore:
    return MonitorStore(db_path=str(tmp_path / "monitor.db"))


def test_store_task_crud(tmp_path):
    store = _store(tmp_path)
    task = MonitorTask(name="苹果价格", url="https://www.apple.com/shop/buy-iphone",
                       watch_fields=["price"], interval_seconds=21600)
    store.create_task(task)
    got = store.get_task(task.id)
    assert got is not None and got.name == "苹果价格"
    assert got.watch_fields == ["price"]
    assert len(store.list_tasks()) == 1
    task.name = "改名"
    assert store.update_task(task)
    assert store.get_task(task.id).name == "改名"
    assert store.delete_task(task.id)
    assert store.get_task(task.id) is None


def test_store_baseline_and_alerts(tmp_path):
    store = _store(tmp_path)
    task = MonitorTask(url="https://example.com")
    store.create_task(task)
    store.save_baseline(task.id, task.url, "hash1", {"price": "7999"})
    bl = store.get_baseline(task.id, task.url)
    assert bl["content_hash"] == "hash1"
    assert bl["fields"]["price"] == "7999"
    # upsert
    store.save_baseline(task.id, task.url, "hash2", {"price": "8999"})
    assert store.get_baseline(task.id, task.url)["content_hash"] == "hash2"

    alert = AlertRecord(task_id=task.id, level=AlertLevel.WARN, title="价格变化")
    store.create_alert(alert)
    alerts = store.list_alerts(task_id=task.id)
    assert len(alerts) == 1 and alerts[0].title == "价格变化"


def test_diff_detector_compare():
    detector = DiffDetector()
    r = detector.compare("a", "b", {"price": "1"}, {"price": "2"})
    assert r.has_changed and r.content_hash_changed
    assert len(r.field_changes) == 1
    assert r.field_changes[0].change_type == "changed"
    assert r.field_changes[0].old_value == "1" and r.field_changes[0].new_value == "2"

    r2 = detector.compare("a", "a", {"price": "1"}, {"price": "1"}, watch_fields=["price"])
    assert not r2.has_changed

    r3 = detector.compare("a", "a", {"price": "1"}, {}, watch_fields=["price"])
    assert r3.field_changes[0].change_type == "removed"


def test_diff_compare_with_baseline_first_run(tmp_path):
    store = _store(tmp_path)
    baseline = BaselineStore(store=store)
    detector = DiffDetector(baseline_store=baseline)
    task = MonitorTask(url="https://example.com", watch_fields=["price"])
    store.create_task(task)
    result, old = detector.compare_with_baseline(task, "<html>v1</html>", {"price": "100"})
    assert old is None and not result.has_changed
    assert result.summary.startswith("首次运行")
    baseline.save_baseline(task.id, task.url, "<html>v1</html>", {"price": "100"})
    result2, old2 = detector.compare_with_baseline(task, "<html>v2</html>", {"price": "200"})
    assert result2.has_changed
    assert any(c.field == "price" for c in result2.field_changes)


def test_monitor_cooldown_503_alerts_once(tmp_path, monkeypatch):
    """验收：503 持续失败，cooldown 内只告警一次。"""
    store = _store(tmp_path)
    baseline = BaselineStore(store=store)
    scheduler = MonitorScheduler(store=store, baseline_store=baseline)

    async def fake_fetch(url):
        return {"success": False, "html": "", "status_code": 503, "error": "HTTP 503"}

    monkeypatch.setattr(scheduler, "_fetch_url", fake_fetch)

    task = MonitorTask(url="https://httpbin.org/status/503", alert_cooldown=3600)
    store.create_task(task)

    r1 = awaitable(scheduler.run_task_once(task.id))
    assert not r1["success"]
    assert len(store.list_alerts(task_id=task.id)) == 1

    # 冷却期内再次触发 → 不产生新告警
    r2 = awaitable(scheduler.run_task_once(task.id))
    assert not r2["success"]
    assert len(store.list_alerts(task_id=task.id)) == 1

    # 模拟 10 分钟后（回拨 last_alert_at）→ 再次告警
    t = store.get_task(task.id)
    t.last_alert_at = time.time() - 7200
    store.update_task(t)
    r3 = awaitable(scheduler.run_task_once(task.id))
    assert not r3["success"]
    assert len(store.list_alerts(task_id=task.id)) == 2


def test_monitor_field_change_alert(tmp_path, monkeypatch):
    store = _store(tmp_path)
    baseline = BaselineStore(store=store)
    scheduler = MonitorScheduler(store=store, baseline_store=baseline)
    html1 = '<html><body><span class="price">7999</span></body></html>'
    html2 = '<html><body><span class="price">8999</span></body></html>'
    state = {"html": html1}

    async def fake_fetch(url):
        return {"success": True, "html": state["html"], "status_code": 200, "error": ""}

    monkeypatch.setattr(scheduler, "_fetch_url", fake_fetch)
    task = MonitorTask(url="https://example.com", watch_fields=["price"], alert_cooldown=0)
    store.create_task(task)

    awaitable(scheduler.run_task_once(task.id))  # 首次建立基线
    assert len(store.list_alerts(task_id=task.id)) == 0
    state["html"] = html2
    res = awaitable(scheduler.run_task_once(task.id))
    assert res["changed"]
    alerts = store.list_alerts(task_id=task.id)
    assert len(alerts) == 1
    assert "price" in alerts[0].diff_summary or alerts[0].title == "监控字段变化: price"


def test_notifier_payload():
    task = MonitorTask(url="https://example.com", name="监控")
    alert = AlertRecord(task_id=task.id, title="价格变化", message="7999→8999")
    payload = Notifier()._build_webhook_payload(task, alert)
    assert payload["event"] == "monitor_alert"
    assert payload["url"] == "https://example.com"
    assert payload["new_value"] == ""


def test_scheduler_start_stop(tmp_path):
    import asyncio

    async def run():
        store = _store(tmp_path)
        scheduler = MonitorScheduler(store=store)
        task = MonitorTask(url="https://example.com", schedule_type=ScheduleType.INTERVAL, interval_seconds=3600)
        store.create_task(task)
        await scheduler.start()
        assert scheduler._running
        await scheduler.stop()
        assert not scheduler._running

    asyncio.run(run())


def test_monitor_lane_parallel(tmp_path):
    """P4-1：monitor lane 并行执行多个监控任务。"""
    import asyncio
    from crawagent.monitor.monitor_lane import MonitorLane

    store = _store(tmp_path)
    t1 = MonitorTask(url="https://example.com/1")
    t2 = MonitorTask(url="https://example.com/2")
    store.create_task(t1)
    store.create_task(t2)

    lane = MonitorLane(store=store, lane_name="monitor")

    async def fake_run_once(task_id):
        return {"success": True, "changed": task_id == t1.id}

    lane.scheduler.run_task_once = fake_run_once
    summary = awaitable(lane.run_parallel([t1.id, t2.id]))
    assert summary[t1.id] == "changed"
    assert summary[t2.id] == "ok"


def test_monitor_lane_creates_session_lane(tmp_path):
    """P4-1：绑定 harness session 时自动创建同名 lane。"""
    import asyncio
    from crawagent.monitor.monitor_lane import MonitorLane

    created = []

    class FakeSession:
        async def get_lane(self, name):
            return None

        async def create_lane(self, name):
            created.append(name)
            return {"name": name}

    lane = MonitorLane(store=_store(tmp_path), session=FakeSession())
    asyncio.run(lane.ensure_lane())
    assert created == ["monitor"]


def awaitable(coro):
    import asyncio
    return asyncio.run(coro)
