"""browse_tool 测试 — HTML 转 Markdown / 锁定章节代理 / browse_and_crawl 错误处理。

Playwright 真集成测成本高（启动浏览器 + 30s+ 超时）—— 集中在纯函数 + 错误路径。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import browse_tool


# ---------------------------------------------------------------------------
# _html_to_md
# ---------------------------------------------------------------------------

def test_html_to_md_empty():
    """空字符串 → 空。"""
    assert browse_tool._html_to_md("") == ""


def test_html_to_md_too_short():
    """< 200 字符 → 空（视为内容不足）。"""
    short = "<p>hi</p>"  # 10 chars
    assert browse_tool._html_to_md(short) == ""


def test_html_to_md_normal_content():
    """正常 HTML → Markdown（含段落 + 标题）。"""
    html = """
    <html><head><title>T</title></head><body>
    <h1>第一章</h1>
    <p>这是正文段落一，包含了很多文字以触发转换逻辑。</p>
    <ul><li>项目一</li><li>项目二</li></ul>
    <pre><code>print("hello")</code></pre>
    </body></html>
    """
    md = browse_tool._html_to_md(html)
    assert "第一章" in md
    assert "正文段落一" in md
    assert len(md) > 50


def test_html_to_md_strips_after_conversion_if_too_short():
    """转换后 < 50 字符 → 返回空（视为内容稀薄）。"""
    html = "<p>x</p>" * 30  # 210 字符但转换后很短
    # html2text 转换后可能 > 50 → 这里只看输出长度
    out = browse_tool._html_to_md(html)
    # 如果 < 50 字符应该返回空，否则正常返回
    if len(out) < 50:
        assert out == ""
    else:
        assert len(out) >= 50


# ---------------------------------------------------------------------------
# _fetch_locked_chapter
# ---------------------------------------------------------------------------

def test_fetch_locked_chapter_no_api_returns_empty(monkeypatch):
    """settings.locked_chapter_api 为空 → 返回空串（不抛错）。"""
    class _S:
        locked_chapter_api = ""
    monkeypatch.setattr(browse_tool, "get_settings", lambda: _S())
    assert browse_tool._fetch_locked_chapter("item_123") == ""


def test_fetch_locked_chapter_success(monkeypatch):
    """API 返回 code=200 + content 含 <p> → 返回纯文本。"""
    class _S:
        locked_chapter_api = "https://api.example.com/chapter"
    monkeypatch.setattr(browse_tool, "get_settings", lambda: _S())

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "code": 200,
        "data": {"content": "<div><p>第一段</p><p>第二段</p></div>"},
    }
    with patch("requests.get", return_value=mock_resp) as mock_get:
        out = browse_tool._fetch_locked_chapter("item_456")

    assert "第一段" in out
    assert "第二段" in out
    assert "\n\n" in out  # 段落间空行
    # 验证请求参数
    call_args = mock_get.call_args
    assert call_args.args[0] == "https://api.example.com/chapter"
    assert call_args.kwargs["params"] == {"item_id": "item_456"}
    assert "User-Agent" in call_args.kwargs["headers"]


def test_fetch_locked_chapter_non_200_returns_empty(monkeypatch):
    """API 返回 code != 200 → 空串。"""
    class _S:
        locked_chapter_api = "https://api.example.com/chapter"
    monkeypatch.setattr(browse_tool, "get_settings", lambda: _S())

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 403, "msg": "Forbidden"}
    with patch("requests.get", return_value=mock_resp):
        out = browse_tool._fetch_locked_chapter("item_789")
    assert out == ""


def test_fetch_locked_chapter_empty_content_returns_empty(monkeypatch):
    """API 返回 code=200 但 data.content 为空 → 空串。"""
    class _S:
        locked_chapter_api = "https://api.example.com/chapter"
    monkeypatch.setattr(browse_tool, "get_settings", lambda: _S())

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 200, "data": {"content": ""}}
    with patch("requests.get", return_value=mock_resp):
        out = browse_tool._fetch_locked_chapter("item_empty")
    assert out == ""


def test_fetch_locked_chapter_request_exception_returns_empty(monkeypatch):
    """requests.get 抛异常 → 返回空串（不传播给 browse_and_crawl）。"""
    import requests as real_requests
    class _S:
        locked_chapter_api = "https://api.example.com/chapter"
    monkeypatch.setattr(browse_tool, "get_settings", lambda: _S())

    with patch("requests.get", side_effect=real_requests.ConnectionError("timeout")):
        out = browse_tool._fetch_locked_chapter("item_err")
    assert out == ""


def test_fetch_locked_chapter_strips_pgc_voice_markers(monkeypatch):
    """清洗 PGC_VOICE 标记（{!-- PGC_VOICE: ... --}）。"""
    class _S:
        locked_chapter_api = "https://api.example.com/chapter"
    monkeypatch.setattr(browse_tool, "get_settings", lambda: _S())

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "code": 200,
        "data": {"content": "<p>正常内容</p>{!--PGC_VOICE:abc123--}<p>继续</p>"},
    }
    with patch("requests.get", return_value=mock_resp):
        out = browse_tool._fetch_locked_chapter("item_pgc")
    assert "正常内容" in out
    assert "继续" in out
    assert "PGC_VOICE" not in out


# ---------------------------------------------------------------------------
# browse_and_crawl 错误处理（Playwright 真集成测成本高）
# ---------------------------------------------------------------------------

def test_browse_and_crawl_playwright_launch_fails_returns_error():
    """Playwright 启动失败 → 返回 ``Browse failed: ...``。"""
    def fake_run(coro):
        # 关闭传入的 coroutine 避免 RuntimeWarning：coroutine was never awaited
        coro.close()
        raise RuntimeError("browser not installed")
    with patch("crawagent.tools.browse_tool.asyncio.run", side_effect=fake_run):
        out = browse_tool.browse_and_crawl.func("https://example.com")
    assert "Browse failed" in out
    assert "browser not installed" in out


def test_browse_and_crawl_returns_error_string_for_exception():
    """任何未捕获异常都被包装为 \"Browse failed: ...\" 字符串（不抛给调用方）。"""
    def fake_run(coro):
        coro.close()
        raise ValueError("bad url")
    with patch("crawagent.tools.browse_tool.asyncio.run", side_effect=fake_run):
        out = browse_tool.browse_and_crawl.func("not-a-url")
    assert out.startswith("Browse failed:")
    assert "bad url" in out