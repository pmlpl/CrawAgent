"""wallpaper_tool 测试 — 卡片链接提取 / 标题解析 / 详情图提取 / 工具集成。

依赖测试约定：mock ``pagination.http_get`` / ``pagination.walk_pages`` 拦截真实网络。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import wallpaper_tool


# ---------------------------------------------------------------------------
# _extract_card_links
# ---------------------------------------------------------------------------

def test_extract_card_links_filters_navigation():
    """导航链接（/user/ /login/ 等）被过滤。"""
    html = """
    <html><body>
    <a href="/user/profile">User</a>
    <a href="/login">Login</a>
    <a href="/static/img">Static</a>
    <a href="/wallpaper/12345">Detail</a>
    <a href="/detail/67890">Detail</a>
    </body></html>
    """
    links = wallpaper_tool._extract_card_links(html, "https://example.com")
    # 排除导航 + 含 4 位数字 ID 或匹配详情页路径
    assert all("/user/" not in u for u in links)
    assert all("/login" not in u for u in links)
    assert all("/static/" not in u for u in links)
    # 保留含数字 ID 的
    assert any("/wallpaper/12345" in u for u in links)
    assert any("/detail/67890" in u for u in links)


def test_extract_card_links_skips_page_query():
    """``?page=N`` 分页链接被过滤（不是详情页）。"""
    html = '<a href="/list?page=2">More</a><a href="/detail/99999">Detail</a>'
    links = wallpaper_tool._extract_card_links(html, "https://example.com")
    assert not any("page=" in u for u in links)
    assert any("/detail/99999" in u for u in links)


def test_extract_card_links_dedupes():
    """重复链接去重。"""
    html = '<a href="/detail/111">A</a><a href="/detail/111">B</a>'
    links = wallpaper_tool._extract_card_links(html, "https://example.com")
    detail_urls = [u for u in links if "/detail/111" in u]
    assert len(detail_urls) == 1


def test_extract_card_links_skips_anchors_and_js():
    """``#anchor`` / ``javascript:`` / 空 href 跳过。"""
    html = """
    <a href="#">Anchor</a>
    <a href="javascript:void(0)">JS</a>
    <a href="">Empty</a>
    <a href="/detail/555">Real</a>
    """
    links = wallpaper_tool._extract_card_links(html, "https://example.com")
    assert all(not u.endswith("#") for u in links)
    assert all("javascript:" not in u for u in links)
    assert all(u != "https://example.com" or "/detail/" in u for u in links)
    assert any("/detail/555" in u for u in links)


# ---------------------------------------------------------------------------
# _parse_title
# ---------------------------------------------------------------------------

def test_parse_title_extracts_resolution():
    """标题 ``4K 山景`` → ``("4K", "山景")``。"""
    res, name = wallpaper_tool._parse_title("4K 山景")
    assert res == "4K"
    assert "山景" in name


def test_parse_title_strips_site_suffix():
    """站点后缀（" - 壁纸" 等）被去掉。"""
    res, name = wallpaper_tool._parse_title("5K 海浪「哲风壁纸」")
    # 站点后缀去掉，再去掉分隔符
    assert "「哲风壁纸」" not in name
    assert "哲风壁纸" not in name
    assert "海浪" in name


def test_parse_title_no_resolution():
    """无分辨率前缀 → 空。"""
    res, name = wallpaper_tool._parse_title("城市夜景")
    assert res == ""
    assert "城市夜景" in name


def test_parse_title_empty_returns_empty():
    """空字符串 → ("", "")。"""
    res, name = wallpaper_tool._parse_title("")
    assert res == ""
    assert name == ""


def test_parse_title_strips_separator():
    """分隔符（| / 【 等）后的描述被去掉。"""
    res, name = wallpaper_tool._parse_title("8K 雪山 | 风景摄影精选")
    assert res == "8K"
    assert "雪山" in name
    assert "风景摄影精选" not in name


# ---------------------------------------------------------------------------
# _extract_detail_info
# ---------------------------------------------------------------------------

def test_extract_detail_info_static_image():
    """静态图（无 <video> 标签 + 无 PUA 关键字）→ is_dynamic=False。"""
    html = """
    <html><head><title>4K 海景</title></head>
    <body>
    <img src="/static/wallpaper.jpg" />
    <img data-src="/lazy/wallpaper2.jpg" />
    </body></html>
    """
    info = wallpaper_tool._extract_detail_info(html, "https://example.com/detail/1")
    assert info["title"] == "4K 海景"
    assert info["is_dynamic"] is False
    assert info["resolution"] == "4K"
    assert any("wallpaper.jpg" in u for u in info["images"])
    assert any("wallpaper2.jpg" in u for u in info["images"])
    assert info["videos"] == []


