"""变更 036 — 附件功能（后端）。

覆盖：POST /api/uploads（成功/重名序号/超限/文件名清洗/落点校验/非法会话 ID）、
server._build_attachment_block（注入格式/无附件零改动/非法条目剔除）。
read_file 工具的用例在 test_file_tool.py（同文件工具）。全部离线。
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------- fixture ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """uploads 路由挂独立 app；data 根指到 tmp_path（不碰真实 data/）。"""
    import crawagent.web.routers.uploads as up

    class _S:
        project_root = tmp_path

    monkeypatch.setattr(up, "get_settings", lambda: _S())
    app = FastAPI()
    app.include_router(up.router)
    return TestClient(app), tmp_path / "data" / "uploads"


# ---------- POST /api/uploads ----------

def test_upload_success(client):
    tc, root = client
    r = tc.post(
        "/api/uploads",
        files={"file": ("笔记.txt", "你好附件".encode("utf-8"), "text/plain")},
        data={"session_id": "sess_up1"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["name"] == "笔记.txt"
    assert data["size"] == 12  # 4 个中文 × 3 字节
    p = Path(data["path"])
    assert p.read_bytes() == "你好附件".encode("utf-8")
    assert p.is_relative_to(root) and p.parent == root / "sess_up1"


def test_upload_duplicate_gets_serial(client):
    """重名上传两份 → 第二份落 -1 序号，第一份内容不被覆盖。"""
    tc, root = client
    up1 = tc.post("/api/uploads", files={"file": ("data.json", b'{"v":1}', "application/json")},
                  data={"session_id": "s"}).json()
    up2 = tc.post("/api/uploads", files={"file": ("data.json", b'{"v":2}', "application/json")},
                  data={"session_id": "s"}).json()
    up3 = tc.post("/api/uploads", files={"file": ("data.json", b'{"v":3}', "application/json")},
                  data={"session_id": "s"}).json()
    assert up1["name"] == "data.json"
    assert up2["name"] == "data-1.json"
    assert up3["name"] == "data-2.json"
    assert (root / "s" / "data.json").read_bytes() == b'{"v":1}'  # 原件不动
    assert (root / "s" / "data-1.json").read_bytes() == b'{"v":2}'


def test_upload_over_limit_rejected(client, monkeypatch):
    """超上限被人话错误拒绝，残留半截文件被清掉。"""
    import crawagent.web.routers.uploads as up

    tc, root = client
    monkeypatch.setattr(up, "MAX_UPLOAD_BYTES", 8)
    r = tc.post("/api/uploads", files={"file": ("big.bin", b"x" * 100, "application/octet-stream")},
                data={"session_id": "s"}).json()
    assert r["ok"] is False
    assert "上限" in r["error"]
    assert not (root / "s" / "big.bin").exists()


def test_upload_filename_sanitized_and_contained(client):
    """文件名清洗（非法字符/路径成分剥掉）+ 落点恒在 uploads 根内。"""
    tc, root = client
    r = tc.post("/api/uploads", files={"file": ("../../evil<b>.txt", b"x", "text/plain")},
                data={"session_id": "s"}).json()
    assert r["ok"] is True
    p = Path(r["path"])
    assert p.is_relative_to(root / "s")  # 不逃逸
    assert "<" not in p.name and "/" not in p.name and "\\" not in p.name
    # 带路径成分的名字落到会话目录内（basename 化），不存在于根外
    assert p.exists()


def test_upload_bad_session_id(client):
    tc, _ = client
    for sid in ("", "a/b", "a b", "x" * 200):
        r = tc.post("/api/uploads", files={"file": ("a.txt", b"x", "text/plain")},
                    data={"session_id": sid}).json()
        assert r["ok"] is False, sid


def test_upload_long_name_keeps_ext(client):
    """超长文件名截断 stem 但保留扩展名（AI 靠它判断 pdf/docx 走转换）。"""
    tc, root = client
    long_name = "很" * 100 + ".pdf"
    r = tc.post("/api/uploads", files={"file": (long_name, b"%PDF", "application/pdf")},
                data={"session_id": "s"}).json()
    assert r["ok"] is True
    assert r["name"].endswith(".pdf")
    assert len(Path(r["name"]).stem) <= 60


# ---------- _build_attachment_block（chat_ws 注入格式） ----------

def test_attachment_block_format():
    from crawagent.web.server import _build_attachment_block

    block = _build_attachment_block([
        {"name": "笔记.txt", "path": "D:/data/uploads/s1/笔记.txt", "size": 12},
    ])
    assert block is not None
    assert block.startswith("[附件]（用 read_file 工具读取内容")
    assert "markitdown_convert" in block
    assert "- D:/data/uploads/s1/笔记.txt（笔记.txt，12 字节）" in block


def test_attachment_block_empty_cases():
    from crawagent.web.server import _build_attachment_block

    assert _build_attachment_block(None) is None
    assert _build_attachment_block([]) is None
    assert _build_attachment_block("not a list") is None
    # 全是非法条目 → 视为无附件
    assert _build_attachment_block(["junk", {"no_path": 1}, {"path": "  "}]) is None


def test_attachment_block_skips_bad_entries_and_fills_defaults():
    from crawagent.web.server import _build_attachment_block

    block = _build_attachment_block([
        "junk",
        {"path": "D:/x/a.log"},                     # 无 name/size → 兜底
        {"name": "b.bin", "path": "D:/x/b.bin", "size": "big"},  # size 非整数
    ])
    assert "junk" not in block
    assert "- D:/x/a.log（a.log，大小未知）" in block
    assert "（b.bin，大小未知）" in block
