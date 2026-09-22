"""_http.py 共享 helper 测试 — 节流 / UA / 错误转发 / encoding 修正。

依赖测试约定（mock requests.get 拦截真实网络）：用 unittest.mock 替换
crawagent.tools._http.requests.get，所有断言在 mock 返回值上做。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import _http


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_response():
    """构造一个 mock Response 对象，content / status_code / apparent_encoding / raise_for_status 可控。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html>hello</html>"
    resp.content = b"<html>hello</html>"
    resp.apparent_encoding = "utf-8"
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# http_get
# ---------------------------------------------------------------------------

def test_http_get_returns_text(fake_response):
    """返回 resp.text。"""
    with patch.object(_http.requests, "get", return_value=fake_response) as mock_get:
        out = _http.http_get("https://example.com")
    assert out == "<html>hello</html>"
    mock_get.assert_called_once()


def test_http_get_passes_default_ua(fake_response):
    """未传 headers 时，自动加 User-Agent 默认值。"""
    with patch.object(_http.requests, "get", return_value=fake_response) as mock_get:
        _http.http_get("https://example.com")
    called_headers = mock_get.call_args.kwargs["headers"]
    assert "User-Agent" in called_headers
    assert called_headers["User-Agent"] == _http.DEFAULT_UA


def test_http_get_caller_headers_override_default(fake_response):
    """caller 传 headers 时，caller 的 UA 覆盖默认值。"""
    caller_ua = "MyAgent/1.0"
    with patch.object(_http.requests, "get", return_value=fake_response) as mock_get:
        _http.http_get("https://example.com", headers={"User-Agent": caller_ua, "X-Custom": "1"})
    called_headers = mock_get.call_args.kwargs["headers"]
    assert called_headers["User-Agent"] == caller_ua
    assert called_headers["X-Custom"] == "1"


def test_http_get_respects_timeout(fake_response):
    """timeout 参数透传给 requests.get。"""
    with patch.object(_http.requests, "get", return_value=fake_response) as mock_get:
        _http.http_get("https://example.com", timeout=42.5)
    assert mock_get.call_args.kwargs["timeout"] == 42.5


def test_http_get_default_timeout(fake_response):
    """未传 timeout 时使用默认 20。"""
    with patch.object(_http.requests, "get", return_value=fake_response) as mock_get:
        _http.http_get("https://example.com")
    assert mock_get.call_args.kwargs["timeout"] == 20


def test_http_get_passes_params(fake_response):
    """params 透传给 requests.get（URL query 参数）。"""
    with patch.object(_http.requests, "get", return_value=fake_response) as mock_get:
        _http.http_get("https://example.com/search", params={"q": "test", "rn": 10})
    assert mock_get.call_args.kwargs["params"] == {"q": "test", "rn": 10}


def test_http_get_calls_enforce_delay(fake_response):
    """每次调用前都走 _enforce_delay 节流。"""
    with patch.object(_http.requests, "get", return_value=fake_response), \
         patch.object(_http, "_enforce_delay") as mock_mine:
        _http.http_get("https://example.com")
    mock_mine.assert_called_once()


def test_http_get_raises_on_4xx(fake_response):
    """HTTP 4xx/5xx → raise_for_status 抛 HTTPError。"""
    import requests as real_requests
    fake_response.status_code = 404
    fake_response.raise_for_status.side_effect = real_requests.HTTPError("404 Not Found")
    with patch.object(_http.requests, "get", return_value=fake_response):
        with pytest.raises(real_requests.HTTPError):
            _http.http_get("https://example.com/missing")


def test_http_get_uses_apparent_encoding(fake_response):
    """encoding 修正：用 apparent_encoding 覆盖默认 encoding（crawl_tool 行为一致）。"""
    fake_response.apparent_encoding = "gb2312"
    fake_response.encoding = "iso-8859-1"
    with patch.object(_http.requests, "get", return_value=fake_response):
        _http.http_get("https://example.com")
    assert fake_response.encoding == "gb2312"


def test_http_get_apparent_encoding_fallback_utf8(fake_response):
    """apparent_encoding 拿不到时 fallback utf-8。"""
    fake_response.apparent_encoding = None
    fake_response.encoding = "iso-8859-1"
    with patch.object(_http.requests, "get", return_value=fake_response):
        _http.http_get("https://example.com")
    assert fake_response.encoding == "utf-8"