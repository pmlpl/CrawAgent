"""auto_sync_mcp_token 回归测试 —— 必须保留 .env 里除 anything-analyzer 外的所有 server。

背景（bug）：auto_sync 原从 os.environ 读 MCP_SERVERS，而 pydantic-settings 不把
.env 导出进 os.environ，启动期 os.environ["MCP_SERVERS"] 为空 → auto_sync 新建
只含 anything 的列表写回 os.environ → 用户手动加的 browser-harness 等 stdio server
被丢掉，os.environ 优先级又高于 .env，get_settings 也跟着只看到 anything。

修法：auto_sync 改从 get_settings().mcp_servers（权威源，含 .env 全量）读，只更新
anything-analyzer 的 token/url，写回时保留全部 server。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(monkeypatch):
    """两个 server 的 .env 配置 + 假 Electron 配置。"""
    servers_in_env = [
        {"name": "anything-analyzer", "transport": "streamable_http", "disabled": True,
         "url": "http://127.0.0.1:23816/mcp", "headers": {"Authorization": "Bearer OLD"}},
        {"name": "browser-harness", "transport": "stdio", "disabled": False,
         "command": "/x/browser-harness-mcp.exe"},
    ]

    # 清掉 os.environ，模拟 pydantic-settings 不导出 .env 的真实启动期状态
    monkeypatch.delenv("MCP_SERVERS", raising=False)

    # get_settings 返回 .env 里的全量（含 browser-harness）
    monkeypatch.setattr(
        "crawagent.config.settings.get_settings",
        lambda: SimpleNamespace(mcp_servers=json.dumps(servers_in_env)),
    )

    # 假 Electron 配置（auto_sync 从这里拿 anything 的真实 token）
    monkeypatch.setattr(
        "crawagent.tools.mcp_capture_tool._load_electron_mcp_config",
        lambda: {"authToken": "NEW-TOKEN", "host": "0.0.0.0", "port": 23816, "enabled": True},
    )
    yield servers_in_env


def test_autosync_preserves_non_anything_servers(env):
    import os
    from crawagent.tools.mcp_capture_tool import auto_sync_mcp_token

    assert auto_sync_mcp_token() is True

    after = json.loads(os.environ["MCP_SERVERS"])
    names = [s.get("name") for s in after]
    assert "anything-analyzer" in names
    assert "browser-harness" in names, "auto_sync 不得丢掉用户手动加的 stdio server"

    # anything 的 token 应被 Electron 的新 token 覆盖
    anything = next(s for s in after if s.get("name") == "anything-analyzer")
    assert anything["headers"]["Authorization"] == "Bearer NEW-TOKEN"

    # browser-harness 字段应原样保留
    bh = next(s for s in after if s.get("name") == "browser-harness")
    assert bh["command"] == "/x/browser-harness-mcp.exe"
    assert bh["transport"] == "stdio"
    assert bh["disabled"] is False
