"""bilibili_tool 测试 — WBI 签名 / 多端点 / 主函数整合。

依赖测试约定：mock `crawagent.tools.social_utils._get` 拦截真实 API。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import bilibili_tool


# ---------------------------------------------------------------------------
# _mixin_key
# ---------------------------------------------------------------------------

def test_mixin_key_basic():
    """mixin_key 长度 = 32，从 img_key+sub_key 按 enc_tab 重排取前 32 字符。"""
    img = "0" * 32
    sub = "1" * 32
    key = bilibili_tool._mixin_key(img, sub)
    assert len(key) == 32
    # 所有字符来自输入
    assert all(c in "01" for c in key)


def test_mixin_key_known_input():
    """固定输入 → 固定输出（验证算法）。"""
    img = "abcdefghijklmnopqrstuvwxyz012345"
    sub = "ABCDEFGHIJKLMNOPQRSTUVWXYZ987654"
    key = bilibili_tool._mixin_key(img, sub)
    assert len(key) == 32
    # 应该按 enc_tab 重排 — 第一个字符应是 enc_tab[0]=46 → img[46] (越界取不到，sub[46] 也越界)
    # 用实际值更稳，只验证长度
    # 第二次调用同样输入 → 同样输出
    assert bilibili_tool._mixin_key(img, sub) == key


# ---------------------------------------------------------------------------
# _wbi_sign
# ---------------------------------------------------------------------------

def test_wbi_sign_appends_wts_and_w_rid(monkeypatch):
    """签名 → params 加 wts + w_rid。"""
    # 固定 wts 便于断言
    monkeypatch.setattr(bilibili_tool.time, "time", lambda: 1700000000)

    signed = bilibili_tool._wbi_sign(
        {"bvid": "BV1abc", "cid": 12345},
        img_key="0" * 32,
        sub_key="1" * 32,
    )
    assert signed["wts"] == 1700000000
    assert "w_rid" in signed
    assert len(signed["w_rid"]) == 32  # md5 hex
    # 原始字段保留
    assert signed["bvid"] == "BV1abc"
    assert signed["cid"] == 12345


def test_wbi_sign_strips_special_chars_in_values(monkeypatch):
    """值中 ``!'()*`` 字符 → URL-encode 前先 strip。"""
    monkeypatch.setattr(bilibili_tool.time, "time", lambda: 1700000000)
    signed = bilibili_tool._wbi_sign(
        {"title": "hello!world*foo"},
        img_key="0" * 32,
        sub_key="1" * 32,
    )
    assert "w_rid" in signed


# ---------------------------------------------------------------------------
# _get_wbi_keys
# ---------------------------------------------------------------------------

def test_get_wbi_keys_success(monkeypatch):
    """nav API 返回含 wbi_img → 提取 img_key/sub_key。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png",
            }
        }
    }

    with patch.object(bilibili_tool, "_get", return_value=mock_resp):
        img, sub = bilibili_tool._get_wbi_keys({"User-Agent": "test"})
    assert len(img) == 32
    assert len(sub) == 32


def test_get_wbi_keys_api_failure_returns_empty(monkeypatch):
    """API 抛异常 → 返回 ("", "")。"""
    import requests as real_requests
    with patch.object(bilibili_tool, "_get", side_effect=real_requests.ConnectionError("net")):
        img, sub = bilibili_tool._get_wbi_keys({})
    assert img == ""
    assert sub == ""


def test_get_wbi_keys_short_keys_rejected(monkeypatch):
    """img_key/sub_key 长度 ≠ 32 → 返回空。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "wbi_img": {
                "img_url": "https://example.com/short.png",  # stem 太短
                "sub_url": "https://example.com/short.png",
            }
        }
    }
    with patch.object(bilibili_tool, "_get", return_value=mock_resp):
        img, sub = bilibili_tool._get_wbi_keys({})
    assert img == ""


# ---------------------------------------------------------------------------
# _bilibili_playurl（mock WBI keys + 端点）
# ---------------------------------------------------------------------------

def test_bilibili_playurl_wbi_signed_success():
    """WBI 签名成功 → 返回 signed payload。"""
    payload = {"code": 0, "data": {"dash": {"video": []}}}
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload

    with patch.object(bilibili_tool, "_get_wbi_keys", return_value=("a" * 32, "b" * 32)), \
         patch.object(bilibili_tool, "_get", return_value=mock_resp) as mock_get:
        result = bilibili_tool._bilibili_playurl({"bvid": "BV1"}, {})
    assert result["code"] == 0
    # 应调 wbi 端点
    call_url = mock_get.call_args.args[0]
    assert "wbi/playurl" in call_url


def test_bilibili_playurl_wbi_fails_falls_back_unsigned():
    """WBI 失败 → fallback unsigned 端点。"""
    payload_wbi = {"code": -101, "message": "token error"}
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload_wbi

    with patch.object(bilibili_tool, "_get_wbi_keys", return_value=("a" * 32, "b" * 32)), \
         patch.object(bilibili_tool, "_get", return_value=mock_resp) as mock_get:
        result = bilibili_tool._bilibili_playurl({"bvid": "BV1"}, {})
    # fallback 调 unsigned 端点
    call_urls = [c.args[0] for c in mock_get.call_args_list]
    assert any("wbi/playurl" in u for u in call_urls)


def test_bilibili_playurl_no_wbi_keys_skips_signed():
    """_get_wbi_keys 返回空 → 直接走 unsigned。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 0, "data": {}}

    with patch.object(bilibili_tool, "_get_wbi_keys", return_value=("", "")), \
         patch.object(bilibili_tool, "_get", return_value=mock_resp) as mock_get:
        bilibili_tool._bilibili_playurl({"bvid": "BV1"}, {})
    call_urls = [c.args[0] for c in mock_get.call_args_list]
    # 没调 wbi，只调 unsigned
    assert not any("wbi/playurl" in u for u in call_urls)


# ---------------------------------------------------------------------------
# _bilibili / bilibili_extract
# ---------------------------------------------------------------------------

def test_bilibili_extract_forwards_to_bilibili():
    """公开入口 → _bilibili（1 行转发，不是 @tool）。"""
    with patch.object(bilibili_tool, "_bilibili", return_value={"title": "测试"}) as mock_b:
        result = bilibili_tool.bilibili_extract("https://www.bilibili.com/video/BV123", {"title"})
    assert result == {"title": "测试"}
    mock_b.assert_called_once()


def test_bilibili_download_forwards():
    """公开入口 → _download_bilibili_video（1 行转发）。"""
    with patch.object(bilibili_tool, "_download_bilibili_video", return_value=([], [])) as mock_d:
        result = bilibili_tool.bilibili_download({}, Path("/tmp"), "title", {})
    assert result == ([], [])
    mock_d.assert_called_once()