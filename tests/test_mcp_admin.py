"""mcp_admin 工具回归测试 —— 聊天即加 MCP 的 4 个 @tool。

用可变 store 模拟 get_settings().mcp_servers 与 save_mcp_servers，不碰真实 .env /
不触发 reset_agent_cache / 不做网络探测。覆盖 list/add/remove/disable 的成功与
各错误分支（重名/坏 transport/stdio 缺 command/坏 url/坏 headers JSON/不存在）。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import mcp_admin_tool


@pytest.fixture()
def store(monkeypatch):
    """可变 MCP_SERVERS store：get_settings 读它，save_mcp_servers 写它。"""
    # 先 pre-import 这两个模块（它们 import 期会调真实 get_settings），
    # 之后再 patch get_settings，避免 import 期撞上假 get_settings 缺 skills_dirs
    import crawagent.web.routers.settings  # noqa: F401
    import crawagent.graph.skills  # noqa: F401

    state = {"servers": []}

    def fake_get_settings():
        return SimpleNamespace(mcp_servers=json.dumps(state["servers"]))

    def fake_save(incoming):
        # 真实 save_mcp_servers 会校验/掩码/落盘；这里只模拟落盘到 store
        state["servers"] = list(incoming)
        return {"ok": True, "mcp_servers": list(incoming)}

    def fake_status():
        # 返回每个 server reachable=False（不跑网络），让 add 回告走 UNREACHABLE 分支
        return [{"name": s.get("name"), "reachable": False, "tools": 0} for s in state["servers"]]

    monkeypatch.setattr("crawagent.config.settings.get_settings", fake_get_settings)
    monkeypatch.setattr("crawagent.web.routers.settings.save_mcp_servers", fake_save)
    monkeypatch.setattr("crawagent.graph.skills.mcp_servers_status", fake_status)
    # _mask_mcp_headers 真实函数可用（它读 current 里的 headers 还原掩码；store 里无掩码，直通）
    return state


# ---------- list ----------

def test_list_empty(store):
    assert "[MCP_EMPTY]" in mcp_admin_tool.list_mcp_servers.func()


def test_list_with_servers(store):
    store["servers"] = [
        {"name": "firecrawl", "transport": "streamable_http", "url": "http://127.0.0.1:23816/mcp", "disabled": False},
        {"name": "stdio-one", "transport": "stdio", "command": "/x/python", "args": ["-m", "srv"], "disabled": True},
    ]
    out = mcp_admin_tool.list_mcp_servers.func()
    assert "firecrawl" in out and "stdio-one" in out
    assert "停用" in out  # disabled 标记


# ---------- add 成功 ----------

def test_add_http_success(store):
    res = mcp_admin_tool.add_mcp_server.func(
        name="firecrawl", transport="streamable_http", url="http://127.0.0.1:23816/mcp"
    )
    assert "[MCP_ADDED]" in res
    assert "下条消息生效" in res
    assert len(store["servers"]) == 1
    assert store["servers"][0]["name"] == "firecrawl"
    assert store["servers"][0]["url"] == "http://127.0.0.1:23816/mcp"
    assert store["servers"][0]["disabled"] is False


def test_add_stdio_args_string_split_into_list(store):
    res = mcp_admin_tool.add_mcp_server.func(
        name="srv", transport="stdio", command="/x/python", args="-m mcp_server_fetch --port 8000"
    )
    assert "[MCP_ADDED]" in res
    entry = store["servers"][0]
    assert entry["command"] == "/x/python"
    assert entry["args"] == ["-m", "mcp_server_fetch", "--port", "8000"]


def test_add_headers_json_parsed(store):
    res = mcp_admin_tool.add_mcp_server.func(
        name="h", transport="streamable_http", url="http://x/mcp",
        headers='{"Authorization":"Bearer tok"}',
    )
    assert "[MCP_ADDED]" in res
    assert store["servers"][0]["headers"] == {"Authorization": "Bearer tok"}


# ---------- add 错误分支 ----------

def test_add_duplicate(store):
    store["servers"] = [{"name": "dup", "transport": "streamable_http", "url": "http://x/mcp", "disabled": False}]
    res = mcp_admin_tool.add_mcp_server.func(name="dup", transport="streamable_http", url="http://y/mcp")
    assert "[MCP_DUPLICATE]" in res
    assert len(store["servers"]) == 1  # 未写入


def test_add_bad_transport(store):
    res = mcp_admin_tool.add_mcp_server.func(name="x", transport="weird")
    assert "[MCP_ERROR]" in res and "transport" in res
    assert store["servers"] == []


def test_add_stdio_missing_command(store):
    res = mcp_admin_tool.add_mcp_server.func(name="x", transport="stdio")
    assert "[MCP_ERROR]" in res and "command" in res


def test_add_http_bad_url(store):
    res = mcp_admin_tool.add_mcp_server.func(name="x", transport="streamable_http", url="ftp://x")
    assert "[MCP_ERROR]" in res and "http(s)" in res


def test_add_bad_headers_json(store):
    res = mcp_admin_tool.add_mcp_server.func(
        name="x", transport="streamable_http", url="http://x/mcp", headers="{not json"
    )
    assert "[MCP_ERROR]" in res and "JSON" in res


# ---------- remove ----------

def test_remove_existing(store):
    store["servers"] = [
        {"name": "a", "transport": "streamable_http", "url": "http://a/mcp", "disabled": False},
        {"name": "b", "transport": "streamable_http", "url": "http://b/mcp", "disabled": False},
    ]
    res = mcp_admin_tool.remove_mcp_server.func(name="a")
    assert "[MCP_REMOVED]" in res
    assert [s["name"] for s in store["servers"]] == ["b"]


def test_remove_not_found(store):
    store["servers"] = [{"name": "a", "transport": "streamable_http", "url": "http://a/mcp", "disabled": False}]
    res = mcp_admin_tool.remove_mcp_server.func(name="zzz")
    assert "[MCP_NOT_FOUND]" in res


# ---------- disable ----------

def test_disable_existing(store):
    store["servers"] = [{"name": "a", "transport": "streamable_http", "url": "http://a/mcp", "disabled": False}]
    res = mcp_admin_tool.disable_mcp_server.func(name="a")
    assert "[MCP_DISABLED]" in res
    assert store["servers"][0]["disabled"] is True


def test_disable_already_disabled(store):
    store["servers"] = [{"name": "a", "transport": "streamable_http", "url": "http://a/mcp", "disabled": True}]
    res = mcp_admin_tool.disable_mcp_server.func(name="a")
    assert "[MCP_ALREADY]" in res


def test_disable_not_found(store):
    res = mcp_admin_tool.disable_mcp_server.func(name="none")
    assert "[MCP_NOT_FOUND]" in res
