"""LangSmith 追踪面板（010）：snapshot 暴露三字段 + 保存写 .env + key 脱敏跳过。
ENV_FILE monkeypatch 到临时文件 + Settings.model_config.env_file 同指 + 清 os.environ，
不碰真实 .env。文件级断言为主（与 test_settings_advanced 同款），避开 get_settings 的
os.environ.setdefault 污染（那是 langchain 运行时机制，重启才生效，不在单测范畴）。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crawagent.config.settings import Settings
from crawagent.web.routers import settings as settings_mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    monkeypatch.setattr(settings_mod, "ENV_FILE", env_file)
    # Settings.model_config.env_file 钉死项目根 .env（line 47），须改指临时文件；
    # 同时清 os.environ 的 LANGSMITH_*（pydantic env vars 优先于 .env，会盖住临时文件）
    monkeypatch.setitem(Settings.model_config, "env_file", env_file)
    for k in ("LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_TRACING"):
        monkeypatch.delenv(k, raising=False)
    app = FastAPI()
    app.include_router(settings_mod.router)
    return TestClient(app)


def _env_text():
    return settings_mod.ENV_FILE.read_text(encoding="utf-8")


def test_snapshot_exposes_langsmith_fields(client):
    """GET /api/settings 必须返回 langsmith_api_key（脱敏）/ project / tracing 三字段，默认值正确。"""
    data = client.get("/api/settings").json()
    assert "langsmith_api_key" in data
    assert "langsmith_project" in data
    assert "langsmith_tracing" in data
    # 默认（临时空 env + os.environ 已清）：key 空串、project=crawagent、tracing=False
    assert data["langsmith_api_key"] == ""
    assert data["langsmith_project"] == "crawagent"
    assert data["langsmith_tracing"] is False


def test_snapshot_masks_api_key(client):
    """已配置 key 时 snapshot 回显脱敏掩码（****+末4位），不出明文。"""
    settings_mod.ENV_FILE.write_text("LANGSMITH_API_KEY=sk-real-secret-1234\n", encoding="utf-8")
    data = client.get("/api/settings").json()
    assert data["langsmith_api_key"] == "****1234"
    assert "real-secret" not in data["langsmith_api_key"]


def test_save_langsmith_writes_env(client):
    """POST 三字段 → .env 出现 LANGSMITH_API_KEY/PROJECT/TRACING。"""
    r = client.post("/api/settings", json={
        "langsmith_api_key": "sk-test-key-xyz",
        "langsmith_project": "my-proj",
        "langsmith_tracing": True,
    })
    assert r.json()["ok"] is True
    text = _env_text()
    assert "LANGSMITH_API_KEY=sk-test-key-xyz" in text
    assert "LANGSMITH_PROJECT=my-proj" in text
    assert "LANGSMITH_TRACING=true" in text


def test_save_langsmith_masked_key_skipped(client):
    """含 * 的掩码 key 不回写：先写真实 key，再用掩码保存，原值不变。"""
    settings_mod.ENV_FILE.write_text("LANGSMITH_API_KEY=sk-keep-me-9999\n", encoding="utf-8")
    r = client.post("/api/settings", json={
        "langsmith_api_key": "****9999",  # 掩码回传，应跳过
        "langsmith_tracing": True,
    })
    assert r.json()["ok"] is True
    text = _env_text()
    assert "LANGSMITH_API_KEY=sk-keep-me-9999" in text  # 原值保留
    assert "****9999" not in text
    assert "LANGSMITH_TRACING=true" in text  # 其他字段照写


def test_save_langsmith_tracing_false_writes_string(client):
    """tracing=False 落盘 "false" 字符串（匹配 get_settings 的 env 导出格式）。"""
    client.post("/api/settings", json={"langsmith_tracing": True})
    assert "LANGSMITH_TRACING=true" in _env_text()
    client.post("/api/settings", json={"langsmith_tracing": False})
    assert "LANGSMITH_TRACING=false" in _env_text()
    assert "LANGSMITH_TRACING=true" not in _env_text()


def test_save_langsmith_empty_key_does_not_blank(client):
    """key 留空 = 不改（不写空串覆盖原值），与 LLM key 同款语义。"""
    settings_mod.ENV_FILE.write_text("LANGSMITH_API_KEY=sk-existing-aaaa\n", encoding="utf-8")
    client.post("/api/settings", json={"langsmith_api_key": ""})
    assert "LANGSMITH_API_KEY=sk-existing-aaaa" in _env_text()
