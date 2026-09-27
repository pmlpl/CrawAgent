"""浏览器子 Agent 模型设置（032）：snapshot 暴露三字段 + 保存写 .env + key 脱敏跳过。

照 010 LangSmith 测试模式（test_settings_langsmith.py）：ENV_FILE monkeypatch 到临时
文件 + Settings.model_config.env_file 同指 + 清 os.environ 的 BROWSER_USE_LLM_*
（pydantic env vars 优先于 .env，会盖住临时文件），不碰真实 .env。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crawagent.config.settings import Settings
from crawagent.web.routers import settings as settings_mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    monkeypatch.setattr(settings_mod.core, "ENV_FILE", env_file)
    monkeypatch.setitem(Settings.model_config, "env_file", env_file)
    for k in ("BROWSER_USE_LLM_MODEL", "BROWSER_USE_LLM_BASE_URL", "BROWSER_USE_LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    app = FastAPI()
    app.include_router(settings_mod.router)
    return TestClient(app)


def _env_text():
    return settings_mod.core.ENV_FILE.read_text(encoding="utf-8")


def test_snapshot_exposes_browser_use_fields(client):
    """GET /api/settings 返回三字段，默认全空（= 跟随主模型）。"""
    data = client.get("/api/settings").json()
    assert data["browser_use_llm_model"] == ""
    assert data["browser_use_llm_base_url"] == ""
    assert data["browser_use_llm_api_key"] == ""


def test_snapshot_masks_api_key(client):
    """已配置 key 时 snapshot 回显脱敏掩码（****+末4位），不出明文。"""
    settings_mod.core.ENV_FILE.write_text(
        "BROWSER_USE_LLM_MODEL=glm-4v-flash\nBROWSER_USE_LLM_API_KEY=sk-real-secret-1234\n",
        encoding="utf-8",
    )
    data = client.get("/api/settings").json()
    assert data["browser_use_llm_model"] == "glm-4v-flash"
    assert data["browser_use_llm_api_key"] == "****1234"
    assert "real-secret" not in data["browser_use_llm_api_key"]


def test_save_browser_use_writes_env(client):
    """POST 三字段 → .env 出现三键。"""
    r = client.post("/api/settings", json={
        "browser_use_llm_model": "glm-4v-flash",
        "browser_use_llm_base_url": "http://100.83.19.7:8085/v1",
        "browser_use_llm_api_key": "sk-sub-agent",
    })
    assert r.json()["ok"] is True
    text = _env_text()
    assert "BROWSER_USE_LLM_MODEL=glm-4v-flash" in text
    assert "BROWSER_USE_LLM_BASE_URL=http://100.83.19.7:8085/v1" in text
    assert "BROWSER_USE_LLM_API_KEY=sk-sub-agent" in text


def test_save_browser_use_masked_key_skipped(client):
    """含 * 的掩码 key 不回写：原值保留；model/base_url 照写。"""
    settings_mod.core.ENV_FILE.write_text("BROWSER_USE_LLM_API_KEY=sk-keep-me-9999\n", encoding="utf-8")
    r = client.post("/api/settings", json={
        "browser_use_llm_model": "glm-4v-flash",
        "browser_use_llm_api_key": "****9999",
    })
    assert r.json()["ok"] is True
    text = _env_text()
    assert "BROWSER_USE_LLM_API_KEY=sk-keep-me-9999" in text  # 原值保留
    assert "****9999" not in text
    assert "BROWSER_USE_LLM_MODEL=glm-4v-flash" in text


def test_save_browser_use_empty_clears_follow_main(client):
    """model/base_url 清空保存 = 清除 .env 键（恢复跟随主模型）；key 同语义。"""
    settings_mod.core.ENV_FILE.write_text(
        "BROWSER_USE_LLM_MODEL=glm-4v-flash\n"
        "BROWSER_USE_LLM_BASE_URL=http://x/v1\n"
        "BROWSER_USE_LLM_API_KEY=sk-old\n",
        encoding="utf-8",
    )
    r = client.post("/api/settings", json={
        "browser_use_llm_model": "",
        "browser_use_llm_base_url": "",
        "browser_use_llm_api_key": "",
    })
    assert r.json()["ok"] is True
    text = _env_text()
    assert "BROWSER_USE_LLM_MODEL=\n" in text
    assert "BROWSER_USE_LLM_BASE_URL=\n" in text
    assert "BROWSER_USE_LLM_API_KEY=\n" in text
    assert "glm-4v-flash" not in text
    assert "sk-old" not in text


def test_save_browser_use_unsubmitted_keys_untouched(client):
    """未提交的键不写入（partial payload 不清掉无关配置）。"""
    settings_mod.core.ENV_FILE.write_text("BROWSER_USE_LLM_MODEL=glm-4v-flash\n", encoding="utf-8")
    r = client.post("/api/settings", json={"thinking_depth": "low"})
    assert r.json()["ok"] is True
    assert "BROWSER_USE_LLM_MODEL=glm-4v-flash" in _env_text()
