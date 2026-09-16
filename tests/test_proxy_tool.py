"""代理池工具测试 — 全离线，mock socket/requests/文件路径，不真连代理。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import proxy_tool
from crawagent.tools.proxy_tool import (
    add_proxy, remove_proxy, mark_proxy_failed, get_proxy, list_proxies,
    _format_proxy_url, _load_pool, _health_check,
)


class _Settings:
    """轻量 settings 替身，只暴露 proxy_tool 用到的字段。"""
    def __init__(self, default_proxy="", project_root=None):
        self.default_proxy = default_proxy
        self.project_root = project_root or Path(".")


def _setup(tmp_path, monkeypatch, default_proxy=""):
    monkeypatch.setattr(proxy_tool, "_proxies_file", lambda: tmp_path / "proxies.json")
    monkeypatch.setattr(proxy_tool, "get_settings", lambda: _Settings(default_proxy, tmp_path))


# ---- _format_proxy_url ----

def test_format_proxy_url_no_auth():
    assert _format_proxy_url({"scheme": "http", "host": "1.2.3.4", "port": 7890}) == "http://1.2.3.4:7890"


def test_format_proxy_url_with_auth():
    r = _format_proxy_url({"scheme": "socks5", "host": "h", "port": 1080,
                           "username": "u", "password": "p"})
    assert r == "socks5://u:p@h:1080"


def test_format_proxy_url_only_user():
    r = _format_proxy_url({"scheme": "https", "host": "h", "port": 443, "username": "u"})
    assert r == "https://u@h:443"


def test_format_proxy_url_default_scheme():
    assert _format_proxy_url({"host": "h", "port": 80}) == "http://h:80"


# ---- add_proxy 参数校验 ----

def test_add_proxy_bad_scheme():
    r = add_proxy.func("ftp", "h", 80)
    assert "ERROR" in r and "scheme" in r


def test_add_proxy_empty_host():
    r = add_proxy.func("http", "", 80)
    assert "ERROR" in r and "host" in r


def test_add_proxy_port_out_of_range():
    r = add_proxy.func("http", "h", 99999)
    assert "ERROR" in r and "范围" in r


def test_add_proxy_port_non_int():
    r = add_proxy.func("http", "h", "abc")
    assert "ERROR" in r and "整数" in r


# ---- 池 CRUD ----

def test_add_and_list_and_remove(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = add_proxy.func("http", "1.2.3.4", 7890, label="clash")
    assert "已添加" in r and "池大小 1" in r
    add_proxy.func("socks5", "5.6.7.8", 1080, username="u", password="p")
    out = list_proxies.func()
    assert "代理池（2 条）" in out
    # 更新已存在条目
    r2 = add_proxy.func("http", "1.2.3.4", 7890, label="updated")
    assert "已更新" in r2
    # 删除
    r3 = remove_proxy.func("1.2.3.4", 7890)
    assert "已删除" in r3 and "剩余 1" in r3
    # 删不存在的
    r4 = remove_proxy.func("1.2.3.4", 7890)
    assert "NOT_FOUND" in r4


def test_remove_proxy_bad_port():
    r = remove_proxy.func("h", "abc")
    assert "ERROR" in r


def test_list_proxies_empty_pool(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert list_proxies.func() == "代理池为空，调 add_proxy 添加"


def test_list_proxies_include_disabled(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    add_proxy.func("http", "h1", 80)
    for _ in range(3):
        mark_proxy_failed.func("h1", 80, "timeout")
    out_active = list_proxies.func()
    assert "代理池为空" in out_active  # disabled 被过滤
    out_all = list_proxies.func(True)
    assert "h1" in out_all and "disabled" in out_all


# ---- mark_proxy_failed ----

def test_mark_proxy_failed_accumulate_and_disable(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    add_proxy.func("http", "h", 80)
    r1 = mark_proxy_failed.func("h", 80, "timeout")
    assert "fail_count=1" in r1 and "status=active" in r1
    r2 = mark_proxy_failed.func("h", 80, "again")
    assert "fail_count=2" in r2 and "status=active" in r2
    r3 = mark_proxy_failed.func("h", 80)
    assert "fail_count=3" in r3 and "status=disabled" in r3


def test_mark_proxy_failed_not_found(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert "NOT_FOUND" in mark_proxy_failed.func("nope", 80)


# ---- get_proxy ----

def test_get_proxy_default_proxy_shortcut(monkeypatch):
    monkeypatch.setattr(proxy_tool, "get_settings", lambda: _Settings("http://clash:7890"))
    assert get_proxy.func() == "PROXY: http://clash:7890"


def test_get_proxy_empty_pool(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = get_proxy.func()
    assert "NO_PROXY" in r and "为空" in r


def test_get_proxy_healthy_only_skip_check(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    add_proxy.func("http", "h", 80)
    assert get_proxy.func(healthy_only=False).startswith("PROXY: http://h:80")


def test_get_proxy_all_unhealthy(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    add_proxy.func("http", "h", 80)
    monkeypatch.setattr(proxy_tool, "_health_check", lambda url, timeout=3.0: False)
    r = get_proxy.func(timeout=0.1)
    assert "NO_PROXY" in r and "健康检查均失败" in r


def test_get_proxy_label_filter_no_match(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    add_proxy.func("http", "h", 80, label="clash")
    r = get_proxy.func(label="vpn", healthy_only=False)
    assert "NO_PROXY" in r and "符合条件" in r


def test_get_proxy_health_ok(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    add_proxy.func("http", "h", 80)
    monkeypatch.setattr(proxy_tool, "_health_check", lambda url, timeout=3.0: True)
    assert get_proxy.func().startswith("PROXY: http://h:80")


def test_get_proxy_skips_disabled(tmp_path, monkeypatch):
    """disabled 的代理不在候选里。"""
    _setup(tmp_path, monkeypatch)
    add_proxy.func("http", "bad", 80)
    add_proxy.func("http", "good", 81)
    for _ in range(3):
        mark_proxy_failed.func("bad", 80, "x")
    monkeypatch.setattr(proxy_tool, "_health_check", lambda url, timeout=3.0: True)
    r = get_proxy.func()
    assert "good" in r and "bad" not in r


# ---- _health_check ----

def test_health_check_bad_url():
    assert _health_check("not-a-url") is False


def test_health_check_tcp_fail(monkeypatch):
    import socket
    def _fail(*a, **kw):
        raise OSError("no route")
    monkeypatch.setattr(socket, "create_connection", _fail)
    assert _health_check("http://1.2.3.4:80") is False


class _FakeCM:
    def __enter__(self): return None
    def __exit__(self, *a): return False


def test_health_check_tcp_ok_get_204(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: _FakeCM())
    class _Resp:
        status_code = 204
    monkeypatch.setattr(proxy_tool.requests, "get", lambda *a, **kw: _Resp())
    assert _health_check("http://h:80") is True


def test_health_check_tcp_ok_get_non_204(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: _FakeCM())
    class _Resp:
        status_code = 503
    monkeypatch.setattr(proxy_tool.requests, "get", lambda *a, **kw: _Resp())
    assert _health_check("http://h:80") is False


# ---- _load_pool 边界 ----

def test_load_pool_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy_tool, "_proxies_file", lambda: tmp_path / "nope.json")
    data = _load_pool()
    assert data["proxies"] == [] and data["cursor"] == 0


def test_load_pool_corrupt_json(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(proxy_tool, "_proxies_file", lambda: p)
    data = _load_pool()
    assert data["proxies"] == []
