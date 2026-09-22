"""mcp_capture_tool 测试 — wait_capture_ready 状态机 + session 重置。

全部离线，mock _call_mcp + time.sleep（不让测试真等）。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import mcp_capture_tool


# ---------------------------------------------------------------------------
# fixture：把 _call_mcp / _mcp_rpc_id / _mcp_session_id 都 mock 掉
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_session_state(monkeypatch):
    """每个测试前后清 MCP session 全局状态。"""
    # 替换 _call_mcp 成空 mock（默认会改）
    monkeypatch.setattr(mcp_capture_tool, "_call_mcp", MagicMock(return_value={}))
    # time.sleep 短路（不真等）
    monkeypatch.setattr(mcp_capture_tool.time, "sleep", lambda *_a, **_kw: None)
    yield


def _sessions_response(sessions_list):
    """造 MCP tools/call 响应格式。"""
    return {"result": {"content": [{"text": str(sessions_list).replace("'", '"')}]}}


def _json_sessions(sessions_list):
    """造 MCP tools/call 响应格式（用真实 JSON 字符串）。"""
    import json
    return {"result": {"content": [{"text": json.dumps(sessions_list)}]}}


# ---------------------------------------------------------------------------
# wait_capture_ready 状态机
# ---------------------------------------------------------------------------

def test_wait_returns_ready_when_session_running(monkeypatch):
    """list_sessions 返回 running → 返回 [CAPTURE_READY]。"""
    monkeypatch.setattr(
        mcp_capture_tool, "_call_mcp",
        lambda *a, **kw: _json_sessions([{"id": "s1", "status": "running"}]),
    )
    out = mcp_capture_tool.wait_capture_ready.func("s1", timeout=5)
    assert out == "[CAPTURE_READY]"


def test_wait_returns_not_started_when_stopped(monkeypatch):
    """list_sessions 一直返回 stopped → 返回 [CAPTURE_NOT_STARTED] 含引导提示。"""
    monkeypatch.setattr(
        mcp_capture_tool, "_call_mcp",
        lambda *a, **kw: _json_sessions([{"id": "s1", "status": "stopped", "name": "MySession"}]),
    )
    out = mcp_capture_tool.wait_capture_ready.func("s1", timeout=1)
    assert "[CAPTURE_NOT_STARTED]" in out
    assert "MySession" in out  # 引导提示含会话名


def test_wait_returns_not_started_when_session_missing(monkeypatch):
    """list_sessions 没这个 session_id → 走超时分支。"""
    monkeypatch.setattr(
        mcp_capture_tool, "_call_mcp",
        lambda *a, **kw: _json_sessions([{"id": "other", "status": "running"}]),
    )
    out = mcp_capture_tool.wait_capture_ready.func("missing", timeout=1)
    assert "[CAPTURE_NOT_STARTED]" in out


def test_wait_handles_non_list_response(monkeypatch):
    """_call_mcp 返回非 list（异常情况）→ 不抛。"""
    def fake_call(*a, **kw):
        return {"result": {"content": [{"text": "not a list"}]}}
    monkeypatch.setattr(mcp_capture_tool, "_call_mcp", fake_call)
    out = mcp_capture_tool.wait_capture_ready.func("any", timeout=1)
    assert "[CAPTURE_NOT_STARTED]" in out  # 超时兜底


def test_reset_mcp_session_clears_state(monkeypatch):
    """_reset_mcp_session 把全局 session_id 清空。"""
    # 设个值
    mcp_capture_tool._mcp_session_id = "fake-sid"
    assert mcp_capture_tool._mcp_session_id == "fake-sid"
    mcp_capture_tool._reset_mcp_session()
    assert mcp_capture_tool._mcp_session_id is None


def test_wait_minimum_timeout_is_one(monkeypatch):
    """timeout=0 或负数 → 至少跑 1 秒（wait_capture_ready 内部 clamp）。"""
    call_count = [0]
    def fake_call(*a, **kw):
        call_count[0] += 1
        return _json_sessions([])
    monkeypatch.setattr(mcp_capture_tool, "_call_mcp", fake_call)
    # timeout=0 应被抬到 1，不会立刻超时
    out = mcp_capture_tool.wait_capture_ready.func("s1", timeout=0)
    assert call_count[0] >= 1  # 至少调一次
    assert "[CAPTURE_NOT_STARTED]" in out