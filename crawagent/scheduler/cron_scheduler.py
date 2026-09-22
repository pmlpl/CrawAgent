"""核心实现 — 见 crawagent.scheduler 文档。"""
import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from crawagent.config.settings import get_settings


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------

_SCHEDULES_FILE: Path | None = None
_LOCK = threading.Lock()


def _schedules_path() -> Path:
    global _SCHEDULES_FILE
    if _SCHEDULES_FILE is None:
        _SCHEDULES_FILE = get_settings().project_root / "data" / "schedules.json"
    return _SCHEDULES_FILE


def _load() -> dict:
    p = _schedules_path()
    if not p.exists():
        return {"jobs": [], "last_run": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"jobs": [], "last_run": {}}


def _save(data: dict) -> None:
    p = _schedules_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class CronJob:
    """一个定时抓取任务配置。"""

    id: str                         # 唯一标识（hash(url|cron_expr)）
    name: str                       # 人类可读名称
    url: str                        # 要爬取的 URL
    cron_expr: str                  # 5 字段 cron
    enabled: bool = True            # 总开关
    notify_urls: list[str] = field(default_factory=list)
    last_run_at: str = ""           # ISO 时间
    last_content_hash: str = ""     # 上次内容 hash（变化检测）
    last_status: str = ""           # ok / error / no_change
    created_at: str = ""


def _job_id(url: str, cron_expr: str) -> str:
    return hashlib.md5(f"{url}|{cron_expr}".encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# 简化 cron 解析
# ---------------------------------------------------------------------------

def _parse_cron_field(expr: str, min_val: int, max_val: int) -> set[int]:
    """解析单个 cron 字段，返回所有匹配值集合。"""
    result: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if part == "*":
            result.update(range(min_val, max_val + 1))
            continue
        if part.startswith("*/"):
            step = int(part[2:])
            result.update(range(min_val, max_val + 1, max(1, step)))
            continue
        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str)
            if "-" in range_part:
                lo, hi = range_part.split("-", 1)
                result.update(range(int(lo), int(hi) + 1, max(1, step)))
            else:
                result.update(range(int(range_part), max_val + 1, max(1, step)))
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            result.update(range(int(lo), int(hi) + 1))
            continue
        result.add(int(part))
    return result


def cron_should_run(expr: str, now: datetime | None = None) -> bool:
    """判断 cron 表达式在给定时间点是否触发（分钟粒度）。

    weekday 兼容两种约定：cron 标准 (0-6, 0=周日) + (0-7 都=周日)。
    """
    now = now or datetime.now()
    fields = expr.strip().split()
    if len(fields) != 5:
        return False

    minute_set = _parse_cron_field(fields[0], 0, 59)
    hour_set = _parse_cron_field(fields[1], 0, 23)
    day_set = _parse_cron_field(fields[2], 1, 31)
    month_set = _parse_cron_field(fields[3], 1, 12)
    weekday_set = _parse_cron_field(fields[4], 0, 7)

    py_wd = now.weekday()  # 0=Mon..6=Sun
    cron_wd = (py_wd + 1) % 7  # 0=Sun..6=Sat
    cron_wd_set = {cron_wd}
    cron_wd_set.add(7 if cron_wd == 0 else cron_wd)

    if minute_set and now.minute not in minute_set:
        return False
    if hour_set and now.hour not in hour_set:
        return False
    if day_set and now.day not in day_set:
        return False
    if month_set and now.month not in month_set:
        return False
    if weekday_set.isdisjoint(cron_wd_set):
        return False
    return True


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def register_cron_job(
    url: str,
    cron_expr: str,
    name: str = "",
    notify_urls: list[str] | None = None,
) -> CronJob:
    """注册或更新一个定时抓取任务。"""
    job_id = _job_id(url, cron_expr)
    with _LOCK:
        data = _load()
        existing = None
        for j in data["jobs"]:
            if j["id"] == job_id:
                existing = CronJob(**j)
                break

        now = datetime.now().isoformat(timespec="seconds")
        if existing:
            if name:
                existing.name = name
            if notify_urls is not None:
                existing.notify_urls = list(notify_urls)
            existing.enabled = True
            result = existing
        else:
            result = CronJob(
                id=job_id,
                name=name or url,
                url=url,
                cron_expr=cron_expr,
                notify_urls=list(notify_urls or []),
                created_at=now,
            )

        data["last_run"][job_id] = now
        # data["jobs"] 里存的是 dict（JSON 格式），filter 后直接保留
        data["jobs"] = [j for j in data["jobs"] if j["id"] != job_id]
        data["jobs"].append(asdict(result))
        _save(data)
        return result


