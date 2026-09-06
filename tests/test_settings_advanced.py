"""高级页设置（T2）：抓取参数 / 保留清理策略的保存与校验测试。
ENV_FILE monkeypatch 到临时文件，不碰真实 .env。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crawagent.config.settings import Settings
from crawagent.web.routers import settings as settings_mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "ENV_FILE", tmp_path / "env")
    app = FastAPI()
    app.include_router(settings_mod.router)
    return TestClient(app)


def _env_text():
    return settings_mod.ENV_FILE.read_text(encoding="utf-8")


def test_snapshot_contains_advanced_fields(client):
    data = client.get("/api/settings").json()
    for key in ("request_timeout", "request_delay", "output_dir", "downloads_dir",
                "logs_retention_days", "output_retention_days", "downloads_retention_days",
                "output_max_size_gb", "downloads_max_size_gb"):
        assert key in data, f"快照缺少 {key}"


def test_save_crawl_params_roundtrip(client):
    r = client.post("/api/settings", json={"request_timeout": 45, "request_delay": 2.5})
    assert r.json()["ok"] is True
    text = _env_text()
    assert "REQUEST_TIMEOUT=45" in text
    assert "REQUEST_DELAY=2.5" in text


def test_save_crawl_params_rejects_empty(client):
    # 必填参数清空必须拒绝：落盘 "None" 会让下次启动的 pydantic 解析直接崩
    for empty in ("", None):
        r = client.post("/api/settings", json={"request_timeout": empty})
        body = r.json()
        assert body["ok"] is False and "不能为空" in body["error"], (empty, body)


def test_save_crawl_params_rejects_out_of_range(client):
    r = client.post("/api/settings", json={"request_timeout": 0})
    body = r.json()
    assert body["ok"] is False and "5" in body["error"]
    r = client.post("/api/settings", json={"request_delay": -1})
    body = r.json()
    assert body["ok"] is False and "0" in body["error"]


def test_save_retention_days_and_caps(client):
    r = client.post("/api/settings", json={
        "logs_retention_days": 7,
        "downloads_retention_days": 90,
        "downloads_max_size_gb": 10,
    })
    assert r.json()["ok"] is True
    text = _env_text()
    assert "LOGS_RETENTION_DAYS=7" in text
    assert "DOWNLOADS_RETENTION_DAYS=90" in text
    assert "DOWNLOADS_MAX_SIZE_GB=10" in text
    # 未提交的键不写入
    assert "OUTPUT_RETENTION_DAYS" not in text


def test_retention_null_means_never_clean(client):
    r = client.post("/api/settings", json={"output_retention_days": None})
    assert r.json()["ok"] is True
    assert "OUTPUT_RETENTION_DAYS=null" in _env_text()


def test_retention_rejects_out_of_range(client):
    r = client.post("/api/settings", json={"logs_retention_days": 99999})
    body = r.json()
    assert body["ok"] is False
    r = client.post("/api/settings", json={"output_max_size_gb": 0})
    body = r.json()
    assert body["ok"] is False


def test_py_parses_null_env_as_never_clean(tmp_path):
    env = tmp_path / "env"
    env.write_text("OUTPUT_RETENTION_DAYS=null\nDOWNLOADS_MAX_SIZE_GB=null\n", encoding="utf-8")
    s = Settings(_env_file=env)
    assert s.output_retention_days is None
    assert s.downloads_max_size_gb is None


def test_open_folder_whitelist_includes_artifacts(client, monkeypatch):
    opened = []
    import os
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(str(p)), raising=False)
    r = client.post("/api/ecosystem/open-folder", json={"folder": "output"})
    body = r.json()
    assert body["ok"] is True
    assert len(opened) == 1
    r = client.post("/api/ecosystem/open-folder", json={"folder": "downloads"})
    assert r.json()["ok"] is True
    r = client.post("/api/ecosystem/open-folder", json={"folder": "c:/windows"})
    assert r.json()["ok"] is False


def test_save_dirs_roundtrip_creates_and_writes(client, tmp_path):
    out = tmp_path / "产物" / "output"
    dl = tmp_path / "媒体" / "downloads"
    r = client.post("/api/settings", json={"output_dir": str(out), "downloads_dir": str(dl)})
    assert r.json()["ok"] is True
    assert out.is_dir() and dl.is_dir()  # 目录当场创建，「打开目录」按钮立刻可用
    text = _env_text()
    assert f"OUTPUT_DIR={out}" in text
    assert f"DOWNLOADS_DIR={dl}" in text


def test_save_dirs_rejects_empty(client):
    r = client.post("/api/settings", json={"output_dir": ""})
    body = r.json()
    assert body["ok"] is False and "不能为空" in body["error"]


def test_save_dirs_resolves_relative_against_project_root(client):
    import shutil
    from pathlib import Path
    name = "output_dir_relative_test_tmp"
    root = Path(settings_mod.__file__).resolve().parents[3]
    try:
        r = client.post("/api/settings", json={"output_dir": name})
        assert r.json()["ok"] is True
        line = [l for l in _env_text().splitlines() if l.startswith("OUTPUT_DIR=")][0]
        assert line == f"OUTPUT_DIR={root / name}"  # 相对路径已解析为项目根下绝对路径
    finally:
        shutil.rmtree(root / name, ignore_errors=True)
