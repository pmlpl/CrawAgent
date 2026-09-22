"""search_tool 测试 — DuckDuckGo + Baidu fallback 搜索。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import search_tool


# ---------------------------------------------------------------------------
# _ddg_search
# ---------------------------------------------------------------------------

def test_ddg_search_parses_result_anchors(monkeypatch):
    """DuckDuckGo 结果页解析：a.result__a + snippet。"""
    html = """
    <html><body>
    <div class="result">
      <a class="result__a" href="https://example.com/page">标题</a>
      <a class="result__snippet">片段描述</a>
    </div>
    </body></html>
    """
    mock_resp = MagicMock()
    mock_resp.text = html
    with patch.object(search_tool.requests, "get", return_value=mock_resp):
        results = search_tool._ddg_search("test query", max_results=5)
    assert len(results) == 1
    assert results[0]["title"] == "标题"
    assert results[0]["url"] == "https://example.com/page"
    assert "片段" in results[0]["snippet"]


def test_ddg_search_uddg_url_decoded(monkeypatch):
    """DDG 包裹 URL（``uddg=<urlencoded>``）要解开。"""
    import urllib.parse
    real = "https://example.com/real"
    wrapped = f"https://duckduckgo.com/l/?uddg={urllib.parse.quote(real)}"
    html = f'<div class="result"><a class="result__a" href="{wrapped}">T</a></div>'
    mock_resp = MagicMock()
    mock_resp.text = html
    with patch.object(search_tool.requests, "get", return_value=mock_resp):
        results = search_tool._ddg_search("q", max_results=5)
    assert results[0]["url"] == real


def test_ddg_search_protocol_relative_url(monkeypatch):
    """``//example.com/...`` protocol-relative URL → https。"""
    html = '<div class="result"><a class="result__a" href="//example.com/page">T</a></div>'
    mock_resp = MagicMock()
    mock_resp.text = html
    with patch.object(search_tool.requests, "get", return_value=mock_resp):
        results = search_tool._ddg_search("q", max_results=5)
    assert results[0]["url"] == "https://example.com/page"


def test_ddg_search_skips_non_http(monkeypatch):
    """非 http(s) 链接跳过。"""
    html = """
    <div class="result"><a class="result__a" href="javascript:void(0)">JS</a></div>
    <div class="result"><a class="result__a" href="https://example.com/real">Real</a></div>
    """
    mock_resp = MagicMock()
    mock_resp.text = html
    with patch.object(search_tool.requests, "get", return_value=mock_resp):
        results = search_tool._ddg_search("q", max_results=5)
    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/real"


# ---------------------------------------------------------------------------
# _baidu_search
# ---------------------------------------------------------------------------

def test_baidu_search_parses_c_container(monkeypatch):
    """百度结果页解析：div.c-container + h3 + a。"""
    html = """
    <html><body>
    <div class="c-container">
      <h3><a href="https://example.com/page">百度标题</a></h3>
      <div class="c-abstract">百度摘要</div>
    </div>
    </body></html>
    """
    mock_resp = MagicMock()
    mock_resp.text = html
    with patch.object(search_tool.requests, "get", return_value=mock_resp):
        results = search_tool._baidu_search("q", max_results=5)
    assert len(results) == 1
    assert results[0]["title"] == "百度标题"
    assert results[0]["url"] == "https://example.com/page"


def test_baidu_search_respects_max_results(monkeypatch):
    """max_results 限制返回数。"""
    items = ""
    for i in range(10):
        items += f'<div class="c-container"><h3><a href="https://e.com/{i}">T{i}</a></h3></div>'
    mock_resp = MagicMock()
    mock_resp.text = f"<html><body>{items}</body></html>"
    with patch.object(search_tool.requests, "get", return_value=mock_resp):
        results = search_tool._baidu_search("q", max_results=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------

def test_search_tool_ddg_failure_raises():
    """DDG 失败 → 通过 run_custom_script / 集成路径 — 这里直接验证 _ddg_search 抛错。"""
    import requests as real_requests
    with patch.object(search_tool.requests, "get", side_effect=real_requests.ConnectionError("net")):
        with pytest.raises(real_requests.ConnectionError):
            search_tool._ddg_search("q", max_results=5)