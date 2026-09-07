"""MCP 连接错误回归测试 —— WinError 10061 / connection refused 不应漏到任务层。

背景：anything-analyzer 未启动时，_call_mcp 的 httpx.post 抛 httpx.TransportError，
原代码无 try/except，裸异常穿透 @tool → 杀死整轮。修法是把 httpx 调用包起来，
返回 {"error": {...}}，让 check_mcp_status 的错误分支接管（自动拉起 / 引导用户）。

本测试只验证 _call_mcp 这条根因路径：连接被拒时返回 error dict 而非抛异常。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from crawagent.tools import mcp_capture_tool


@pytest.fixture()
def isolated_mcp(monkeypatch, tmp_path):
    """隔离的 MCP 环境配置：单条 streamable_http server + 清空 session 缓存。"""
    monkeypatch.setenv(
        "MCP_SERVERS",
        json.dumps(
            [
                {
                    "name": "anything-analyzer",
                    "transport": "streamable_http",
                    "url": "http://127.0.0.1:23816/mcp",
                    "headers": {"Authorization": "Bearer test-token"},
                }
            ]
        ),
    )
    # 清空 session 缓存，强制 _ensure_mcp_session 走真握手路径
    monkeypatch.setattr(mcp_capture_tool, "_mcp_session_id", None, raising=False)
    yield


def _raising_client(exc):
    """构造一个 httpx.Client 替身：实例化即抛指定异常。"""

    class _BadClient:
        def __init__(self, *args, **kwargs):
            raise exc

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *args, **kwargs):
            raise exc

    return _BadClient


def test_call_mcp_returns_error_dict_on_connection_refused(isolated_mcp, monkeypatch):
    """服务未启动 → httpx.ConnectError → _call_mcp 返回 {"error": {...}}，不抛。"""
    monkeypatch.setattr(
        httpx, "Client", _raising_client(httpx.ConnectError("[WinError 10061] 目标计算机积极拒绝"))
    )

    # 不应抛异常
    result = mcp_capture_tool._call_mcp("ping", {}, timeout=2)

    assert isinstance(result, dict)
    assert "error" in result
    assert result["error"].get("kind") == "connection_refused"
    # 原始 Windows 错误信息应保留在 message 里，便于排查
    assert "10061" in result["error"].get("message", "")


def test_call_mcp_returns_error_dict_on_connect_timeout(isolated_mcp, monkeypatch):
    """连接超时（同为 TransportError 子类）也走优雅返回，不抛。"""
    monkeypatch.setattr(
        httpx, "Client", _raising_client(httpx.ConnectTimeout("timed out"))
    )

    result = mcp_capture_tool._call_mcp("ping", {}, timeout=1)

    assert isinstance(result, dict)
    assert "error" in result
    assert result["error"].get("kind") == "connection_refused"


def test_check_mcp_status_does_not_raise_on_connection_refused(isolated_mcp, monkeypatch):
    """端到端：check_mcp_status 在连不上时返回引导字符串，不抛、不杀任务。"""
    monkeypatch.setattr(
        httpx, "Client", _raising_client(httpx.ConnectError("[WinError 10061] 拒绝"))
    )
    # ensure_mcp_started 也别真去 spawn —— 它读 get_settings().mcp_servers 与命令配置
    monkeypatch.setattr(mcp_capture_tool, "ensure_mcp_started", lambda *a, **k: False, raising=False)

    # 不应抛异常。check_mcp_status 是 @tool 装饰的 StructuredTool，用 .func 调底层纯函数
    result = mcp_capture_tool.check_mcp_status.func()

    assert isinstance(result, str)
    # 落到 UNREACHABLE 分支，给出引导（不是裸 WinError）
    assert "MCP_UNREACHABLE" in result
