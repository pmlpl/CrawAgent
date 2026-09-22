"""proxy_tool 测试 — 代理池管理 / TCP 探活 / HTTP 验证。

依赖测试约定：monkeypatch ``_proxies_file`` 让持久化落到 tmp_path；
mock ``socket.create_connection`` + ``requests.get`` 拦截网络层。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import proxy_tool


# ---------------------------------------------------------------------------
# _find_proxy
# ---------------------------------------------------------------------------

def test_find_proxy_found():
    """池中存在的 host+port → 返回代理 dict。"""
    data = {"proxies": [{"host": "1.2.3.4", "port": 8080, "scheme": "http"}]}
    proxy = proxy_tool._find_proxy(data, "1.2.3.4", 8080)
    assert proxy is not None
    assert proxy["host"] == "1.2.3.4"


def test_find_proxy_not_found():
    """不存在的 host+port → None。"""
    data = {"proxies": [{"host": "1.2.3.4", "port": 8080, "scheme": "http"}]}
    assert proxy_tool._find_proxy(data, "5.6.7.8", 9090) is None


def test_find_proxy_empty_pool():
    """空池 → None。"""
    assert proxy_tool._find_proxy({"proxies": []}, "1.2.3.4", 8080) is None


def test_find_proxy_matches_only_host_and_port():
    """只匹配 host+port（其他字段差异不影响查找）。"""
    data = {"proxies": [{"host": "1.2.3.4", "port": 8080, "scheme": "socks5", "label": "vpn"}]}
    proxy = proxy_tool._find_proxy(data, "1.2.3.4", 8080)
    assert proxy is not None
    assert proxy["scheme"] == "socks5"


# ---------------------------------------------------------------------------
# _health_check（TCP + HTTP 双重探活）
# ---------------------------------------------------------------------------

def test_health_check_tcp_connect_fails_returns_false(monkeypatch):
    """TCP 探活失败 → 立即返回 False（不发 HTTP 请求）。"""
    import socket as real_socket
    def fail(*a, **kw):
        raise OSError("connection refused")
    monkeypatch.setattr(real_socket, "create_connection", fail)

    with patch.object(proxy_tool.requests, "get") as mock_get:
        result = proxy_tool._health_check("http://1.2.3.4:8080", timeout=2.0)
    assert result is False
    mock_get.assert_not_called()


def test_health_check_tcp_ok_http_204_returns_true(monkeypatch):
    """TCP 通 + HTTP generate_204 → 返回 204 → True。"""
    import socket as real_socket
    monkeypatch.setattr(real_socket, "create_connection", lambda *a, **kw: MagicMock(__enter__=lambda s: s, __exit__=lambda *a: None))

    mock_resp = MagicMock()
    mock_resp.status_code = 204
    with patch.object(proxy_tool.requests, "get", return_value=mock_resp) as mock_get:
        result = proxy_tool._health_check("http://1.2.3.4:8080", timeout=2.0)
    assert result is True
    # 验证 proxies 参数
    assert mock_get.call_args.kwargs["proxies"] == {"http": "http://1.2.3.4:8080", "https": "http://1.2.3.4:8080"}
    assert mock_get.call_args.kwargs["allow_redirects"] is False


def test_health_check_tcp_ok_http_non_204_returns_false(monkeypatch):
    """TCP 通 + HTTP 200（被劫持页面）→ False。"""
    import socket as real_socket
    monkeypatch.setattr(real_socket, "create_connection", lambda *a, **kw: MagicMock(__enter__=lambda s: s, __exit__=lambda *a: None))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch.object(proxy_tool.requests, "get", return_value=mock_resp):
        result = proxy_tool._health_check("http://1.2.3.4:8080", timeout=2.0)
    assert result is False


def test_health_check_tcp_ok_http_exception_returns_false(monkeypatch):
    """TCP 通 + HTTP 抛异常 → False。"""
    import socket as real_socket
    import requests as real_requests
    monkeypatch.setattr(real_socket, "create_connection", lambda *a, **kw: MagicMock(__enter__=lambda s: s, __exit__=lambda *a: None))

    with patch.object(proxy_tool.requests, "get", side_effect=real_requests.Timeout("timeout")):
        result = proxy_tool._health_check("http://1.2.3.4:8080", timeout=2.0)
    assert result is False


def test_health_check_invalid_url_returns_false():
    """URL 无法解析 → False。"""
    # urlparse 不会抛错，host 为空时 return False
    assert proxy_tool._health_check("not-a-valid-url", timeout=1.0) is False


# ---------------------------------------------------------------------------
# add_proxy 参数校验（monkeypatch _proxies_file 让持久化落 tmp_path）
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_pool(tmp_path, monkeypatch):
    """让 ``_proxies_file()`` 返回 tmp_path/proxies.json。"""
    pool = tmp_path / "proxies.json"
    monkeypatch.setattr(proxy_tool, "_proxies_file", lambda: pool)
    return pool


def test_add_proxy_invalid_scheme(tmp_pool):
    """非法 scheme → ERROR。"""
    out = proxy_tool.add_proxy.func("ftp", "1.2.3.4", 8080)
    assert "ERROR" in out
    assert "scheme" in out


def test_add_proxy_empty_host(tmp_pool):
    """空 host → ERROR。"""
    out = proxy_tool.add_proxy.func("http", "", 8080)
    assert "ERROR" in out
    assert "host" in out


def test_add_proxy_invalid_port(tmp_pool):
    """port 超出范围 → ERROR。"""
    out = proxy_tool.add_proxy.func("http", "1.2.3.4", 99999)
    assert "ERROR" in out
    assert "port 范围" in out


def test_add_proxy_port_not_int(tmp_pool):
    """port 非整数 → ERROR。"""
    out = proxy_tool.add_proxy.func("http", "1.2.3.4", "not-a-port")
    assert "ERROR" in out
    assert "port 必须是整数" in out


def test_add_proxy_success(tmp_pool):
    """正常参数 → 添加成功 + 持久化。"""
    out = proxy_tool.add_proxy.func("http", "1.2.3.4", 8080, label="test")
    assert "已添加" in out or "已更新" in out
    assert "http://1.2.3.4:8080" in out
    # 验证持久化
    import json
    saved = json.loads(tmp_pool.read_text(encoding="utf-8"))
    assert len(saved["proxies"]) == 1
    assert saved["proxies"][0]["host"] == "1.2.3.4"


def test_add_proxy_existing_updates(tmp_pool):
    """已存在的 host+port → 更新字段，不增加条目数。"""
    import json
    tmp_pool.write_text(json.dumps({"proxies": [{"host": "1.2.3.4", "port": 8080, "scheme": "http", "label": "old"}]}))

    out = proxy_tool.add_proxy.func("socks5", "1.2.3.4", 8080, label="new")
    assert "已更新" in out or "已添加" in out
    saved = json.loads(tmp_pool.read_text(encoding="utf-8"))
    assert len(saved["proxies"]) == 1
    assert saved["proxies"][0]["scheme"] == "socks5"
    assert saved["proxies"][0]["label"] == "new"