"""轮次事件日志：跨线程 append + 订阅者按 cursor 增量重放。"""
from __future__ import annotations

from typing import Any


class EventLog:
    """一轮对话的事件日志（append-only）。

    worker 线程通过 append() 记录事件（list.append 原子，无需加锁）；
    订阅协程通过 since(cursor) 增量取走新事件并推进 cursor。
    重连时从 cursor=0 全量重放，保证 done/error 等收尾事件不因
    订阅者切换（页面刷新、WebSocket 断开重连）而丢失。
    """

    __slots__ = ("_events",)

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        """worker（线程）记录一条轮次事件"""
        self._events.append(event)

    def since(self, cursor: int) -> tuple[list[dict[str, Any]], int]:
        """返回 cursor 之后的事件与推进后的 cursor 位置"""
        events = self._events[cursor:]
        return events, cursor + len(events)

    def __len__(self) -> int:
        return len(self._events)
