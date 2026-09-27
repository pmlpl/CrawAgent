"""模型发现与两维探测端点（033）：discover / probe / caps-manual + caps 持久化。

httpx 与 probe 函数全部 mock，不发真实网络；ENV_FILE 钉临时文件不碰真实 .env。
"""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crawagent.config.settings import Settings
from crawagent.web.routers import settings as settings_mod

PROVIDERS = (
    '[{"name":"中转","base_url":"http://relay.example/v1","api_key":"sk-real",'
    '"models":["glm-5.2"]}]'
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    env_file.write_text(f"LLM_PROVIDERS={PROVIDERS}\n", encoding="utf-8")
    monkeypatch.setattr(settings_mod.core, "ENV_FILE", env_file)
    monkeypatch.setitem(Settings.model_config, "env_file", env_file)
    app = FastAPI()
    app.include_router(settings_mod.router)
    return TestClient(app), env_file


# ── discover ──

def _mock_httpx(monkeypatch, *, status=200, payload=None, exc=None):
    """全局替换 httpx.get（路由函数内 import httpx，取到的 .get 即此假货）。"""
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured.update(url=url, headers=headers, timeout=timeout)
        if exc is not None:
            raise exc
        return httpx.Response(status_code=status, json=payload if payload is not None else {"data": []})

    monkeypatch.setattr(httpx, "get", fake_get)
    return captured


def test_discover_ok(client, monkeypatch):
    """200 + OpenAI 结构 → sorted [{id, owned_by}]；鉴权头带上。"""
    captured = _mock_httpx(monkeypatch, payload={"data": [
        {"id": "b-model", "owned_by": "z"}, {"id": "a-model", "owned_by": "x"},
    ]})
    tc, _ = client
    r = tc.post("/api/models/discover", json={"provider": "中转"}).json()
    assert r["ok"] is True
    assert r["models"] == [{"id": "a-model", "owned_by": "x"}, {"id": "b-model", "owned_by": "z"}]
    assert captured["url"] == "http://relay.example/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer sk-real"  # 服务端取已存 Key


def test_discover_auth_and_404_and_unreachable(client, monkeypatch):
    tc, _ = client
    _mock_httpx(monkeypatch, status=401)
    r = tc.post("/api/models/discover", json={"provider": "中转"}).json()
    assert r["ok"] is False and "401" in r["error"]

    _mock_httpx(monkeypatch, status=404)
    r = tc.post("/api/models/discover", json={"provider": "中转"}).json()
    assert r["ok"] is False and "404" in r["error"] and "OpenAI 兼容" in r["error"]

    _mock_httpx(monkeypatch, exc=httpx.ConnectError("refused"))
    r = tc.post("/api/models/discover", json={"provider": "中转"}).json()
    assert r["ok"] is False and "连不上" in r["error"]

    _mock_httpx(monkeypatch, payload={"data": "not-a-list"})
    r = tc.post("/api/models/discover", json={"provider": "中转"}).json()
    assert r["ok"] is False and "结构" in r["error"]


def test_discover_missing_base(client, monkeypatch):
    tc, env_file = client
    env_file.write_text("LLM_PROVIDERS=[]\n", encoding="utf-8")
    r = tc.post("/api/models/discover", json={"provider": "不存在"}).json()
    assert r["ok"] is False and "Base URL" in r["error"]


# ── probe ──

def _mock_probes(monkeypatch, vision=(False, "黑色"), structured=(True, '{"answer":"ok"}')):
    """patch 探测模块本体（路由内 from-import 在调用时解析，patch core 命名空间无效）。"""
    import crawagent.llm.probe as probe_mod
    calls = []
    monkeypatch.setattr(probe_mod, "probe_vision",
                        lambda b, k, m: (calls.append(("vision", b, k, m)), vision)[1])
    monkeypatch.setattr(probe_mod, "probe_structured",
                        lambda b, k, m: (calls.append(("structured", b, k, m)), structured)[1])
    return calls


def test_probe_runs_two_dims_and_persists_caps(client, monkeypatch):
    """两维一次跑完，结果带原话 + 持久化进 provider caps（模型原话留证）。"""
    tc, env_file = client
    calls = _mock_probes(monkeypatch, vision=(False, "黑色"), structured=(True, '{"answer":"ok"}'))
    r = tc.post("/api/models/probe", json={"provider": "中转", "model": "glm-5.2"}).json()
    assert r["ok"] is True
    assert r["vision"] is False and r["vision_reply"] == "黑色"
    assert r["structured"] is True and r["structured_reply"] == '{"answer":"ok"}'
    assert ("vision", "http://relay.example/v1", "sk-real", "glm-5.2") in calls

    text = env_file.read_text(encoding="utf-8")
    assert '"vision": false' in text and '"黑色"' in text  # caps 落盘 LLM_PROVIDERS
    assert '"structured": true' in text


def test_probe_result_overwrites_and_reaches_snapshot(client, monkeypatch):
    """重复探测覆盖旧结果；GET /api/settings 的 models[].caps 反映最新值。"""
    tc, _ = client
    _mock_probes(monkeypatch, vision=(False, "黑色"), structured=(False, "不支持"))
    tc.post("/api/models/probe", json={"provider": "中转", "model": "glm-5.2"})
    _mock_probes(monkeypatch, vision=(True, "青色"), structured=(True, '{"answer":"ok"}'))
    tc.post("/api/models/probe", json={"provider": "中转", "model": "glm-5.2"})

    models = tc.get("/api/settings").json()["models"]
    caps = next(m["caps"] for m in models if m["name"] == "glm-5.2")
    assert caps["vision"] is True and caps["vision_reply"] == "青色"
    assert caps["structured"] is True


def test_probe_masked_key_falls_back_to_stored(client, monkeypatch):
    """前端传掩码 key → 视同留空，用已存 Key（明文不出后端）。"""
    tc, _ = client
    calls = _mock_probes(monkeypatch)
    tc.post("/api/models/probe", json={
        "provider": "中转", "model": "glm-5.2", "api_key": "****-real",
    })
    assert calls[0][2] == "sk-real"


def test_caps_manual_persists(client):
    """视觉手动勾选落盘 + snapshot 反映 vision_manual。"""
    tc, env_file = client
    r = tc.post("/api/models/caps/manual", json={
        "provider": "中转", "model": "glm-5.2", "vision_manual": True,
    }).json()
    assert r["ok"] is True
    caps = next(m["caps"] for m in r["models"] if m["name"] == "glm-5.2")
    assert caps["vision_manual"] is True and caps["vision_source"] == "manual"
    assert '"vision_manual": true' in env_file.read_text(encoding="utf-8")
