"""MCP 失败负缓存回归测试（B 修复）。

get_mcp_tools 对连不上的 server：首次失败打印一行 + 记时间戳，
TTL 内再次调用不再重复探测（不刷屏）。reset_mcp_cache 清空负缓存后重新探测。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(monkeypatch):
    """一个 enabled-but-down 的 stdio server 配置。"""
    monkeypatch.setenv(
        "MCP_SERVERS",
        json.dumps([
            {"name": "down-srv", "transport": "stdio", "disabled": False, "command": "/nonexistent/xyz"},
        ]),
    )

    # 真实 _server_conn 会返回 stdio conn（command 路径），但 MultiServerMCPClient 会尝试 spawn 失败。
    # 用一个假 client 类替换它，可控地抛错并计数。
    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, conn, **kw):
            pass

        async def get_tools(self):
            calls["n"] += 1
            raise ConnectionRefusedError("simulated down")

    import crawagent.graph.skills as skills
    import langchain_mcp_adapters.client as lmc
    monkeypatch.setattr(lmc, "MultiServerMCPClient", _FakeClient, raising=False)
    monkeypatch.setattr(
        "crawagent.config.settings.get_settings",
        lambda: SimpleNamespace(mcp_servers=__import__("os").environ["MCP_SERVERS"], project_root=Path(".")),
    )
    skills.reset_mcp_cache()
    return calls


def test_failed_server_not_reprobed_within_ttl(env):
    import crawagent.graph.skills as skills

    skills.get_mcp_tools()
    assert env["n"] == 1, "首次应探测一次"

    skills.get_mcp_tools()
    skills.get_mcp_tools()
    assert env["n"] == 1, f"TTL 内不应重复探测，实际 {env['n']}"


def test_reset_mcp_cache_clears_fail_cache_and_reprobes(env):
    import crawagent.graph.skills as skills

    skills.get_mcp_tools()
    assert env["n"] == 1
    skills.get_mcp_tools()
    assert env["n"] == 1  # 仍缓存失败

    skills.reset_mcp_cache()
    skills.get_mcp_tools()
    assert env["n"] == 2, "reset 后应重新探测一次"


def test_success_clears_fail_cache(env, monkeypatch):
    """一个 server 先失败再恢复：成功后清失败标记，后续走成功缓存。"""
    import crawagent.graph.skills as skills

    skills.get_mcp_tools()
    assert env["n"] == 1

    # 改成成功
    import langchain_mcp_adapters.client
    class _OkClient:
        def __init__(self, conn, **kw):
            pass

        async def get_tools(self):
            from langchain_core.tools import StructuredTool

            def _fake():
                """fake tool for test."""
                return "x"

            return [StructuredTool.from_function(_fake, name="fake_tool")]

    monkeypatch.setattr(langchain_mcp_adapters.client, "MultiServerMCPClient", _OkClient, raising=False)
    skills.reset_mcp_cache()
    tools = skills.get_mcp_tools()
    assert len(tools) == 1
    # 成功后 fail_cache 应已清空该 server
    assert "down-srv" not in skills._mcp_fail_cache


def test_dedup_tools_first_server_wins():
    """跨 server 同名工具去重：配置序先到先得（anything/browser-harness 都有 browser_screenshot）。"""
    from crawagent.graph.skills import _dedup_tools

    class T:
        def __init__(self, name):
            self.name = name

    per_server = {
        "anything-analyzer": [T("browser_screenshot"), T("run_analysis")],
        "browser-harness": [T("browser_screenshot"), T("browser_click")],
    }
    out = _dedup_tools(per_server)
    names = [t.name for t in out]
    assert names == ["browser_screenshot", "run_analysis", "browser_click"]
    assert len(names) == len(set(names))
