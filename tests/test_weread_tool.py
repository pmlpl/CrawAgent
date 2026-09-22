"""weread_tool 测试 — 微信读书工具（Cookie + 内部 API）。

依赖测试约定：mock ``_weread_cookie`` 返回测试 cookie，mock requests 拦截 API 调用。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import weread_tool


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def with_cookie(monkeypatch):
    """默认有 Cookie。"""
    monkeypatch.setattr(weread_tool, "_weread_cookie", lambda: "wr_vid=test_vid; wr_ssk=test_ssk")


@pytest.fixture
def no_cookie(monkeypatch):
    """默认无 Cookie。"""
    monkeypatch.setattr(weread_tool, "_weread_cookie", lambda: "")


# ---------------------------------------------------------------------------
# _weread_headers
# ---------------------------------------------------------------------------

def test_weread_headers_without_cookie():
    """无 Cookie → 不含 Cookie 字段，但有 UA/Referer/Accept。"""
    with patch.object(weread_tool, "_weread_cookie", return_value=""):
        h = weread_tool._weread_headers()
    assert "Cookie" not in h
    assert "User-Agent" in h
    assert "Referer" in h
    assert "Accept" in h


def test_weread_headers_with_cookie():
    """有 Cookie → headers 含 Cookie 字段。"""
    with patch.object(weread_tool, "_weread_cookie", return_value="wr_vid=abc; wr_ssk=xyz"):
        h = weread_tool._weread_headers()
    assert h["Cookie"] == "wr_vid=abc; wr_ssk=xyz"


# ---------------------------------------------------------------------------
# _extract_book_id
# ---------------------------------------------------------------------------

def test_extract_book_id_from_json_pattern():
    """``"bookId": 12345`` → ``"12345"``。"""
    html = '<script>window.__INITIAL_STATE__={"bookId":12345,...}</script>'
    assert weread_tool._extract_book_id(html) == "12345"


def test_extract_book_id_from_quoted_json():
    """``"bookId":"67890"`` 字符串也识别。"""
    html = 'var data = {"bookId":"67890"};'
    assert weread_tool._extract_book_id(html) == "67890"


def test_extract_book_id_from_meta_tag():
    """``weread:book_id content="11111"`` 识别。"""
    html = '<meta property="weread:book_id" content="11111">'
    assert weread_tool._extract_book_id(html) == "11111"


def test_extract_book_id_returns_none_if_not_found():
    """无 bookId → None。"""
    html = "<html><body>No bookId here</body></html>"
    assert weread_tool._extract_book_id(html) is None


# ---------------------------------------------------------------------------
# _extract_v_from_url
# ---------------------------------------------------------------------------

def test_extract_v_from_url_found():
    """``?v=a57325c0...`` → 提取 v 参数。"""
    v = weread_tool._extract_v_from_url("https://weread.qq.com/book-detail?type=1&v=a57325c05c8ed3a57224187")
    assert v == "a57325c05c8ed3a57224187"


def test_extract_v_from_url_not_found():
    """URL 无 v 参数 → None。"""
    v = weread_tool._extract_v_from_url("https://weread.qq.com/book-detail")
    assert v is None


# ---------------------------------------------------------------------------
# list_weread_chapters 错误路径
# ---------------------------------------------------------------------------

def test_list_chapters_no_cookie_returns_setup_instructions(no_cookie):
    """无 Cookie → 返回设置指南，不调用 API。"""
    out = weread_tool.list_weread_chapters.func("822995")
    assert "WEREAD_COOKIE_NOT_SET" in out
    assert "wr_vid" in out
    assert "wr_ssk" in out


def test_list_chapters_url_fetch_failure_returns_error(with_cookie):
    """URL 详情页 fetch 失败 → 返回错误。"""
    from crawagent.tools import _http
    with patch.object(_http, "http_get", side_effect=Exception("connection refused")):
        out = weread_tool.list_weread_chapters.func("https://weread.qq.com/book-detail?v=abc")
    assert "无法获取详情页" in out


def test_list_chapters_url_no_book_id_returns_error(with_cookie):
    """URL 详情页不含 bookId → 返回错误。"""
    from crawagent.tools import _http
    html = "<html><body>no bookId</body></html>"
    with patch.object(_http, "http_get", return_value=html):
        out = weread_tool.list_weread_chapters.func("https://weread.qq.com/book-detail?v=abc")
    assert "无法从 URL 提取 bookId" in out


def test_list_chapters_api_user_not_exists_returns_auth_failed(with_cookie):
    """API 返回 errCode != 0 + 用户不存在 → [AUTH_FAILED]。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"errCode": -1, "errMsg": "用户不存在"}
    with patch.object(weread_tool.requests, "get", return_value=mock_resp):
        out = weread_tool.list_weread_chapters.func("822995")
    assert "AUTH_FAILED" in out
    assert "Cookie 已过期" in out or "无效" in out


