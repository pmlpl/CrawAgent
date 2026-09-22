"""MCP 开关翻转 vs os.environ 启动快照（bug 回归）。

auto_sync_mcp_token 启动时把 server 列表写进 os.environ["MCP_SERVERS"]，
pydantic 里 env vars 优先于 .env——save 只写 .env 不同步 os.environ 的话，
翻转被启动快照盖住（开关"看起来无效" + _mcp_config_signature 缓存指纹致盲）。
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from crawagent.config.settings import Settings
from crawagent.web.routers.settings import save_mcp_servers


@pytest.fixture()
def env(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    monkeypatch.setattr("crawagent.web.routers.settings.core.ENV_FILE", env_file)
    monkeypatch.setitem(Settings.model_config, "env_file", env_file)
    return env_file


def _snapshot(disabled: bool) -> list[dict]:
    return [{
        "name": "anything-analyzer",
        "transport": "streamable_http",
        "disabled": disabled,
        "url": "http://127.0.0.1:23816/mcp",
    }]


def test_toggle_survives_stale_os_environ(env, monkeypatch):
    """os.environ 有启动快照（disabled=true）时翻转 → .env / os.environ / 响应三处都变 false。"""
    monkeypatch.setenv("MCP_SERVERS", json.dumps(_snapshot(True)))

    res = save_mcp_servers(_snapshot(False))
    assert res["ok"] is True

    saved = {s["name"]: s for s in res["mcp_servers"]}
    assert saved["anything-analyzer"]["disabled"] is False, "响应必须反映翻转后的新值"

    oenv = json.loads(os.environ["MCP_SERVERS"])
    assert oenv[0]["disabled"] is False, "os.environ 必须与 .env 同步（pydantic 优先读它）"

    text = env.read_text(encoding="utf-8")
    assert '"disabled": false' in text


def test_disable_flip_back(env, monkeypatch):
    """反向：os.environ 快照 disabled=false 时停用 → 三处都变 true。"""
    monkeypatch.setenv("MCP_SERVERS", json.dumps(_snapshot(False)))

    res = save_mcp_servers(_snapshot(True))
    assert res["ok"] is True
    assert res["mcp_servers"][0]["disabled"] is True
    assert json.loads(os.environ["MCP_SERVERS"])[0]["disabled"] is True


def test_no_os_environ_snapshot_still_works(env, monkeypatch):
    """os.environ 无快照（Electron 未检测到，auto_sync 早退）时走纯 .env 路径照常。"""
    monkeypatch.delenv("MCP_SERVERS", raising=False)

    res = save_mcp_servers(_snapshot(False))
    assert res["ok"] is True
    assert res["mcp_servers"][0]["disabled"] is False
    assert '"disabled": false' in env.read_text(encoding="utf-8")
