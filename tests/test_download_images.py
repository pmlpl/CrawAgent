"""download_images 测试 — URL 解析 / 后缀推断 / 文件名清洗 / 集成下载（mock HTTP）。

依赖测试约定：monkeypatch settings 让 downloads_dir 指向 tmp_path；mock requests.get 拦截网络。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import download_images


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_settings(tmp_path, monkeypatch):
    class _S:
        project_root = tmp_path
        downloads_dir = tmp_path / "downloads"
    (tmp_path / "downloads").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(download_images, "get_settings", lambda: _S())
    return _S


# ---------------------------------------------------------------------------
# _split_urls
# ---------------------------------------------------------------------------

def test_split_urls_single():
    """单 URL → 列表。"""
    assert download_images._split_urls("https://example.com/a.jpg") == ["https://example.com/a.jpg"]


def test_split_urls_comma_separated():
    """逗号分隔。"""
    out = download_images._split_urls("https://a.com/1.jpg,https://b.com/2.jpg")
    assert len(out) == 2
    assert "https://a.com/1.jpg" in out


def test_split_urls_newline_separated():
    """换行分隔。"""
    out = download_images._split_urls("https://a.com/1.jpg\nhttps://b.com/2.jpg")
    assert len(out) == 2


def test_split_urls_json_array():
    """JSON 数组字符串。"""
    out = download_images._split_urls('["https://a.com/1.jpg","https://b.com/2.jpg"]')
    assert len(out) == 2


def test_split_urls_filters_empty():
    """空项过滤。"""
    out = download_images._split_urls("https://a.com/1.jpg, , ")
    assert out == ["https://a.com/1.jpg"]


def test_split_urls_invalid_json_treated_as_single():
    """非 JSON 字符串 → 当单 URL 处理（不抛）。"""
    out = download_images._split_urls("not-a-json-array")
    assert out == ["not-a-json-array"]


# ---------------------------------------------------------------------------
# _detect_ext
# ---------------------------------------------------------------------------

def test_detect_ext_from_url():
    """URL 含扩展名 → 用 URL 扩展名。"""
    ext = download_images._detect_ext("https://example.com/photo.jpg", "")
    assert ext == ".jpg"


def test_detect_ext_from_content_type():
    """URL 无扩展名 + content-type → 用 content-type。"""
    ext = download_images._detect_ext("https://example.com/photo", "image/png")
    assert ext == ".png"


def test_detect_ext_content_type_priority_over_url():
    """实际实现：content-type 优先于 URL（因为 ct 更准确）。"""
    ext = download_images._detect_ext("https://example.com/photo.jpg", "image/png")
    assert ext == ".png"


def test_detect_ext_fallback_to_bin():
    """无扩展名 + 无 content-type → fallback .bin。"""
    ext = download_images._detect_ext("https://example.com/photo", "")
    assert ext == ".bin"


# ---------------------------------------------------------------------------
# _safe_stem
# ---------------------------------------------------------------------------

def test_safe_stem_strips_illegal_chars():
    """文件名非法字符替换为 ``_``。"""
    stem = download_images._safe_stem("https://example.com/a:b/c*d?.jpg")
    # \ / : * ? < > | 等 Windows 非法字符
    assert "/" not in stem
    assert ":" not in stem
    assert "*" not in stem
    assert "?" not in stem


def test_safe_stem_truncates_long():
    """超长 stem 截断。"""
    long_url = "https://example.com/" + ("a" * 500) + ".jpg"
    stem = download_images._safe_stem(long_url)
    # 函数可能限制 80 字符
    assert len(stem) <= 80


# ---------------------------------------------------------------------------
# _unique_path
# ---------------------------------------------------------------------------

def test_unique_path_no_collision(fake_settings):
    """文件不存在 → 直接用。"""
    out_dir = fake_settings.downloads_dir
    p = download_images._unique_path(out_dir, "test", ".jpg")
    assert p.name == "test.jpg"


def test_unique_path_collision_appends_counter(fake_settings):
    """文件已存在 → 加 ``_1`` / ``_2``。"""
    out_dir = fake_settings.downloads_dir
    (out_dir / "test.jpg").write_text("existing")
    p = download_images._unique_path(out_dir, "test", ".jpg")
    assert p.name == "test_1.jpg"


def test_unique_path_two_collisions(fake_settings):
    """多个同名文件 → 计数到 _2。"""
    out_dir = fake_settings.downloads_dir
    (out_dir / "test.jpg").write_text("1")
    (out_dir / "test_1.jpg").write_text("2")
    p = download_images._unique_path(out_dir, "test", ".jpg")
    assert p.name == "test_2.jpg"


# ---------------------------------------------------------------------------
# download_images 集成（mock HTTP）
# ---------------------------------------------------------------------------

def test_download_images_no_urls_returns_error(fake_settings):
    """空 URL 列表 → 错误信息。"""
    out = download_images.download_images.func("")
    assert "no valid urls" in out


def test_download_images_subdir_escape_blocked(fake_settings, tmp_path):
    """subdir 路径逃逸 → 拒绝。"""
    out = download_images.download_images.func("https://example.com/a.jpg", subdir="../escape")
    assert "escapes" in out or "escape" in out or "failed" in out


def test_download_images_success(fake_settings):
    """正常下载 → 文件被写入 tmp_path。"""
    fake_resp = MagicMock()
    fake_resp.headers = {"content-type": "image/jpeg"}
    fake_resp.iter_content.return_value = [b"\xff\xd8\xff\xe0JFIF", b"more bytes"]
    fake_resp.raise_for_status = MagicMock()

    with patch.object(download_images.requests, "get", return_value=fake_resp):
        out = download_images.download_images.func(
            "https://example.com/photo.jpg", subdir="batch1"
        )
    assert "1/1" in out or "succeeded" in out
    # 验证文件已写入
    files = list(fake_settings.downloads_dir.glob("batch1/*"))
    assert len(files) == 1
    assert files[0].suffix == ".jpg"


def test_download_images_failure_recorded(fake_settings):
    """下载失败 → 记录到 failures。"""
    import requests as real_requests
    with patch.object(download_images.requests, "get", side_effect=real_requests.ConnectionError("timeout")):
        out = download_images.download_images.func("https://example.com/photo.jpg")
    assert "0/1" in out or "failed" in out
    assert "timeout" in out