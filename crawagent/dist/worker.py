"""Worker 进程 — 从 Redis 队列领取任务并执行，带心跳/注册/注销。

设计意图：
    - worker_main() 是 CLI 入口的主循环
    - 心跳线程每 N 秒 SET crawagent:worker:{wid} EX ttl
    - BZPOPMIN pop_task → claim_task → run_turn(emit=publish_event) → finish_task
    - SIGINT/SIGTERM 优雅退出
"""
from __future__ import annotations

import os
import signal
import socket
import threading
import time
import uuid

from crawagent.config.settings import get_settings
from crawagent.dist.redis_client import get_redis, _k
from crawagent.dist.queue import pop_task, claim_task, finish_task, cancel_task
from crawagent.dist.pubsub import publish_event, save_task_metrics


def _gen_worker_id() -> str:
    """生成 worker ID：w_{host}_{pid}_{uuid8}"""
    host = socket.gethostname() or "unknown"
    pid = os.getpid()
    uid = uuid.uuid4().hex[:8]
    return f"w_{host}_{pid}_{uid}"


def register_worker(worker_id: str) -> None:
    """注册 worker 到 Redis：SADD workers + HSET worker info。"""
    r = get_redis()
    r.sadd(_k("workers"), worker_id)
    r.hset(_k("worker", worker_id), mapping={
        "status": "idle",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "current_task": "",
        "last_heartbeat": str(time.time()),
    })


def unregister_worker(worker_id: str) -> None:
    """注销 worker：SREM workers + DEL worker key。"""
    r = get_redis()
    r.srem(_k("workers"), worker_id)
    r.delete(_k("worker", worker_id))


def heartbeat_loop(worker_id: str, stop_event: threading.Event) -> None:
    """心跳线程：每 N 秒刷新 worker key TTL。

    超过 2×ttl 无心跳 → worker 视为死亡，任务回队列。
    """
    settings = get_settings()
    interval = settings.dist_worker_heartbeat_interval
    ttl = settings.dist_worker_heartbeat_ttl

    while not stop_event.is_set():
        r = get_redis()
        r.hset(_k("worker", worker_id), mapping={
            "last_heartbeat": str(time.time()),
        })
        r.expire(_k("worker", worker_id), ttl)
        stop_event.wait(interval)


def list_workers() -> list[dict]:
    """列出所有在线 worker。"""
    r = get_redis()
    wids = r.smembers(_k("workers"))
    workers = []
    for wid in wids:
        info = r.hgetall(_k("worker", wid))
        if not info:
            # 心跳过期但仍在 workers SET → 标记 dead
            info = {"worker_id": wid, "status": "dead"}
        info["worker_id"] = wid
        workers.append(info)
    return workers


def get_worker(worker_id: str) -> dict:
    """读单个 worker 信息。"""
    r = get_redis()
    info = r.hgetall(_k("worker", worker_id))
    info["worker_id"] = worker_id
    return info


def worker_main(stop_event=None, max_tasks: int = 0) -> None:
    """Worker 主循环。

    参数：
        stop_event: 外部 threading.Event，set() 后优雅退出
        max_tasks: 最多跑多少个任务后退出（0 = 无限）
    """
    import threading as _t
    if stop_event is None:
        stop_event = _t.Event()

    # 信号处理
    def _signal_handler(signum, frame):
        stop_event.set()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    worker_id = _gen_worker_id()
    register_worker(worker_id)
    print(f"[worker] {worker_id} started, waiting for tasks...")

    # 起心跳线程
    hb_thread = _t.Thread(target=heartbeat_loop, args=(worker_id, stop_event), daemon=True)
    hb_thread.start()

    tasks_done = 0
    settings = get_settings()
    sweep_interval = settings.dist_task_sweep_interval

    try:
        while not stop_event.is_set():
            # 检查任务是否被取消（不阻塞太久）
            task_data = pop_task(timeout=sweep_interval)
            if task_data is None:
                continue

            task_id, meta = task_data

            # 检查是否已取消
            if meta.get("status") == "cancelled":
                continue

            claim_task(task_id, worker_id)
            print(f"[worker] {worker_id} claimed task {task_id} (session={meta.get('session_id','')})")

            # 更新 worker 状态
            r = get_redis()
            r.hset(_k("worker", worker_id), mapping={
                "status": "busy",
                "current_task": task_id,
            })

            # 执行任务
            try:
                # 延迟导入避免循环依赖
                from crawagent.dist.runner import run_turn

                def emit(event: dict) -> None:
                    publish_event(task_id, event, worker_id=worker_id)

                session_id = meta.get("session_id", "")
                prompt = meta.get("prompt", "")
                model = meta.get("model") or None

                # 发送开始事件
                publish_event(task_id, {
                    "type": "status",
                    "line": f"worker {worker_id} 开始执行任务",
                }, worker_id=worker_id)

                run_turn(
                    session_id=session_id,
                    text=prompt,
                    model=model,
                    emit=emit,
                    task_id=task_id,
                )

                finish_task(task_id, status="done")
                publish_event(task_id, {
                    "type": "done",
                    "task_id": task_id,
                }, worker_id=worker_id)

            except Exception as e:
                finish_task(task_id, status="error", error=str(e))
                publish_event(task_id, {
                    "type": "error",
                    "error": str(e),
                }, worker_id=worker_id)
                print(f"[worker] task {task_id} failed: {e}")

            finally:
                # 恢复 idle
                r = get_redis()
                r.hset(_k("worker", worker_id), mapping={
                    "status": "idle",
                    "current_task": "",
                })

            tasks_done += 1
            if max_tasks > 0 and tasks_done >= max_tasks:
                print(f"[worker] {worker_id} reached max_tasks={max_tasks}, exiting")
                break

    finally:
        unregister_worker(worker_id)
        print(f"[worker] {worker_id} stopped (tasks_done={tasks_done})")
