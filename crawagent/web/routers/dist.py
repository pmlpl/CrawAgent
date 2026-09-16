"""分布式 REST + WS 路由 — 任务管理 + worker 监控 + 实时事件流。

路由清单：
    POST   /api/dist/tasks              提交任务入队
    GET    /api/dist/tasks               任务列表（含 status 过滤）
    GET    /api/dist/tasks/{task_id}     单任务详情 + metrics
    DELETE /api/dist/tasks/{task_id}    取消任务
    GET    /api/dist/workers             worker 列表
    GET    /api/dist/workers/{wid}       单 worker
    WS     /ws/dist/{task_id}            实时事件流（先 replay 再 subscribe）
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from crawagent.dist.queue import (
    enqueue_task, get_task, list_tasks, cancel_task,
)
from crawagent.dist.worker import list_workers, get_worker
from crawagent.dist.pubsub import replay_events, subscribe, get_task_metrics

router = APIRouter()


class TaskCreateRequest(BaseModel):
    """提交任务请求体。"""
    session_id: str
    prompt: str
    model: str = ""
    priority: int = 0


@router.post("/api/dist/tasks")
async def create_dist_task(req: TaskCreateRequest):
    """提交任务入队。"""
    task_id = enqueue_task(
        session_id=req.session_id,
        prompt=req.prompt,
        model=req.model or None,
        priority=req.priority,
    )
    return {"task_id": task_id, "status": "queued"}


@router.get("/api/dist/tasks")
async def get_dist_tasks(status: str | None = None, limit: int = 50):
    """任务列表（可选 status 过滤）。"""
    tasks = list_tasks(status=status, limit=limit)
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/api/dist/tasks/{task_id}")
async def get_dist_task_detail(task_id: str):
    """单任务详情 + metrics。"""
    meta = get_task(task_id)
    if not meta:
        return {"error": "NOT_FOUND", "task_id": task_id}
    metrics = get_task_metrics(task_id)
    return {"task": meta, "metrics": metrics}


@router.delete("/api/dist/tasks/{task_id}")
async def cancel_dist_task(task_id: str):
    """取消任务。"""
    result = cancel_task(task_id)
    return {"task_id": task_id, "result": result}


@router.get("/api/dist/workers")
async def get_dist_workers():
    """worker 列表。"""
    workers = list_workers()
    return {"workers": workers, "count": len(workers)}


@router.get("/api/dist/workers/{worker_id}")
async def get_dist_worker_detail(worker_id: str):
    """单 worker。"""
    worker = get_worker(worker_id)
    return {"worker": worker}


@router.websocket("/ws/dist/{task_id}")
async def task_event_ws(ws: WebSocket, task_id: str):
    """实时任务事件流。

    连接后先 replay_events 补齐历史事件，再 subscribe pubsub 透传实时事件。
    任务结束（done/error）后自动关闭连接。
    """
    await ws.accept()

    # 1. 先补齐历史事件
    events = replay_events(task_id, cursor=0)
    for ev in events:
        await ws.send_text(json.dumps(ev, ensure_ascii=False, default=str))
        if ev.get("type") in ("done", "error"):
            await ws.close()
            return

    # 检查任务是否已结束
    meta = get_task(task_id)
    if meta.get("status") in ("done", "error", "cancelled"):
        await ws.send_text(json.dumps({"type": "done", "task_id": task_id}))
        await ws.close()
        return

    # 2. 订阅实时事件
    pubsub = subscribe(task_id)
    try:
        while True:
            message = pubsub.get_message(timeout=0.5)
            if message and message.get("type") == "message":
                data = message.get("data", "")
                await ws.send_text(data)
                try:
                    ev = json.loads(data)
                    if ev.get("type") in ("done", "error"):
                        await ws.close()
                        return
                except (json.JSONDecodeError, TypeError):
                    pass

            # 检查 WebSocket 是否已断开
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break

    except WebSocketDisconnect:
        pass
    finally:
        try:
            pubsub.close()
        except Exception:
            pass
