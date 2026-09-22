"""social_tool 测试 — 平台识别 / JSON 输出 / 路径逃逸拦截。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import social_tool


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_settings(tmp_path, monkeypatch):
    class _S:
        project_root = tmp_path
        downloads_dir = tmp_path / "downloads"
    (tmp_path / "downloads").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(social_tool, "get_settings", lambda: _S())
    return _S


# ---------------------------------------------------------------------------
# _extract_data 平台识别
# ---------------------------------------------------------------------------

def test_extract_data_routes_to_bilibili_for_b23_tv(monkeypatch):
    """b23.tv 短链 → bilibili_extract。"""
    with patch.object(social_tool, "bilibili_extract", return_value={"platform": "bilibili"}) as mock_b:
        result = social_tool._extract_data("https://b23.tv/abc", {"metadata"})
    assert result["platform"] == "bilibili"
    mock_b.assert_called_once()


def test_extract_data_routes_to_bilibili_for_bilibili_com(monkeypatch):
    """bilibili.com 长链 → bilibili_extract。"""
    with patch.object(social_tool, "bilibili_extract", return_value={"platform": "bilibili"}) as mock_b:
        result = social_tool._extract_data("https://www.bilibili.com/video/BV12345", {"metadata"})
    assert result["platform"] == "bilibili"


def test_extract_data_routes_to_douyin(monkeypatch):
    """douyin.com / iesdouyin.com → douyin_extract。"""
    with patch.object(social_tool, "douyin_extract", return_value={"platform": "douyin"}) as mock_d:
        result = social_tool._extract_data("https://v.douyin.com/abc/", {"metadata"})
    assert result["platform"] == "douyin"
    mock_d.assert_called_once()


def test_extract_data_unsupported_url_returns_error():
    """非抖音 / B 站 URL → error 信息。"""
    result = social_tool._extract_data("https://example.com/page", {"metadata"})
    assert "error" in result
    assert "Unsupported URL" in result["error"]


def test_extract_data_catches_exceptions(monkeypatch):
    """平台 extract 抛异常 → 包装 error，不传播。"""
    with patch.object(social_tool, "douyin_extract", side_effect=RuntimeError("network down")):
        result = social_tool._extract_data("https://v.douyin.com/abc/", {"metadata"})
    assert "error" in result
    assert "Extraction failed" in result["error"]
    assert "network down" in result["error"]


def test_extract_data_empty_wanted_falls_back_default(monkeypatch):
    """wanted 为空 → 默认 ``{metadata, comments, media}``。"""
    with patch.object(social_tool, "douyin_extract", return_value={}) as mock_d:
        social_tool._extract_data("https://v.douyin.com/abc/", set())
    mock_d.assert_called_once_with("https://v.douyin.com/abc/", {"metadata", "comments", "media"})


# ---------------------------------------------------------------------------
# extract_social_media
# ---------------------------------------------------------------------------

def test_extract_social_media_returns_json(monkeypatch):
    """返回 JSON 字符串（ensure_ascii=False）。"""
    with patch.object(social_tool, "_extract_data", return_value={"platform": "douyin", "title": "测试"}):
        out = social_tool.extract_social_media.func("https://v.douyin.com/abc/")
    data = json.loads(out)
    assert data["platform"] == "douyin"
    assert data["title"] == "测试"


def test_extract_social_media_default_fields(monkeypatch):
    """默认 fields = "metadata,comments,media"。"""
    with patch.object(social_tool, "_extract_data", return_value={}) as mock_e:
        social_tool.extract_social_media.func("https://example.com/")
    called_wanted = mock_e.call_args.args[1]
    assert called_wanted == {"metadata", "comments", "media"}


def test_extract_social_media_custom_fields(monkeypatch):
    """自定义 fields → wanted set。"""
    with patch.object(social_tool, "_extract_data", return_value={}) as mock_e:
        social_tool.extract_social_media.func("https://example.com/", fields="media,comments")
    called_wanted = mock_e.call_args.args[1]
    assert called_wanted == {"media", "comments"}


# ---------------------------------------------------------------------------
# download_social_media 路径安全
# ---------------------------------------------------------------------------

def test_download_social_media_extract_failure_returns_error_json(fake_settings, monkeypatch):
    """_extract_data 失败 → 返回 error JSON。"""
    with patch.object(social_tool, "_extract_data", return_value={"error": "Unsupported URL"}):
        out = social_tool.download_social_media.func("https://example.com/")
    data = json.loads(out)
    assert "error" in data


def test_download_social_media_subdir_escape_blocked(fake_settings, monkeypatch):
    """subdir 路径逃逸 → 返回 error JSON。"""
    with patch.object(social_tool, "_extract_data", return_value={
        "platform": "douyin", "aweme_id": "12345", "title": "x",
    }):
        out = social_tool.download_social_media.func(
            "https://v.douyin.com/abc/", subdir="../../escape"
        )
    data = json.loads(out)
    assert "error" in data
    assert "escapes" in data["error"].lower()