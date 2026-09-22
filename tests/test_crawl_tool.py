"""crawl_tool.crawl_webpage 测试 — 网络请求 mock，不真发 HTTP。

覆盖：基础成功 / 4xx 错误 / 超时 / SPA shell 检测 / 节流。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import crawl_tool


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def bypass_delay(monkeypatch):
    """_enforce_delay 真 sleep 会拖测试，短路掉。

    变更 020 后 crawl_webpage 走 ``_http.http_get``，helper 在 import 时绑定了
    ``crawagent.tools.crawl_tool._enforce_delay`` 函数对象。monkeypatch ``crawl_tool._enforce_delay``
    不会影响 ``_http._enforce_delay`` 已绑定的引用，所以同时 patch 两个位置。
    """
    monkeypatch.setattr(crawl_tool, "_enforce_delay", lambda: None)
    monkeypatch.setattr("crawagent.tools._http._enforce_delay", lambda: None)
    # 重置 last_request_time，避免前面测试污染
    crawl_tool._last_request_time = 0
    yield


def _mock_response(text="<html>ok</html>", status_code=200, encoding="utf-8"):
    """造 requests.Response 替身。"""
    resp = MagicMock()
    resp.text = text
    resp.content = text.encode(encoding)
    resp.status_code = status_code
    resp.encoding = None
    resp.apparent_encoding = encoding
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import requests
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status_code}")
    return resp


@pytest.fixture
def fake_settings(monkeypatch):
    """mock get_settings → Settings 带合理 timeout / delay。"""

    class _S:
        request_timeout = 5
        request_delay = 0
        max_content_length = 50000

    monkeypatch.setattr(crawl_tool, "get_settings", lambda: _S())
    return _S


# ---------------------------------------------------------------------------
# crawl_webpage 基础 + 边界
# ---------------------------------------------------------------------------

def test_crawl_basic_html(fake_settings, monkeypatch):
    """正常 HTML → 返回原文。"""
    monkeypatch.setattr(
        crawl_tool.requests, "get",
        lambda *a, **kw: _mock_response("<html><body>hi</body></html>"),
    )
    out = crawl_tool.crawl_webpage.func("https://example.com")
    assert "hi" in out


def test_crawl_passes_user_agent(fake_settings, monkeypatch):
    """请求带 User-Agent header。"""
    captured = {}
    def fake_get(url, **kw):
        captured["headers"] = kw.get("headers", {})
        return _mock_response("<html>ok</html>")
    monkeypatch.setattr(crawl_tool.requests, "get", fake_get)
    crawl_tool.crawl_webpage.func("https://example.com")
    assert "User-Agent" in captured["headers"]
    ua = captured["headers"]["User-Agent"]
    assert len(ua) > 10  # 不是空


def test_crawl_404_returns_error(fake_settings, monkeypatch):
    """HTTP 404 → 返回 ERROR: ... 字符串，不抛。"""
    monkeypatch.setattr(
        crawl_tool.requests, "get",
        lambda *a, **kw: _mock_response(status_code=404),
    )
    out = crawl_tool.crawl_webpage.func("https://example.com/missing")
    assert "ERROR" in out or "404" in out


def test_crawl_timeout_returns_error(fake_settings, monkeypatch):
    """requests.get 超时 → 返回错误字符串。"""
    import requests
    def fake_get(*a, **kw):
        raise requests.Timeout("read timed out")
    monkeypatch.setattr(crawl_tool.requests, "get", fake_get)
    out = crawl_tool.crawl_webpage.func("https://slow.example.com")
    assert "ERROR" in out or "timed out" in out


def test_crawl_connection_error_returns_error(fake_settings, monkeypatch):
    """连接错误 → 返回错误字符串。"""
    import requests
    def fake_get(*a, **kw):
        raise requests.ConnectionError("Name or service not known")
    monkeypatch.setattr(crawl_tool.requests, "get", fake_get)
    out = crawl_tool.crawl_webpage.func("https://nope.example.com")
    assert "ERROR" in out


def test_crawl_spa_shell_detected(fake_settings, monkeypatch):
    """HTML < 5000 chars 且基本是 <script> → 返回 [SPA_SHELL_DETECTED]。"""
    spa = "<html><head><script src='app.js'></script></head><body></body></html>"
    monkeypatch.setattr(
        crawl_tool.requests, "get",
        lambda *a, **kw: _mock_response(spa),
    )
    out = crawl_tool.crawl_webpage.func("https://spa.example.com")
    assert "[SPA_SHELL_DETECTED]" in out


def test_crawl_truncates_oversized_content(fake_settings, monkeypatch):
    """HTML 超过 max_content_length → 末尾附 [content truncated]。"""
    big = "<html>" + ("x" * 100000) + "</html>"
    monkeypatch.setattr(
        crawl_tool.requests, "get",
        lambda *a, **kw: _mock_response(big),
    )
    out = crawl_tool.crawl_webpage.func("https://huge.example.com")
    # max_content_length=50000 → 应该被截
    assert "[content truncated]" in out


def test_crawl_enforce_delay_called(fake_settings, monkeypatch):
    """每次调用前 _enforce_delay 必调（autouse 已经替换，验证它没被绕过）。

    变更 020 后：crawl_webpage 走 ``_http.http_get``，helper 内部调 ``_enforce_delay``。
    monkeypatch 必须在 ``_http`` 模块侧生效（import 时已绑定引用）。
    """
    called = []
    monkeypatch.setattr("crawagent.tools._http._enforce_delay", lambda: called.append(1))
    monkeypatch.setattr(
        crawl_tool.requests, "get",
        lambda *a, **kw: _mock_response("<html>ok</html>"),
    )
    crawl_tool.crawl_webpage.func("https://a.com")
    crawl_tool.crawl_webpage.func("https://b.com")
    assert len(called) == 2


def test_crawl_passes_timeout_to_requests(fake_settings, monkeypatch):
    """请求带 timeout=settings.request_timeout。"""
    captured = {}
    def fake_get(*a, **kw):
        captured["timeout"] = kw.get("timeout")
        return _mock_response("<html>ok</html>")
    monkeypatch.setattr(crawl_tool.requests, "get", fake_get)
    crawl_tool.crawl_webpage.func("https://x.com")
    assert captured["timeout"] == 5  # fake_settings.request_timeout