def test_extract_detail_info_dynamic_via_video_tag():
    """含 <video> 标签 → is_dynamic=True。"""
    html = """
    <html><head><title>动态壁纸</title></head>
    <body>
    <video src="/dyn/wallpaper.mp4"></video>
    </body></html>
    """
    info = wallpaper_tool._extract_detail_info(html, "https://example.com/detail/2")
    assert info["is_dynamic"] is True
    assert any("wallpaper.mp4" in u for u in info["videos"])


def test_extract_detail_info_dynamic_via_title_keyword():
    """无 <video> 但标题含 \"动态\" 关键字 → is_dynamic=True。"""
    html = """
    <html><head><title>动态海浪</title></head>
    <body><img src="/img.jpg" /></body></html>
    """
    info = wallpaper_tool._extract_detail_info(html, "https://example.com/d/3")
    assert info["is_dynamic"] is True


def test_extract_detail_info_picks_primary_image():
    """主图 = 出现次数最多的图片（排除 logo/icon 噪声）。"""
    html = """
    <html><body>
    <img src="/logo.png" />
    <img src="/main.jpg" />
    <img src="/main.jpg" />
    <img src="/other.jpg" />
    </body></html>
    """
    info = wallpaper_tool._extract_detail_info(html, "https://example.com/d/4")
    # main.jpg 出现 2 次最多（logo 被 noise filter 排除）
    assert "main.jpg" in info["primary_image"]


def test_extract_detail_info_filters_logo_noise():
    """logo/icon 类图片从主图候选排除。"""
    html = """
    <html><body>
    <img src="/logo.png" />
    <img src="/real.jpg" />
    </body></html>
    """
    info = wallpaper_tool._extract_detail_info(html, "https://example.com/d/5")
    # 过滤后只剩 real.jpg
    assert "real.jpg" in info["primary_image"]
    assert "logo" not in info["primary_image"]


def test_extract_detail_info_background_image_in_style():
    """CSS ``background-image: url(...)`` 也要提取。"""
    html = """
    <html><body>
    <div style="background-image: url(/bg/wallpaper.jpg)">x</div>
    </body></html>
    """
    info = wallpaper_tool._extract_detail_info(html, "https://example.com/d/6")
    assert any("wallpaper.jpg" in u for u in info["images"])


# ---------------------------------------------------------------------------
# extract_wallpaper_list（mock pagination）
# ---------------------------------------------------------------------------

def test_extract_wallpaper_list_no_links_returns_error():
    """列表页无详情页链接 → 返回错误信息。"""
    empty_html = "<html><body>No cards</body></html>"
    with patch("crawagent.tools.wallpaper_tool.walk_pages") as mock_walk:
        # walk_pages 生成空 (url, html, fresh)
        def gen():
            yield ("https://example.com/list", empty_html, [])
        mock_walk.return_value = gen()

        out = wallpaper_tool.extract_wallpaper_list.func("https://example.com/list")
    assert "no detail-page links" in out or "failed" in out.lower()


def test_extract_wallpaper_list_returns_formatted_output(monkeypatch):
    """正常路径：返回格式化条目（标题 + 类型 + 详情链接 + 缩略图）。"""
    list_html = '<a href="/detail/12345">card</a>'
    detail_html = """
    <html><head><title>4K 山景壁纸</title></head>
    <body>
    <img src="/img1.jpg" />
    <img src="/img1.jpg" />
    </body></html>
    """

    with patch("crawagent.tools.wallpaper_tool.walk_pages") as mock_walk, \
         patch("crawagent.tools.wallpaper_tool.http_get") as mock_get:
        # walk_pages 一次 yield 一个 page + 新链接
        def gen():
            yield ("https://example.com/list", list_html, ["https://example.com/detail/12345"])
        mock_walk.return_value = gen()
        mock_get.return_value = detail_html

        out = wallpaper_tool.extract_wallpaper_list.func("https://example.com/list", limit=5)
    assert "Wallpaper list" in out
    assert "山景" in out
    assert "STATIC" in out
    assert "/detail/12345" in out


# ---------------------------------------------------------------------------
# wallpaper_detail（单条）
# ---------------------------------------------------------------------------

def test_wallpaper_detail_static_image(monkeypatch):
    """单条详情页 → 格式化输出含 type/title/resolution/images。"""
    html = """
    <html><head><title>8K 海浪</title></head>
    <body>
    <img src="/img.jpg" />
    <img src="/img.jpg" />
    </body></html>
    """
    with patch("crawagent.tools.wallpaper_tool.http_get", return_value=html):
        out = wallpaper_tool.wallpaper_detail.func("https://example.com/d/99")
    assert "Wallpaper detail" in out
    assert "Type:" in out
    assert "STATIC" in out
    assert "8K" in out
    assert "img.jpg" in out


def test_wallpaper_detail_http_failure_returns_error(monkeypatch):
    """HTTP 失败 → 返回错误信息（不抛异常）。"""
    with patch("crawagent.tools.wallpaper_tool.http_get", side_effect=Exception("network down")):
        out = wallpaper_tool.wallpaper_detail.func("https://example.com/d/err")
    assert "wallpaper_detail failed" in out
    assert "network down" in out