"""ask_user 选择题工具（human-in-the-loop）单元测试。

emit_turn_event 用 monkeypatch 替身捕获；阻塞用真线程 + resolve_ask 唤醒。
"""
import threading
import time

import pytest

from crawagent.tools import ask_user_tool
from crawagent.tools.ask_user_tool import ask_user, pending_ask_ids, resolve_ask


def _run_ask(kwargs, answer_after=None, answer=None):
    """在子线程里跑 ask_user，返回 (结果线程, 捕获的事件列表)。"""
    events = []
    orig = ask_user_tool.emit_turn_event
    ask_user_tool.emit_turn_event = lambda ev: events.append(ev) or True
    try:
        out = {}

        def runner():
            out["result"] = ask_user.invoke(kwargs)

        t = threading.Thread(target=runner)
        t.start()
        if answer_after is not None:
            # 等待 ask 事件发出后作答
            deadline = time.time() + 5
            while time.time() < deadline and not any(e["type"] == "ask" for e in events):
                time.sleep(0.02)
            ask_ev = next(e for e in events if e["type"] == "ask")
            time.sleep(answer_after)
            assert resolve_ask(ask_ev["ask_id"], answer) is True
        t.join(timeout=10)
        assert not t.is_alive(), "ask_user 线程超时未返回"
        return out.get("result"), events
    finally:
        ask_user_tool.emit_turn_event = orig


def test_ask_user_returns_user_choice_and_events_are_replayable():
    result, events = _run_ask(
        {"question": "要打开 anything-analyzer 吗？", "options": ["打开", "暂不"], "timeout": 5},
        answer_after=0.2, answer="打开",
    )
    assert result == "打开"
    types = [e["type"] for e in events]
    assert types[0] == "ask"
    assert events[0]["options"] == ["打开", "暂不"]
    assert {"type": "ask_answered", "value": "打开"}.items() <= events[-1].items()


def test_ask_user_timeout_is_explicit_and_closes_ask():
    result, events = _run_ask({"question": "授权批量下载？", "options": ["继续", "取消"], "timeout": 1})
    assert "未选择" in result and "暂缓" in result
    assert events[-1]["type"] == "ask_answered" and events[-1]["value"] is None
    assert pending_ask_ids() == []  # 超时后清理，无悬挂


def test_ask_user_rejects_bad_input():
    assert "question 不能为空" in ask_user.invoke({"question": " ", "options": ["a", "b"]})
    assert "2-6" in ask_user.invoke({"question": "q", "options": ["只有一项"]})
    assert "2-6" in ask_user.invoke({"question": "q", "options": [str(i) for i in range(7)]})


def test_resolve_ask_idempotent_and_unknown_safe():
    assert resolve_ask("ask_unknown_id", "x") is False


def test_emit_turn_event_without_turn_is_safe():
    # 无活动轮次（无 emitter）时静默失败返回 False，工具不炸
    from crawagent.tools.progress import emit_turn_event
    assert emit_turn_event({"type": "ask", "ask_id": "x", "question": "q", "options": ["a", "b"]}) is False