def test_list_chapters_api_other_error_returns_api_error(with_cookie):
    """API 返回 errCode != 0 + 其他错误 → [API_ERROR]。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"errCode": 500, "errMsg": "server down"}
    with patch.object(weread_tool.requests, "get", return_value=mock_resp):
        out = weread_tool.list_weread_chapters.func("822995")
    assert "API_ERROR" in out
    assert "500" in out


def test_list_chapters_api_request_exception(with_cookie):
    """API requests.get 抛异常 → 包装错误信息。"""
    import requests as real_requests
    with patch.object(weread_tool.requests, "get", side_effect=real_requests.Timeout("timeout")):
        out = weread_tool.list_weread_chapters.func("822995")
    assert "章节列表 API 请求失败" in out
    assert "timeout" in out


# ---------------------------------------------------------------------------
# list_weread_chapters 正常路径
# ---------------------------------------------------------------------------

def test_list_chapters_success_returns_formatted(with_cookie):
    """API 返回正常 → 格式化输出含书名/作者/章节列表。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "errCode": 0,
        "info": {"title": "深入理解计算机系统", "author": "Randal E. Bryant"},
        "chapterInfos": [
            [
                {"chapterUid": "1001", "chapterName": "第一章 计算机系统漫游", "wordCount": 5000, "isCanRead": True},
                {"chapterUid": "1002", "chapterName": "第二章 数据的表示和处理", "wordCount": 8000, "isCanRead": False},
            ]
        ],
    }
    with patch.object(weread_tool.requests, "get", return_value=mock_resp):
        out = weread_tool.list_weread_chapters.func("822995")
    assert "深入理解计算机系统" in out
    assert "Randal E. Bryant" in out
    assert "第一章" in out
    assert "[VIP/付费]" in out  # 章节 2 isCanRead=False


def test_list_chapters_empty_chapters_returns_warning(with_cookie):
    """API 正常但 chapterInfos 为空 → 警告信息。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "errCode": 0,
        "info": {"title": "某付费书", "author": "某作者"},
        "chapterInfos": [],
    }
    with patch.object(weread_tool.requests, "get", return_value=mock_resp):
        out = weread_tool.list_weread_chapters.func("822995")
    assert "WARNING" in out
    assert "未获取到任何章节" in out


def test_list_chapters_book_v_in_output(with_cookie):
    """URL 含 v= 参数 → 输出附带 ``[book_v=...]``。"""
    from crawagent.tools import _http
    html = '<script>{"bookId":822995}</script>'
    with patch.object(_http, "http_get", return_value=html), \
         patch.object(weread_tool.requests, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "errCode": 0,
            "info": {"title": "测试书", "author": "测试"},
            "chapterInfos": [{"chapterUid": "1", "chapterName": "ch1", "wordCount": 100, "isCanRead": True}],
        }
        mock_get.return_value = mock_resp

        out = weread_tool.list_weread_chapters.func("https://weread.qq.com/book-detail?v=abc123")
    assert "[book_v=abc123]" in out


# ---------------------------------------------------------------------------
# get_weread_chapter（单章）
# ---------------------------------------------------------------------------

def test_get_chapter_no_cookie_returns_setup_instructions(no_cookie):
    """无 Cookie → 返回设置指南。"""
    out = weread_tool.get_weread_chapter.func("abc123", "1001")
    assert "WEREAD_COOKIE_NOT_SET" in out