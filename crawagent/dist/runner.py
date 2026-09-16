"""通用轮次执行体 — 从 turn_engine._run_turn 桥接，emit 参数化输出。

设计意图：
    - 零侵入热路径：不修改 turn_engine.py 的任何代码
    - worker 进程通过 run_turn() 调用 _run_turn，用线程安全 queue + emit 回调桥接
    - _run_turn 内部的 _emit(session_id, q, loop, event) 仍然正常工作：
      _emit 做 loop.call_soon_threadsafe(q.put_nowait, event)，
      我们在另一个线程从 queue 消费事件并调 emit 回调
    - task_id 非 None 时额外走 Redis metrics 快照

事件协议与 turn_engine 一致：
    tool_call/tool_result/ai_delta/ai_done/ai/ai_thinking/ai_thinking_delta/ai_thinking_done/status/done/error
"""
from __future__ import annotations

import asyncio
import queue as _thread_queue
import threading
import time
from typing import Callable

from crawagent.config.settings import get_settings


def run_turn(session_id: str, text: str, model: str | None,
             emit: Callable[[dict], None], *, task_id: str | None = None) -> None:
    """执行一轮对话，把流式事件通过 emit 回调推送。

    参数：
        session_id: 会话 ID（LangGraph thread_id）
        text:       用户输入文本
        model:      模型 ID（None 用默认）
        emit:       事件回调函数，接收 dict 参数
        task_id:    分布式任务 ID（非 None 时走 Redis metrics 快照）

    零侵入：不修改 turn_engine.py；_run_turn 在线程池中执行，
    事件通过线程安全 queue 桥接到 emit 回调。
    """
    # 延迟导入避免循环依赖
    from crawagent.web.turn_engine import _run_turn

    # 创建 asyncio loop（_emit 的 call_soon_threadsafe 需要 running loop）
    loop = asyncio.new_event_loop()

    # 用线程安全 queue.Queue 替代 asyncio.Queue
    # _run_turn → _emit → loop.call_soon_threadsafe(q.put_nowait, event)
    # queue.Queue.put_nowait 是线程安全的，在 loop 线程里调用没问题
    q: _thread_queue.Queue = _thread_queue.Queue()

    def run_loop():
        """在后台线程跑 asyncio loop，让 call_soon_threadsafe 有地方调度。"""
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def consumer():
        """从线程安全 queue 消费事件，通过 emit 回调推送。"""
        while True:
            try:
                event = q.get(timeout=0.5)
                if event is None:
                    break
                # 调用 emit 回调推送事件
                try:
                    emit(event)
                except Exception:
                    pass  # emit 失败不阻断消费
                # done/error 事件标志着本轮结束
                if isinstance(event, dict) and event.get("type") in ("done", "error"):
                    break
            except _thread_queue.Empty:
                # 检查 loop 是否还在运行，如果 _run_turn 已完成且 queue 为空，退出
                if not loop.is_running():
                    break
                continue

    # 启动 loop 线程
    loop_thread = threading.Thread(target=run_loop, daemon=True)
    loop_thread.start()

    # 启动 consumer 线程
    consumer_thread = threading.Thread(target=consumer, daemon=True)
    consumer_thread.start()

    # 在线程池里调用 _run_turn（它期望在线程池中执行，会调 _emit 往 q 里 put）
    try:
        # _run_turn(session_id, text, q, loop, model) 会在内部调 _emit，
        # _emit 做 loop.call_soon_threadsafe(q.put_nowait, event)
        # consumer 从 q.get() 取事件调 emit 回调
        _run_turn(session_id, text, q, loop, model)  # type: ignore[arg-type]
    except Exception as e:
        # _run_turn 自己有 try/except，但如果外面也崩了，发个 error 事件
        try:
            emit({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        # 停止 loop
        loop.call_soon_threadsafe(loop.stop)
        # 放一个 None 到 queue 让 consumer 退出
        q.put(None)
        # 等待 consumer 线程结束
        consumer_thread.join(timeout=5)
        loop_thread.join(timeout=5)


def _maybe_auto_title(session_id: str, agent, config: dict,
                      fallback_text: str = "") -> None:
    """会话自动命名 — 从 turn_engine 搬迁的薄壳调用。

    worker 场景下需要这个函数（_run_turn 内部调它），
    直接 re-export turn_engine 的实现。
    """
    from crawagent.web.turn_engine import _maybe_auto_title as _impl
    _impl(session_id, agent, config, fallback_text)
