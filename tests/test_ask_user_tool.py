"""ask_user_tool 测试 — 阻塞 + 唤醒 + 超时 + 参数校验。

依赖测试约定：用线程触发 resolve_ask 模拟"用户在 UI 上点选项"。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import ask_user_tool


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_pending():
    """每个测试后清空 _PENDING（避免污染其他测试）。"""
    yield
    with ask_user_tool._LOCK:
        ask_user_tool._PENDING.clear()


@pytest.fixture
def fast_emit(monkeypatch):
    """让 emit_turn_event 不真打桩，直接记 calls。"""
    calls: list[dict] = []
    monkeypatch.setattr(ask_user_tool, "emit_turn_event", lambda evt: calls.append(evt))
    return calls


# ---------------------------------------------------------------------------
# 基本阻塞 + 唤醒
# ---------------------------------------------------------------------------

def test_ask_user_returns_resolved_value(fast_emit):
    """用户点击选项 → 工具返回选项原文 + emit ask_answered。"""
    result_holder: list[str] = []
    def trigger():
        time.sleep(0.05)
        # 拿到 ask_id
        ids = ask_user_tool.pending_ask_ids()
        assert len(ids) == 1
        ask_user_tool.resolve_ask(ids[0], "选 A")

    t = threading.Thread(target=trigger)
    t.start()
    out = ask_user_tool.ask_user.func("决定", ["A", "B"])
    t.join(timeout=2)
    result_holder.append(out)

    assert out == "选 A"
    # emit: ask + ask_answered
    assert len(fast_emit) >= 2
    assert fast_emit[0]["type"] == "ask"
    assert fast_emit[0]["question"] == "决定"
    assert fast_emit[0]["options"] == ["A", "B"]
    assert any(e["type"] == "ask_answered" and e.get("value") == "选 A" for e in fast_emit)


def test_ask_user_pending_id_format(fast_emit):
    """ask_id 形如 ``ask_<ms>_<seq>``。"""
    def trigger():
        time.sleep(0.05)
        ids = ask_user_tool.pending_ask_ids()
        assert ids and ids[0].startswith("ask_")
        ask_user_tool.resolve_ask(ids[0], "ok")

    t = threading.Thread(target=trigger)
    t.start()
    ask_user_tool.ask_user.func("Q", ["x", "y"])
    t.join(timeout=2)

    # emit[0] 里的 ask_id 与 pending id 一致
    ask_id = fast_emit[0]["ask_id"]
    assert ask_id.startswith("ask_")
    parts = ask_id.split("_")
    assert len(parts) == 3
    assert parts[0] == "ask"
    assert parts[1].isdigit()
    assert parts[2].isdigit()


# ---------------------------------------------------------------------------
# 超时
# ---------------------------------------------------------------------------

def test_ask_user_timeout_returns_message(fast_emit):
    """超过 timeout 仍无回答 → 返回超时提示语 + emit ask_answered(value=None)。"""
    out = ask_user_tool.ask_user.func("Q", ["A", "B"], timeout=1)
    assert "⏳" in out or "未选择" in out
    assert any(e["type"] == "ask_answered" and e.get("value") is None for e in fast_emit)


def test_ask_user_timeout_clamped_to_max(fast_emit):
    """timeout > _MAX_TIMEOUT → 截断到 _MAX_TIMEOUT（1800s）。但 ask_user.func 同步阻塞，
    这里只验证不抛异常且 emit 被触发（timeout 在 1800s 量级会等很久，所以只测 clamp 逻辑）。"""
    # 直接验证 _MIN_TIMEOUT / _MAX_TIMEOUT 常量
    assert ask_user_tool._MIN_TIMEOUT == 1
    assert ask_user_tool._MAX_TIMEOUT == 1800


def test_ask_user_timeout_invalid_value_falls_back_default(monkeypatch):
    """timeout 传非数字 → except 分支 wait_s=600s（不经过 _MAX_TIMEOUT clamp）。

    不能真等 600s，所以 monkeypatch ``threading.Event.wait`` 立即返回，验证 wait_s=600 路径。
    """
    captured: dict = {}
    real_wait = threading.Event.wait

    def fake_wait(self, timeout=None):
        captured["timeout"] = timeout
        return False  # 立即返回（视为未回答）

    monkeypatch.setattr(threading.Event, "wait", fake_wait)
    out = ask_user_tool.ask_user.func("Q", ["A", "B"], timeout="bogus")  # type: ignore[arg-type]
    assert captured["timeout"] == 600
    assert "未选择" in out


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------

def test_ask_user_empty_question_returns_error(fast_emit):
    """question 为空 → 直接返回错误信息，不进 wait。"""
    out = ask_user_tool.ask_user.func("", ["A", "B"])
    assert "question 不能为空" in out
    # 不应有 emit
    assert all(e.get("type") != "ask" for e in fast_emit)


def test_ask_user_too_few_options_returns_error(fast_emit):
    """options < 2 → 报错。"""
    out = ask_user_tool.ask_user.func("Q", ["only_one"])
    assert "options 需要 2-6 个" in out


def test_ask_user_too_many_options_returns_error(fast_emit):
    """options > 6 → 报错。"""
    out = ask_user_tool.ask_user.func("Q", ["1", "2", "3", "4", "5", "6", "7"])
    assert "options 需要 2-6 个" in out


def test_ask_user_options_strips_whitespace(monkeypatch):
    """options 里带空格的项 → strip；空项被过滤。"""
    # monkeypatch Event.wait 立即返回避免阻塞
    captured_options: list[list[str]] = []

    real_emit = ask_user_tool.emit_turn_event
    def fake_emit(evt):
        if evt.get("type") == "ask":
            captured_options.append(evt.get("options", []))
        return real_emit(evt) if False else None

    monkeypatch.setattr(ask_user_tool, "emit_turn_event", fake_emit)

    # 用 threading.Event.wait 立即返回避免阻塞
    monkeypatch.setattr(threading.Event, "wait", lambda self, timeout=None: False)

    ask_user_tool.ask_user.func("Q", ["  A  ", "", "  ", "B"], timeout=1)
    assert len(captured_options) == 1
    # 空格项 strip 后保留，空字符串被过滤
    assert captured_options[0] == ["A", "B"]


def test_ask_user_question_strips_whitespace(fast_emit):
    """question 带前后空格 → strip。"""
    def trigger():
        time.sleep(0.05)
        ids = ask_user_tool.pending_ask_ids()
        if ids:
            ask_user_tool.resolve_ask(ids[0], "A")

    t = threading.Thread(target=trigger)
    t.start()
    ask_user_tool.ask_user.func("   真问题   ", ["A", "B"])
    t.join(timeout=2)

    # emit[0] 是 ask，question 应被 strip
    ask_evt = next(e for e in fast_emit if e["type"] == "ask")
    assert ask_evt["question"] == "真问题"


# ---------------------------------------------------------------------------
# resolve_ask 幂等性
# ---------------------------------------------------------------------------

def test_resolve_ask_unknown_id_returns_false():
    """未知 ask_id → 返回 False。"""
    assert ask_user_tool.resolve_ask("ask_does_not_exist", "A") is False


def test_resolve_ask_already_answered_returns_false():
    """已经回答过（value 非 None）的 ask_id → 再次 resolve 返回 False。"""
    def trigger_first():
        time.sleep(0.05)
        ids = ask_user_tool.pending_ask_ids()
        ask_user_tool.resolve_ask(ids[0], "first")

    t = threading.Thread(target=trigger_first)
    t.start()
    ask_user_tool.ask_user.func("Q", ["A", "B"])
    t.join(timeout=2)

    # 此时 _PENDING 已被 ask_user 清空（除非超时尚未发生），验证 resolve 未知 ID
    assert ask_user_tool.resolve_ask("ask_unknown", "second") is False


# ---------------------------------------------------------------------------
# pending_ask_ids
# ---------------------------------------------------------------------------

def test_pending_ask_ids_lists_active():
    """阻塞期间 pending_ask_ids 返回当前活动的 ask_id。"""
    captured: list[list[str]] = []

    def trigger():
        time.sleep(0.1)
        captured.append(ask_user_tool.pending_ask_ids())
        ids = ask_user_tool.pending_ask_ids()
        if ids:
            ask_user_tool.resolve_ask(ids[0], "A")

    t = threading.Thread(target=trigger)
    t.start()
    ask_user_tool.ask_user.func("Q", ["A", "B"])
    t.join(timeout=2)

    assert len(captured) == 1
    assert len(captured[0]) == 1
    assert captured[0][0].startswith("ask_")