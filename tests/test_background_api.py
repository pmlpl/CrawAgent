"""背景图服务端存储 API 测试（ADR-0001）：monkeypatch 到临时目录，不碰真实 data/。"""
import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crawagent.web.routers import settings as settings_mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "_BG_IMAGE", tmp_path / "background.jpg")
    monkeypatch.setattr(settings_mod, "_BG_META", tmp_path / "background.json")
    app = FastAPI()
    app.include_router(settings_mod.router)
    return TestClient(app)


def _data_url(data: bytes = b"\xff\xd8\xff\xe0fake-jpeg-bytes") -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def test_get_background_empty(client):
    r = client.get("/api/background")
    assert r.status_code == 200
    assert r.json() == {"image": None, "opacity": 60}


def test_save_and_get_background(client):
    r = client.post("/api/background", json={"data_url": _data_url()})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["image"] and body["image"].startswith("/api/background/image?t=")
    assert settings_mod._BG_IMAGE.read_bytes().startswith(b"\xff\xd8")

    r2 = client.get("/api/background/image")
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "image/jpeg"


def test_save_rejects_non_jpeg_data_url(client):
    r = client.post("/api/background", json={"data_url": "data:image/png;base64,AAAA"})
    body = r.json()
    assert body["ok"] is False and "jpeg" in body["error"]


def test_save_rejects_bad_base64(client):
    r = client.post("/api/background", json={"data_url": "data:image/jpeg;base64,@@@%"})
    assert r.json()["ok"] is False


def test_save_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr(settings_mod, "_MAX_BG_BYTES", 8)
    r = client.post("/api/background", json={"data_url": _data_url(b"1234567890")})
    body = r.json()
    assert body["ok"] is False and "上限" in body["error"]


def test_opacity_roundtrip(client):
    r = client.post("/api/background/opacity", json={"opacity": 35})
    assert r.json() == {"ok": True, "opacity": 35}
    assert client.get("/api/background").json()["opacity"] == 35
    r2 = client.post("/api/background/opacity", json={"opacity": 999})
    assert r2.json()["ok"] is False


def test_clear_background(client):
    client.post("/api/background", json={"data_url": _data_url()})
    assert client.delete("/api/background").json()["ok"] is True
    assert client.get("/api/background").json()["image"] is None
    assert client.get("/api/background/image").status_code == 404
