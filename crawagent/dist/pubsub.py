"""事件发布 + 订阅 — worker 向前端推送任务进度。

设计意图：
    - worker 执行任务时通过 publish_event 推送工具调用/AI 输出/状态
    - 前端 WS 连接后先 replay_events 补齐历史，再 subscribe 实时接收
    - milestone 类事件额外存一份精简列表（列表视图不爆）
"""
from __future__ import annotations

import json

from crawagent.config.settings import get_settings
from crawagent.dist.redis_client import get_redis, _k


def publish_event(task_id: str, event: dict, worker_id: str = "") -> None:
    """推送事件：RPUSH events list + PUBLISH pubsub channel。

    milestone 类事件额外 RPUSH milestones list（cap 200）。
    events list cap 由 settings.dist_task_event_log_max 控制（默认 1000）。
    """
    r = get_redis()
    settings = get_settings()
    event_max = settings.dist_task_event_log_max

    # 序列化（确保 JSON 兼容）
    try:
        event_str = json.dumps(event, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        event_str = json.dumps({"type": "error", "msg": "event serialize failed"}, ensure_ascii=False)

    events_key = _k("task", task_id, "events")
    channel = _k("task:events", task_id)

    r.rpush(events_key, event_str)
    r.ltrim(events_key, -event_max, -1)  # 只保留最新 N 条
    r.publish(channel, event_str)

    # milestone 事件额外存
    if event.get("type") in ("done", "error", "status", "tool_call", "tool_result"):
        milestones_key = _k("task", task_id, "milestones")
        r.rpush(milestones_key, event_str)
        r.ltrim(milestones_key, -200, -1)


def replay_events(task_id: str, cursor: int = 0) -> list[dict]:
    """读 events list 从 cursor 到最新，供 WS 订阅时追赶历史。

    返回 event dict 列表。
    """
    r = get_redis()
    events_key = _k("task", task_id, "events")
    raw = r.lrange(events_key, cursor, -1)
    events = []
    for item in raw:
        try:
            events.append(json.loads(item))
        except (json.JSONDecodeError, TypeError):
            continue
    return events


def subscribe(task_id: str):
    """订阅任务事件 PubSub channel。

    返回 redis.client.PubSub 对象，调用方 .get_message() 接收。
    """
    r = get_redis()
    pubsub = r.pubsub()
    pubsub.subscribe(_k("task:events", task_id))
    return pubsub


def save_task_metrics(task_id: str, metrics: dict) -> None:
    """保存任务运行指标（turn_count/tool_count/tokens 等）。"""
    r = get_redis()
    # 全部转 str（Redis HASH 值必须是 str）
    str_metrics = {k: str(v) for k, v in metrics.items()}
    r.hset(_k("task", task_id, "metrics"), mapping=str_metrics)


def get_task_metrics(task_id: str) -> dict:
    """读任务运行指标。"""
    r = get_redis()
    return r.hgetall(_k("task", task_id, "metrics")) or {}
