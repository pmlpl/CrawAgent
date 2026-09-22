"""login_tool 测试 — Cookie 状态检查 / 表单登录。

``login_site`` 走 Playwright 真集成测成本高 — 集中在 ``check_login_status``（无 Playwright）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import login_tool


# ---------------------------------------------------------------------------
# _extract_origin
# ---------------------------------------------------------------------------

def test_extract_origin_with_scheme_and_host():
    """完整 URL → ``scheme://host``。"""
    origin = login_tool._extract_origin("https://weread.qq.com/web/book/shelf")
    assert origin == "https://weread.qq.com"


def test_extract_origin_strips_trailing_slash():
    """URL 无 scheme 但带 ``/`` → strip。"""
    origin = login_tool._extract_origin("weread.qq.com/")
    assert origin == "weread.qq.com"


def test_extract_origin_fallback():
    """无法解析 → 原样返回 strip ``/``。"""
    origin = login_tool._extract_origin("weread.qq.com")
    assert origin == "weread.qq.com"


# ---------------------------------------------------------------------------
# check_login_status 错误路径
# ---------------------------------------------------------------------------

def test_check_login_status_empty_origin():
    """origin 为空 → ERROR。"""
    out = login_tool.check_login_status.func("")
    assert "ERROR" in out
    assert "origin 不能为空" in out


def test_check_login_status_no_cookie():
    """无存档 Cookie → NO_COOKIE。"""
    with patch.object(login_tool, "get_site_cookies", return_value=""):
        out = login_tool.check_login_status.func("https://example.com")
    assert "NO_COOKIE" in out
    assert "login_site" in out or "save_site_profile" in out


# ---------------------------------------------------------------------------
# check_login_status 正常路径
# ---------------------------------------------------------------------------

def test_check_login_status_logged_in_no_redirect(monkeypatch):
    """请求成功 + 未被重定向 + 无登录表单 + 无 success_indicator → 走 fallback 判断。"""
    with patch.object(login_tool, "get_site_cookies", return_value="sid=valid"):
        # 默认 probe_url="" → 探测常见路径；首个 /account 返回 200 不重定向
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/account"
        mock_resp.text = "<html><body>Welcome, user</body></html>"
        mock_resp.status_code = 200
        with patch.object(login_tool.requests, "get", return_value=mock_resp):
            out = login_tool.check_login_status.func("https://example.com")
    # 不是 LOGGED_OUT 也不是 ERROR → 视为 logged in
    assert "LOGGED_IN" in out or "Cookie 仍然有效" in out


def test_check_login_status_redirected_to_login(monkeypatch):
    """被重定向到登录页 → LOGGED_OUT。"""
    with patch.object(login_tool, "get_site_cookies", return_value="sid=invalid"):
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/login?redirect=/account"
        mock_resp.text = "<html>请登录</html>"
        mock_resp.status_code = 200
        with patch.object(login_tool.requests, "get", return_value=mock_resp):
            out = login_tool.check_login_status.func("https://example.com")
    assert "LOGGED_OUT" in out
    assert "Cookie 已失效" in out


def test_check_login_status_html_has_password_form(monkeypatch):
    """HTML 含 ``<input type="password"`` → LOGGED_OUT。"""
    with patch.object(login_tool, "get_site_cookies", return_value="sid=invalid"):
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/account"
        mock_resp.status_code = 200
        # url 没匹配登录页关键字，但 HTML 含密码表单
        mock_resp.text = '<form><input type="password" name="pwd"></form>'
        with patch.object(login_tool.requests, "get", return_value=mock_resp):
            out = login_tool.check_login_status.func("https://example.com")
    assert "LOGGED_OUT" in out
    assert "登录表单" in out


def test_check_login_status_success_indicator_hit(monkeypatch):
    """HTML 命中 ``success_indicator`` selector → LOGGED_IN。"""
    with patch.object(login_tool, "get_site_cookies", return_value="sid=valid"):
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/account"
        mock_resp.status_code = 200
        mock_resp.text = '<html><div class="user-avatar">me</div></html>'
        with patch.object(login_tool.requests, "get", return_value=mock_resp):
            out = login_tool.check_login_status.func(
                "https://example.com", success_indicator="user-avatar"
            )
    assert "LOGGED_IN" in out
    assert "user-avatar" in out


def test_check_login_status_explicit_probe_url(monkeypatch):
    """显式 probe_url → 只探测这一个 URL。"""
    with patch.object(login_tool, "get_site_cookies", return_value="sid=valid"):
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/special"
        mock_resp.text = "<html>OK</html>"
        mock_resp.status_code = 200
        with patch.object(login_tool.requests, "get", return_value=mock_resp) as mock_get:
            out = login_tool.check_login_status.func(
                "https://example.com",
                probe_url="https://example.com/special",
            )
        # 只调用了一次
        assert mock_get.call_count == 1
        assert mock_get.call_args.args[0] == "https://example.com/special"


def test_check_login_status_request_exception_continues_to_next(monkeypatch):
    """单个 probe URL 抛异常 → continue 试下一个。"""
    import requests as real_requests

    with patch.object(login_tool, "get_site_cookies", return_value="sid=valid"):
        # 第一个抛异常，第二个成功
        good_resp = MagicMock()
        good_resp.url = "https://example.com/user"
        good_resp.text = "<html>Welcome</html>"
        good_resp.status_code = 200

        with patch.object(
            login_tool.requests, "get",
            side_effect=[real_requests.ConnectionError("refused"), good_resp],
        ):
            out = login_tool.check_login_status.func("https://example.com")
    # 跳到第二个 URL → 应不报 ERROR
    assert "ERROR" not in out or "LOGGED_IN" in out


# ---------------------------------------------------------------------------
# _looks_like_login_page（间接通过 check_login_status 测）
# ---------------------------------------------------------------------------

def test_looks_like_login_page_url_keyword():
    """``_looks_like_login_page`` 识别 URL 含 ``/login`` 关键字。"""
    # 通过 check_login_status 间接验证：URL 包含 /login → LOGGED_OUT
    with patch.object(login_tool, "get_site_cookies", return_value="sid=invalid"):
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/login"
        mock_resp.text = "<html>login</html>"
        with patch.object(login_tool.requests, "get", return_value=mock_resp):
            out = login_tool.check_login_status.func("https://example.com")
    assert "LOGGED_OUT" in out


def test_looks_like_login_page_html_keyword():
    """HTML 含 ``请登录`` → LOGGED_OUT。"""
    with patch.object(login_tool, "get_site_cookies", return_value="sid=invalid"):
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/account"
        mock_resp.text = "<html>请登录以继续</html>"
        with patch.object(login_tool.requests, "get", return_value=mock_resp):
            out = login_tool.check_login_status.func("https://example.com")
    assert "LOGGED_OUT" in out