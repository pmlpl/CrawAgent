"""/downloads 静态服务（变更 012）：StaticFiles mount 支持 Range seek + 防路径穿越。
与 server.py main() 同款 mount，此处用独立 FastAPI app 验证机制。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles


def _client(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "v.mp4").write_bytes(b"0123456789" * 10)  # 100B
    app = FastAPI()
    app.mount("/downloads", StaticFiles(directory=str(dl)), name="downloads")
    return TestClient(app), dl


def test_serve_file_200(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/downloads/v.mp4")
    assert r.status_code == 200
    assert len(r.content) == 100


def test_range_request_206(tmp_path):
    """Range 请求返 206 Partial + Content-Range（<video> 拖进度条靠这个）。"""
    client, _ = _client(tmp_path)
    r = client.get("/downloads/v.mp4", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert len(r.content) == 10
    assert r.headers.get("content-range") == "bytes 10-19/100"


def test_path_traversal_rejected(tmp_path):
    """路径穿越 .. 拒绝（StaticFiles 内置防护）。"""
    client, dl = _client(tmp_path)
    secret = dl.parent / "secret.txt"
    secret.write_text("nope")
    # StaticFiles 对 .. 归一化后超出根 → 404
    r = client.get("/downloads/../secret.txt")
    assert r.status_code in (404, 400)
