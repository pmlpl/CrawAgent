"""social_utils 测试 — 抖音 / B 站工具的共享 helper（HTTP / URL 解析 / 文件下载）。

依赖测试约定：monkeypatch settings + mock requests.get。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import social_utils


# ---------------------------------------------------------------------------
# UA 常量
# ---------------------------------------------------------------------------

def test_ua_constants_present():
    """MOBILE_UA / DESKTOP_UA 必须含 Chrome。"""
    assert "Chrome" in social_utils.MOBILE_UA
    assert "Chrome" in social_utils.DESKTOP_UA
    assert "Mobile" in social_utils.MOBILE_UA  # 移动端含 Mobile 关键字


# ---------------------------------------------------------------------------
# _get（HTTP 请求 + UA 默认值）
# ---------------------------------------------------------------------------

def test_get_default_uses_desktop_ua(monkeypatch):
    """无 headers → 默认 DESKTOP_UA。"""
    monkeypatch.setattr(social_utils, "get_settings", lambda: MagicMock(request_timeout=10))
    mock_resp = MagicMock()
    mock_resp.url = "https://example.com"
    with patch.object(social_utils.requests, "get", return_value=mock_resp) as mock_get:
        social_utils._get("https://example.com")
    assert mock_get.call_args.kwargs["headers"]["User-Agent"] == social_utils.DESKTOP_UA


def test_get_caller_headers_override_ua(monkeypatch):
    """caller 自定义 UA → 覆盖默认。"""
    monkeypatch.setattr(social_utils, "get_settings", lambda: MagicMock(request_timeout=10))
    mock_resp = MagicMock()
    with patch.object(social_utils.requests, "get", return_value=mock_resp) as mock_get:
        social_utils._get("https://example.com", headers={"User-Agent": "Custom"})
    assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "Custom"


def test_get_passes_params(monkeypatch):
    """params 透传。"""
    monkeypatch.setattr(social_utils, "get_settings", lambda: MagicMock(request_timeout=10))
    mock_resp = MagicMock()
    with patch.object(social_utils.requests, "get", return_value=mock_resp) as mock_get:
        social_utils._get("https://example.com", params={"q": "test"})
    assert mock_get.call_args.kwargs["params"] == {"q": "test"}


def test_get_uses_request_timeout_from_settings(monkeypatch):
    """timeout 用 settings.request_timeout。"""
    monkeypatch.setattr(social_utils, "get_settings", lambda: MagicMock(request_timeout=42))
    mock_resp = MagicMock()
    with patch.object(social_utils.requests, "get", return_value=mock_resp) as mock_get:
        social_utils._get("https://example.com")
    assert mock_get.call_args.kwargs["timeout"] == 42


# ---------------------------------------------------------------------------
# _resolve（重定向 → 最终 URL）
# ---------------------------------------------------------------------------

def test_resolve_returns_final_url_after_redirect(monkeypatch):
    """响应 url 字段（重定向后）→ 返回。"""
    monkeypatch.setattr(social_utils, "get_settings", lambda: MagicMock(request_timeout=10))
    mock_resp = MagicMock()
    mock_resp.url = "https://final.example.com/page"
    with patch.object(social_utils.requests, "get", return_value=mock_resp):
        final = social_utils._resolve("https://short.url/abc")
    assert final == "https://final.example.com/page"


# ---------------------------------------------------------------------------
# _extract_bvid / _extract_aweme_id
# ---------------------------------------------------------------------------

def test_extract_bvid():
    """B 站 URL 含 ``BV`` 号 → 提取。"""
    assert social_utils._extract_bvid("https://www.bilibili.com/video/BV1abc2345de") == "BV1abc2345de"


def test_extract_bvid_no_match():
    """无 BV 号 → 空。"""
    assert social_utils._extract_bvid("https://example.com/page") == ""


def test_extract_aweme_id_from_modal_id():
    """抖音 ``/video/<digits>`` → 提取。"""
    assert social_utils._extract_aweme_id("https://www.douyin.com/video/7123456789012345678") == "7123456789012345678"


def test_extract_aweme_id_from_query_param():
    """``?modal_id=<digits>`` → 提取。"""
    assert social_utils._extract_aweme_id("https://example.com/share?modal_id=7123456789") == "7123456789"


def test_extract_aweme_id_no_match():
    """无 modal_id → 空。"""
    assert social_utils._extract_aweme_id("https://example.com/page") == ""


# ---------------------------------------------------------------------------
# _safe_name
# ---------------------------------------------------------------------------

def test_safe_name_strips_illegal_chars():
    """Windows 非法字符替换。"""
    safe = social_utils._safe_name("hello/world:test*?")
    assert "/" not in safe
    assert ":" not in safe
    assert "*" not in safe
    assert "?" not in safe


def test_safe_name_empty_fallback():
    """空字符串 / None → "item" 默认。"""
    assert social_utils._safe_name("") == "item"
    assert social_utils._safe_name(None) == "item"  # type: ignore[arg-type]


def test_safe_name_truncates_long():
    """超长名称截断到 80 字符。"""
    long = "a" * 500
    assert len(social_utils._safe_name(long)) <= 80


# ---------------------------------------------------------------------------
# _suffix
# ---------------------------------------------------------------------------

def test_suffix_from_url_extension():
    """URL 扩展名 → 直接用。"""
    assert social_utils._suffix("https://example.com/video.mp4", "video") == ".mp4"


def test_suffix_from_kind_when_no_extension():
    """URL 无扩展名 → 按 kind 默认。"""
    assert social_utils._suffix("https://example.com/video", "video") == ".mp4"
    assert social_utils._suffix("https://example.com/audio", "audio") == ".mp3"
    assert social_utils._suffix("https://example.com/cover", "cover") == ".jpg"


def test_suffix_rejects_overly_long_extension():
    """扩展名 > 5 字符 → fallback 到 kind 默认。"""
    # .abcdefg 长度 8 超过 5 字符限制
    assert social_utils._suffix("https://example.com/file.abcdefg", "video") == ".mp4"


# ---------------------------------------------------------------------------
# _download_to_file（stream 下载）
# ---------------------------------------------------------------------------

def test_download_to_file_writes_content(monkeypatch, tmp_path):
    """下载文件 → 写入 out_path。"""
    monkeypatch.setattr(social_utils, "get_settings", lambda: MagicMock(request_timeout=10))
    mock_resp = MagicMock()
    mock_resp.iter_content.return_value = [b"hello ", b"world"]
    mock_resp.raise_for_status = MagicMock()
    out = tmp_path / "out.bin"
    with patch.object(social_utils.requests, "get", return_value=mock_resp):
        result = social_utils._download_to_file("https://example.com/file", out, headers={})
    assert result == out
    assert out.read_bytes() == b"hello world"


def test_download_to_file_passes_stream(monkeypatch, tmp_path):
    """stream=True 标志透传。"""
    monkeypatch.setattr(social_utils, "get_settings", lambda: MagicMock(request_timeout=10))
    mock_resp = MagicMock()
    mock_resp.iter_content.return_value = [b"x"]
    mock_resp.raise_for_status = MagicMock()
    out = tmp_path / "out.bin"
    with patch.object(social_utils.requests, "get", return_value=mock_resp) as mock_get:
        social_utils._download_to_file("https://example.com/file", out, headers={})
    assert mock_get.call_args.kwargs["stream"] is True


# ---------------------------------------------------------------------------
# _find_item（递归找 Douyin item dict）
# ---------------------------------------------------------------------------

def test_find_item_dict_with_aweme_id():
    """dict 含 aweme_id（数字）+ video/images → 返回该 dict。"""
    obj = {"aweme_id": "7123456789", "video": {"url": "..."}}
    assert social_utils._find_item(obj) == obj


def test_find_item_nested_in_list():
    """list 中嵌套 dict 含 aweme_id → 递归找到。"""
    obj = [{"other": 1}, {"aweme_id": "12345", "video": "..."}]
    found = social_utils._find_item(obj)
    assert found is not None
    assert found["aweme_id"] == "12345"


def test_find_item_no_match_returns_none():
    """无 aweme_id → None。"""
    assert social_utils._find_item({"other": 1}) is None
    assert social_utils._find_item([]) is None


def test_find_item_skips_non_digit_aweme_id():
    """aweme_id 非数字字符串（PUA 字符等）→ 跳过。"""
    obj = {"aweme_id": "abc123", "video": "..."}
    # "abc123" 不是纯数字 → 跳过
    assert social_utils._find_item(obj) is None


# ---------------------------------------------------------------------------
# _parse_router_data（window._ROUTER_DATA 解析）
# ---------------------------------------------------------------------------

def test_parse_router_data_extracts_json():
    """``window._ROUTER_DATA = {...}`` → 解析为 dict。"""
    html = '<script>window._ROUTER_DATA = {"foo": "bar", "n": 42}</script>'
    assert social_utils._parse_router_data(html) == {"foo": "bar", "n": 42}


def test_parse_router_data_no_match_returns_empty():
    """无 _ROUTER_DATA → 空 dict。"""
    assert social_utils._parse_router_data("<html>no router data</html>") == {}


def test_parse_router_data_invalid_json_returns_empty():
    """JSON 解析失败 → 空 dict（不抛）。"""
    html = '<script>window._ROUTER_DATA = {not valid json}</script>'
    assert social_utils._parse_router_data(html) == {}


# ---------------------------------------------------------------------------
# _pick_url
# ---------------------------------------------------------------------------

def test_pick_url_returns_first_non_empty_url():
    """``url_list[0]`` 非空 → 返回。"""
    assert social_utils._pick_url({"url_list": ["https://a.com/", ""]}) == "https://a.com/"


def test_pick_url_empty_returns_empty():
    """url_list 为空 → ""。"""
    assert social_utils._pick_url({"url_list": []}) == ""
    assert social_utils._pick_url({}) == ""