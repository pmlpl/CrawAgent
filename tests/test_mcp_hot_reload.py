"""043 MCP 工具热感知：配置指纹门单测（规格 §3.4）。

三组用例：
1. _mcp_fingerprint 表驱动——加 server / 翻转 enabled / 改 token / 改 url
   变指纹，无关设置变化不敏感；Electron 配置缺失/损坏返回稳定值不抛异常。
2. get_agent 指纹门——指纹变化重建（计数+1）、不变复用、重建后新指纹入账、
   未请求的模型键不被牵连；reset_agent_cache 清空指纹表（全清语义保持）。
3. 指纹计算异常兜底——按「未变化」处理返回上次指纹，绝不阻断。
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import crawagent.tools.mcp_capture_tool as mcp_capture_tool
import crawagent.web.state as state

BASE_SERVERS = [
    {
        "name": "anything-analyzer",
        "transport": "streamable_http",
        "url": "http://127.0.0.1:23816/mcp",
        "headers": {"Authorization": "Bearer tok-1"},
    }
]
BASE_ELECTRON = {"authToken": "tok-1", "port": 23816, "enabled": True}


@pytest.fixture(autouse=True)
def _isolate_agent_cache(monkeypatch):
    """state 模块级缓存换测试私有实例，防跨测试泄漏（conftest 隔离精神的本模块补充）。"""
    monkeypatch.setattr(state, "_agents", state._LRUDict(8, "agents-test"))
    monkeypatch.setattr(state, "_mcp_fps", {})
    monkeypatch.setattr(state, "_last_mcp_fp", "")
    monkeypatch.setattr(state, "_checkpointer", None)
    monkeypatch.setattr(state, "reset_meta_conn", lambda: None)


def _stub_fp_inputs(monkeypatch, servers, electron):
    """指纹输入双桩：settings 只暴露 mcp_servers，Electron 配置直接给值。"""
    monkeypatch.setattr(
        state, "get_settings",
        lambda: SimpleNamespace(mcp_servers=json.dumps(servers, ensure_ascii=False)),
    )
    monkeypatch.setattr(
        mcp_capture_tool, "_load_electron_mcp_config", lambda: electron
    )


def _fp(monkeypatch, servers, electron) -> str:
    _stub_fp_inputs(monkeypatch, servers, electron)
    return state._mcp_fingerprint()


def _stub_builder(monkeypatch) -> list:
    """_build_agent 计数桩：每次构建记下 model，返回可判身份的哨兵对象。"""
    calls: list = []

    def _fake_build(checkpointer=None, model=None):
        calls.append(model)
        return SimpleNamespace(name=f"agent-{model}-{len(calls)}")

    monkeypatch.setattr(state, "_build_agent", _fake_build)
    monkeypatch.setattr(state, "get_checkpointer", lambda: None)
    return calls


# ---- 1. 指纹表驱动 ----

def test_fingerprint_table_driven(monkeypatch):
    base = _fp(monkeypatch, BASE_SERVERS, BASE_ELECTRON)

    # 加 server → 变
    added = BASE_SERVERS + [{"name": "bh", "transport": "stdio", "command": "C:/x.exe"}]
    assert _fp(monkeypatch, added, BASE_ELECTRON) != base

    # 翻转 Electron enabled → 变
    assert _fp(monkeypatch, BASE_SERVERS, {**BASE_ELECTRON, "enabled": False}) != base

    # 改 token（Electron 侧与 settings headers 侧各验一次）→ 变
    assert _fp(monkeypatch, BASE_SERVERS, {**BASE_ELECTRON, "authToken": "tok-2"}) != base
    tok2 = [dict(BASE_SERVERS[0], headers={"Authorization": "Bearer tok-2"})]
    assert _fp(monkeypatch, tok2, BASE_ELECTRON) != base

    # 改 url → 变
    url2 = [dict(BASE_SERVERS[0], url="http://127.0.0.1:9999/mcp")]
    assert _fp(monkeypatch, url2, BASE_ELECTRON) != base

    # 无关设置变化（mcp_servers 之外的 Settings 字段）→ 不敏感
    _stub_fp_inputs(monkeypatch, BASE_SERVERS, BASE_ELECTRON)
    monkeypatch.setattr(
        state, "get_settings",
        lambda: SimpleNamespace(
            mcp_servers=json.dumps(BASE_SERVERS, ensure_ascii=False), theme="dark",
        ),
    )
    assert state._mcp_fingerprint() == base


def test_fingerprint_electron_missing_or_corrupt_stable(monkeypatch, tmp_path: Path):
    """Electron 配置缺失/损坏：真实 _load_electron_mcp_config 返回 None →
    记 {} → 指纹稳定且不抛异常（规格 §3.4 第三组）。"""
    monkeypatch.setattr(
        state, "get_settings",
        lambda: SimpleNamespace(mcp_servers=json.dumps(BASE_SERVERS)),
    )
    monkeypatch.setattr(
        mcp_capture_tool, "_electron_mcp_config_path", lambda: tmp_path / "nope.json"
    )
    fp1 = state._mcp_fingerprint()
    assert fp1 == state._mcp_fingerprint()

    # 损坏文件（JSONDecodeError → None）同样稳定，且与「缺失」同值
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(mcp_capture_tool, "_electron_mcp_config_path", lambda: bad)
    assert state._mcp_fingerprint() == fp1

    # 显式 None 与 {} 等价（_load_electron_mcp_config 契约）
    monkeypatch.setattr(mcp_capture_tool, "_load_electron_mcp_config", lambda: None)
    assert state._mcp_fingerprint() == fp1


def test_fingerprint_exception_falls_back_to_last(monkeypatch):
    """指纹计算抛异常 → 返回上次成功指纹（按未变化处理），不阻断对话轮次。"""
    good = _fp(monkeypatch, BASE_SERVERS, BASE_ELECTRON)
    assert state._last_mcp_fp == good

    def _boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(state, "get_settings", _boom)
    assert state._mcp_fingerprint() == good


# ---- 2. get_agent 指纹门 ----

def test_get_agent_rebuilds_on_fingerprint_change(monkeypatch):
    calls = _stub_builder(monkeypatch)
    monkeypatch.setattr(state, "_mcp_fingerprint", lambda: "fp-A")

    a1 = state.get_agent("m1")
    assert state.get_agent("m1") is a1
    assert len(calls) == 1  # 指纹不变 → 复用

    monkeypatch.setattr(state, "_mcp_fingerprint", lambda: "fp-B")
    a2 = state.get_agent("m1")
    assert a2 is not a1
    assert len(calls) == 2  # 指纹变化 → 重建
    assert state._mcp_fps["m1"] == "fp-B"  # 重建后新指纹入账

    monkeypatch.setattr(state, "_mcp_fingerprint", lambda: "fp-B")
    assert state.get_agent("m1") is a2
    assert len(calls) == 2  # 重建后指纹未再变 → 复用


def test_get_agent_rebuild_only_requested_model_key(monkeypatch):
    calls = _stub_builder(monkeypatch)
    monkeypatch.setattr(state, "_mcp_fingerprint", lambda: "fp-A")
    m1 = state.get_agent("m1")
    m2 = state.get_agent("m2")
    assert len(calls) == 2

    monkeypatch.setattr(state, "_mcp_fingerprint", lambda: "fp-B")
    assert state.get_agent("m1") is not m1  # m1 重建
    assert len(calls) == 3  # 初始 m1+m2 两次 + m1 重建
    assert state._agents["m2"] is m2  # 未请求的键不被牵连（惰性，只丢被请求键）

    assert state.get_agent("m2") is not m2  # m2 被请求时才按新指纹重建
    assert len(calls) == 4


def test_reset_agent_cache_clears_fingerprint_table(monkeypatch):
    calls = _stub_builder(monkeypatch)
    monkeypatch.setattr(state, "_mcp_fingerprint", lambda: "fp-A")
    state.get_agent("m1")
    assert state._mcp_fps == {"m1": "fp-A"}

    state.reset_agent_cache()
    assert state._mcp_fps == {}  # 全清语义保持
    assert state.get_agent("m1") is not None
    assert len(calls) == 2  # 缓存已空：同指纹也重建
