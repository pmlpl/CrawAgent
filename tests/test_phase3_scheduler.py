"""Phase 3 — cron 调度器专属测试。

覆盖：
  1. cron_should_run 解析正确性（*、*/N、range、list、weekday）
  2. register_cron_job / list_cron_jobs / delete_cron_job / toggle_job 持久化
  3. CronJob dataclass roundtrip（asdict → JSON → CronJob）
  4. add_notify_cb 回调注册
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated_schedules(tmp_path, monkeypatch):
    """每个测试独立的 schedules.json。"""
    import crawagent.scheduler.cron_scheduler as cs
    monkeypatch.setattr(cs, "_SCHEDULES_FILE", tmp_path / "schedules.json")
    cs._LOCK = cs._LOCK.__class__()  # 新锁避免与其它测试竞争
    yield
    # 清通知回调
    cs._notify_callbacks.clear()
    cs._engine_running = False


# —— 1. cron 解析 ——

@pytest.mark.parametrize("expr,dt,expected", [
    ("* * * * *", datetime(2026, 9, 2, 10, 30), True),
    ("*/15 * * * *", datetime(2026, 9, 2, 10, 0), True),
    ("*/15 * * * *", datetime(2026, 9, 2, 10, 7), False),
    ("*/15 * * * *", datetime(2026, 9, 2, 10, 15), True),
    ("0 9 * * 1", datetime(2026, 9, 7, 9, 0), True),   # 周一 9 点（2026-09-07 是周一）
    ("0 9 * * 1", datetime(2026, 9, 7, 10, 0), False),
    ("0 9 * * 1", datetime(2026, 9, 8, 9, 0), False),  # 周二 9 点不该跑
    ("30 14 * * *", datetime(2026, 9, 2, 14, 30), True),
    ("30 14 * * *", datetime(2026, 9, 2, 14, 31), False),
    ("0 */2 * * *", datetime(2026, 9, 2, 10, 0), True),
    ("0 */2 * * *", datetime(2026, 9, 2, 11, 0), False),
    ("0 0 1 * *", datetime(2026, 10, 1, 0, 0), True),
    ("0 0 1 * *", datetime(2026, 10, 2, 0, 0), False),
    ("0,30 * * * *", datetime(2026, 9, 2, 10, 0), True),
    ("0,30 * * * *", datetime(2026, 9, 2, 10, 30), True),
    ("0,30 * * * *", datetime(2026, 9, 2, 10, 15), False),
    ("10-15 * * * *", datetime(2026, 9, 2, 10, 10), True),
    ("10-15 * * * *", datetime(2026, 9, 2, 10, 16), False),
    ("invalid", datetime(2026, 9, 2, 10, 0), False),  # 字段数不对
])
def test_cron_should_run(expr, dt, expected):
    from crawagent.scheduler import cron_should_run
    assert cron_should_run(expr, dt) is expected, f"cron={expr} at={dt}"


# —— 2. 持久化 API ——

def test_register_and_list_jobs():
    from crawagent.scheduler import register_cron_job, list_cron_jobs

    j1 = register_cron_job(
        url="https://example.com/feed.xml",
        cron_expr="*/15 * * * *",
        name="Example RSS",
    )
    j2 = register_cron_job(
        url="https://news.test.com",
        cron_expr="0 */2 * * *",
        name="News every 2h",
    )

    assert j1.id != j2.id
    jobs = list_cron_jobs()
    assert len(jobs) == 2
    assert {j.url for j in jobs} == {"https://example.com/feed.xml", "https://news.test.com"}


def test_register_update_existing():
    from crawagent.scheduler import register_cron_job, list_cron_jobs

    j = register_cron_job("https://example.com", cron_expr="*/30 * * * *", name="v1")
    # 相同 url + cron → 更新
    j2 = register_cron_job("https://example.com", cron_expr="*/30 * * * *", name="v2")
    assert j.id == j2.id, "相同 url+cron 应返回同一 id"
    assert j2.name == "v2", "name 应该更新"

    jobs = list_cron_jobs()
    assert len(jobs) == 1


def test_delete_job():
    from crawagent.scheduler import register_cron_job, list_cron_jobs, delete_cron_job

    j = register_cron_job("https://x.com/feed", cron_expr="0 */1 * * *")
    assert delete_cron_job(j.id) is True
    assert len(list_cron_jobs()) == 0
    # 删不存在的
    assert delete_cron_job("nonexistent") is False


def test_toggle_job():
    from crawagent.scheduler import register_cron_job, list_cron_jobs, toggle_job

    j = register_cron_job("https://toggle.test", cron_expr="0 * * * *")
    assert j.enabled is True
    toggle_job(j.id, False)
    jobs = list_cron_jobs()
    assert jobs[0].enabled is False
    toggle_job(j.id, True)
    jobs = list_cron_jobs()
    assert jobs[0].enabled is True


def test_job_persistence_survives_reimport(tmp_path, monkeypatch):
    """注册一个 job → 重新 import cron_scheduler → list 应该还能读到。"""
    import crawagent.scheduler.cron_scheduler as cs

    monkeypatch.setattr(cs, "_SCHEDULES_FILE", tmp_path / "schedules.json")
    cs._LOCK = cs._LOCK.__class__()

    j = cs.register_cron_job("https://survive.test", cron_expr="0 0 * * *", name="survivor")

    # 模拟进程重启：新 module import + 同 schedules 文件
    cs2 = __import__("crawagent.scheduler.cron_scheduler", fromlist=["register_cron_job", "list_cron_jobs"])
    cs2._SCHEDULES_FILE = tmp_path / "schedules.json"

    jobs = cs2.list_cron_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == j.id
    assert jobs[0].name == "survivor"


# —— 3. 通知回调 ——

def test_add_notify_cb_collects(monkeypatch):
    """add_notify_cb 注册的回调应该在 _run_job_once 成功时触发。"""
    from unittest.mock import MagicMock
    import crawagent.scheduler.cron_scheduler as cs

    # 清全局回调避免污染
    cs._notify_callbacks.clear()
    received: list[tuple[str, str, str]] = []
    cs.add_notify_cb(lambda url, status, detail: received.append((url, status, detail)))

    job = cs.CronJob(
        id="testjob", name="Test", url="https://example.com",
        cron_expr="* * * * *", created_at="2026-09-01T00:00:00",
    )

    # patch requests.get 避免真发请求
    fake_resp = MagicMock()
    fake_resp.text = "<html>hello world</html>"
    monkeypatch.setattr("requests.get", lambda url, **kw: fake_resp)

    updated = cs._run_job_once(job)
    assert updated.last_status == "ok"
    assert len(received) == 1, f"callback 应触发一次，实际 {len(received)}"
    assert received[0][0] == "https://example.com"
    assert received[0][1] == "ok"