def list_cron_jobs() -> list[CronJob]:
    """读取所有定时任务（按存储顺序）。

    Returns:
        ``CronJob`` 列表；空列表表示无任务。
    """
    with _LOCK:
        data = _load()
    return [CronJob(**j) for j in data["jobs"]]


def delete_cron_job(job_id: str) -> bool:
    """删除指定 ID 的定时任务（含 ``last_run`` 记录）。

    Args:
        job_id: 任务 ID。

    Returns:
        True = 实际删除了任务；False = ID 不存在。
    """
    with _LOCK:
        data = _load()
        before = len(data["jobs"])
        data["jobs"] = [j for j in data["jobs"] if j["id"] != job_id]
        data["last_run"].pop(job_id, None)
        _save(data)
        return len(data["jobs"]) < before


def toggle_job(job_id: str, enabled: bool) -> bool:
    """切换任务的启用位（仅状态实际变化时写盘）。

    Args:
        job_id: 任务 ID。
        enabled: 期望启用状态。

    Returns:
        True = 状态实际切换；False = 状态未变或 ID 不存在。
    """
    with _LOCK:
        data = _load()
        changed = False
        for j in data["jobs"]:
            if j["id"] == job_id and j["enabled"] != enabled:
                j["enabled"] = enabled
                changed = True
        if changed:
            _save(data)
        return changed


# ---------------------------------------------------------------------------
# 执行引擎
# ---------------------------------------------------------------------------

_notify_callbacks: list[Callable[[str, str, str], None]] = []
_engine_running = False


def add_notify_cb(cb: Callable[[str, str, str], None]) -> None:
    """注册通知回调：(url, status, detail). status = ok / error / no_change."""
    _notify_callbacks.append(cb)


def _run_job_once(job: CronJob) -> CronJob:
    from crawagent.tools._http import http_get

    try:
        content = http_get(job.url, timeout=30, headers={"User-Agent": "CrawAgent-Scheduler/1.0"})
        new_hash = hashlib.sha256(content.encode()).hexdigest()

        now = datetime.now().isoformat(timespec="seconds")
        job.last_run_at = now

        if job.last_content_hash and new_hash == job.last_content_hash:
            job.last_status = "no_change"
        else:
            job.last_content_hash = new_hash
            job.last_status = "ok"
            for cb in _notify_callbacks:
                try:
                    cb(job.url, "ok", f"content changed (hash={new_hash[:16]})")
                except Exception:
                    pass
    except Exception as e:
        job.last_status = f"error: {e}"
        for cb in _notify_callbacks:
            try:
                cb(job.url, "error", str(e))
            except Exception:
                pass

    return job


def _engine_loop() -> None:
    """后台守护线程：每分钟 tick 一次。"""
    global _engine_running
    _engine_running = True
    print("[cron_scheduler] engine started")

    while _engine_running:
        now = datetime.now()
        current_minute = now.replace(second=0, microsecond=0)

        try:
            with _LOCK:
                data = _load()
                jobs = [CronJob(**j) for j in data["jobs"] if j.get("enabled", True)]

            for job in jobs:
                last_run_str = data.get("last_run", {}).get(job.id, "")
                if last_run_str:
                    try:
                        last_run = datetime.fromisoformat(last_run_str)
                        if (now - last_run).total_seconds() < 55:
                            continue
                    except ValueError:
                        pass

                if cron_should_run(job.cron_expr, current_minute):
                    updated = _run_job_once(job)
                    with _LOCK:
                        d = _load()
                        d["last_run"][job.id] = now.isoformat(timespec="seconds")
                        for j in d["jobs"]:
                            if j["id"] == job.id:
                                j["last_run_at"] = updated.last_run_at
                                j["last_content_hash"] = updated.last_content_hash
                                j["last_status"] = updated.last_status
                        _save(d)
        except Exception as e:
            print(f"[cron_scheduler] tick error: {e}")

        now = datetime.now()
        sleep_secs = 61 - (now.second + now.microsecond / 1_000_000)
        try:
            time.sleep(max(1.0, sleep_secs))
        except Exception:
            time.sleep(30)


_engine_thread: threading.Thread | None = None


def start_engine() -> None:
    """启动 cron 调度引擎线程（幂等）。

    多次调用仅第一次实际起线程；后续调用立即返回。线程为 daemon，主进程退出时自动结束。
    """
    if _engine_running:
        return
    _engine_thread = threading.Thread(target=_engine_loop, name="cron_scheduler", daemon=True)
    _engine_thread.start()


def stop_engine() -> None:
    """停止 cron 调度引擎（标志位清零，下一轮 ``_engine_loop`` 检测后退出）。

    线程本身不强制 join — daemon 线程会在主进程退出时自动结束。
    """
    global _engine_running
    _engine_running = False
