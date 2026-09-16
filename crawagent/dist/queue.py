"""Redis 任务队列 — 入队/出队/锁/状态管理。

设计意图：
    - ZSET + priority score 实现优先级队列
    - BZPOPMIN 原子单消费者语义，天然不双发
    - running SET + worker 心跳 TTL 做 crash 恢复（requeue_stale_running）
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime

from crawagent.dist.redis_client import get_redis, _k


def enqueue_task(session_id: str, prompt: str, model: str | None = None,
                 priority: int = 0) -> str:
    """生成 task_id，HSET 元数据，ZADD 入队。

    score = priority * 1e13 + ts_ms（同优先级 FIFO，高优先级排前）。
    """
    r = get_redis()
    task_id = f"t_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat(timespec="seconds")
    now_ms = int(time.time() * 1000)

    meta = {
        "task_id": task_id,
        "session_id": session_id,
        "prompt": prompt,
        "model": model or "",
        "priority": str(priority),
        "status": "queued",
        "created_at": now,
        "started_at": "",
        "finished_at": "",
        "worker_id": "",
        "error": "",
    }
    r.hset(_k("task", task_id), mapping=meta)
    score = priority * 1e13 + now_ms
    r.zadd(_k("tasks"), {task_id: score})
    r.sadd(_k("sessions"), session_id)
    return task_id


def pop_task(timeout: float = 5.0) -> tuple[str, dict] | None:
    """BZPOPMIN 阻塞等待任务。返回 (task_id, meta_dict) 或 None。"""
    r = get_redis()
    result = r.bzpopmin(_k("tasks"), timeout=timeout)
    if result is None:
        return None
    # redis-py 返回 (key, member, score)
    _key, task_id, _score = result
    meta = r.hgetall(_k("task", task_id))
    if not meta:
        return None
    return task_id, meta


def claim_task(task_id: str, worker_id: str) -> bool:
    """worker 领取任务：HSET status=running/worker_id/started_at；SADD running。"""
    r = get_redis()
    now = datetime.now().isoformat(timespec="seconds")
    r.hset(_k("task", task_id), mapping={
        "status": "running",
        "worker_id": worker_id,
        "started_at": now,
    })
    r.sadd(_k("tasks:running"), task_id)
    return True


def finish_task(task_id: str, status: str = "done", error: str = "") -> None:
    """标记任务完成：HSET status/finished_at/error；SREM running。"""
    r = get_redis()
    now = datetime.now().isoformat(timespec="seconds")
    r.hset(_k("task", task_id), mapping={
        "status": status,
        "finished_at": now,
        "error": error,
    })
    r.srem(_k("tasks:running"), task_id)


def cancel_task(task_id: str) -> str:
    """取消任务：HSET status=cancelled。worker 心跳轮会发现。"""
    r = get_redis()
    r.hset(_k("task", task_id), mapping={"status": "cancelled"})
    r.srem(_k("tasks:running"), task_id)
    r.zrem(_k("tasks"), task_id)
    return f"已取消: {task_id}"


def get_task(task_id: str) -> dict:
    """读单个任务元数据。"""
    r = get_redis()
    return r.hgetall(_k("task", task_id)) or {}


def list_tasks(status: str | None = None, limit: int = 50) -> list[dict]:
    """列出任务（可选状态过滤）。从 running SET + tasks ZSET 合并查。"""
    r = get_redis()
    task_ids = set()

    # 从 running SET 取
    running = r.smembers(_k("tasks:running"))
    task_ids.update(running)

    # 从 ZSET 取最近的
    queued = r.zrange(_k("tasks"), 0, limit - 1)
    task_ids.update(queued)

    # 也查已完成的（ZREM 后只有 HASH 还在，扫 HASH 不现实，从 running+queued 够了）

    tasks = []
    for tid in task_ids:
        meta = r.hgetall(_k("task", tid))
        if not meta:
            continue
        if status and meta.get("status") != status:
            continue
        tasks.append(meta)

    # 按 created_at desc
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return tasks[:limit]


def requeue_stale_running() -> int:
    """扫 running SET，对 owner worker 已死的任务回队列。

    返回回队列的任务数。
    """
    r = get_redis()
    running = r.smembers(_k("tasks:running"))
    if not running:
        return 0

    requeued = 0
    now_ms = int(time.time() * 1000)
    for task_id in running:
        meta = r.hgetall(_k("task", task_id))
        if not meta:
            r.srem(_k("tasks:running"), task_id)
            continue

        # 检查 worker 心跳是否存活
        worker_id = meta.get("worker_id", "")
        if worker_id:
            # 检查 worker 心跳 key 是否存在
            if not r.exists(_k("worker", worker_id)):
                # worker 已死 → 回队列
                priority = int(meta.get("priority", "0"))
                score = priority * 1e13 + now_ms
                r.zadd(_k("tasks"), {task_id: score})
                r.hset(_k("task", task_id), mapping={
                    "status": "queued",
                    "worker_id": "",
                })
                r.srem(_k("tasks:running"), task_id)
                requeued += 1

    return requeued
