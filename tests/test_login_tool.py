"""登录工具测试 — 全离线，mock _playwright_login / requests / site_profile，不真登录。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import login_tool
from crawagent.tools.login_tool import (
    login_site, check_login_status,
    _looks_like_login_page, _cookies_to_string, _extract_origin,
)

# 避开编辑工具吞尖括号：HTML 标签用 chr(60)/chr(62) 运行时拼接
LT = chr(60)
GT = chr(62)
PW_INPUT = LT + 'input type="password"' + GT


# ---- _looks_like_login_page ----

def test_looks_like_login_url_pattern():
    assert _looks_like_login_page("https://x.com/login", "") is True
    assert _looks_like_login_page("https://x.com/signin", "") is True
    assert _looks_like_login_page("https://x.com/account/login", "") is True


def test_looks_like_login_password_input():
    html = LT + 'form' + GT + PW_INPUT + LT + '/form' + GT
    assert _looks_like_login_page("https://x.com/page", html) is True


def test_looks_like_login_keyword():
    assert _looks_like_login_page("https://x.com/x", "请登录后查看") is True
    assert _looks_like_login_page("https://x.com/x", "Please log in to continue") is True


def test_not_login_page():
    assert _looks_like_login_page("https://x.com/article/1", "正文内容很长" * 10) is False


# ---- _cookies_to_string ----

def test_cookies_to_string():
    cookies = [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]
    assert _cookies_to_string(cookies) == "a=1; b=2"


def test_cookies_to_string_skips_empty():
    cookies = [{"name": "", "value": "x"}, {"name": "a", "value": ""}, {"name": "b", "value": "2"}]
    assert _cookies_to_string(cookies) == "b=2"


def test_cookies_to_string_empty():
    assert _cookies_to_string([]) == ""


# ---- _extract_origin ----

def test_extract_origin():
    assert _extract_origin("https://x.com/login") == "https://x.com"
    assert _extract_origin("http://sub.x.com/path") == "http://sub.x.com"


def test_extract_origin_no_scheme():
    assert _extract_origin("x.com/path") == "x.com/path"


# ---- login_site 参数校验 ----

def test_login_site_empty_url():
    assert "ERROR" in login_site.func("", "u", "p")


def test_login_site_empty_credentials():
    assert "ERROR" in login_site.func("https://x.com/login", "", "p")
    assert "ERROR" in login_site.func("https://x.com/login", "u", "")


# ---- login_site 成功/失败/需手动（mock _playwright_login 为 async 函数）----

def test_login_site_success(monkeypatch):
    async def fake_login(**kw):
        return ("https://x.com/home", "html", [{"name": "s", "value": "tok"}], True, "")
    monkeypatch.setattr(login_tool, "_playwright_login", fake_login)
    called = {}
    def fake_upsert(**kw):
        called.update(kw)
        return {"origin": kw.get("origin")}
    monkeypatch.setattr(login_tool, "upsert_site", fake_upsert)
    r = login_site.func("https://x.com/login", "u", "p")
    assert "LOGIN_OK" in r and "1 条" in r
    assert called.get("cookies") == "s=tok"


def test_login_site_fail(monkeypatch):
    async def fake_login(**kw):
        return ("", "", [], False, "用户名密码错误")
    monkeypatch.setattr(login_tool, "_playwright_login", fake_login)
    r = login_site.func("https://x.com/login", "u", "p")
    # login_site 失败时返回 _playwright_login 的 reason 原样
    assert "用户名密码错误" in r


def test_login_site_needs_manual(monkeypatch):
    async def fake_login(**kw):
        return ("https://x.com/login", "", [], False,
                "LOGIN_NEEDS_MANUAL: 检测到滑块验证码")
    monkeypatch.setattr(login_tool, "_playwright_login", fake_login)
    r = login_site.func("https://x.com/login", "u", "p")
    assert "LOGIN_NEEDS_MANUAL" in r


def test_login_site_no_cookie_extracted(monkeypatch):
    async def fake_login(**kw):
        return ("https://x.com/home", "html", [], True, "")
    monkeypatch.setattr(login_tool, "_playwright_login", fake_login)
    r = login_site.func("https://x.com/login", "u", "p")
    assert "LOGIN_FAIL" in r and "未提取到 Cookie" in r


# ---- check_login_status ----

def test_check_login_status_no_cookie(monkeypatch):
    monkeypatch.setattr(login_tool, "get_site_cookies", lambda origin: "")
    r = check_login_status.func("https://x.com")
    assert "NO_COOKIE" in r


def test_check_login_status_logged_in(monkeypatch):
    monkeypatch.setattr(login_tool, "get_site_cookies", lambda origin: "s=tok")
    class _Resp:
        status_code = 200
        url = "https://x.com/account"
        text = "个人中心 退出登录"
    monkeypatch.setattr(login_tool.requests, "get", lambda *a, **kw: _Resp())
    r = check_login_status.func("https://x.com")
    assert "LOGGED_IN" in r


def test_check_login_status_logged_out_redirect(monkeypatch):
    monkeypatch.setattr(login_tool, "get_site_cookies", lambda origin: "s=tok")
    class _Resp:
        status_code = 200
        url = "https://x.com/login"
        text = "请登录"
    monkeypatch.setattr(login_tool.requests, "get", lambda *a, **kw: _Resp())
    r = check_login_status.func("https://x.com")
    assert "LOGGED_OUT" in r


def test_check_login_status_logged_out_form(monkeypatch):
    """探测页出现密码框 → 登录失效。"""
    monkeypatch.setattr(login_tool, "get_site_cookies", lambda origin: "s=tok")
    class _Resp:
        status_code = 200
        url = "https://x.com/account"
        text = PW_INPUT
    monkeypatch.setattr(login_tool.requests, "get", lambda *a, **kw: _Resp())
    r = check_login_status.func("https://x.com")
    assert "LOGGED_OUT" in r
