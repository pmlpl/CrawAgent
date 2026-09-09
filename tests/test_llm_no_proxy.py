"""_ensure_no_proxy_for 回归测试 —— LAN/loopback 的 LLM host 自动进 NO_PROXY。

背景：用户系统代理（Privoxy 127.0.0.1:21882）开着，openai SDK 的 httpx honor
它，把 LAN 的 LLM 请求（如 100.83.19.7 Tailscale CGNAT）塞进 Privoxy → 转发失败
500 no-server-data。修法：get_llm 构造客户端前，把 loopback/私网 host 加进 NO_PROXY。
公网域名不动（可能需要代理才能到）。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.llm.model import _ensure_no_proxy_for


@pytest.fixture(autouse=True)
def clean_no_proxy(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    yield


def test_loopback_added():
    _ensure_no_proxy_for("http://127.0.0.1:8085/v1")
    assert "127.0.0.1" in os.environ["NO_PROXY"]


def test_localhost_added():
    _ensure_no_proxy_for("http://localhost:3000/v1")
    assert "localhost" in os.environ["NO_PROXY"]


def test_cgnat_lan_added():
    # Tailscale CGNAT 段 100.64/10 —— 用户自定义 LLM provider 实际地址
    _ensure_no_proxy_for("http://100.83.19.7:8085/v1")
    assert "100.83.19.7" in os.environ["NO_PROXY"]


def test_rfc1918_private_added():
    for host in ("10.0.0.5", "192.168.1.20", "172.16.3.4"):
        os.environ.pop("NO_PROXY", None)
        _ensure_no_proxy_for(f"http://{host}:8000/v1")
        assert host in os.environ["NO_PROXY"]


def test_public_domain_not_added():
    # 公网域名不自动 bypass（可能需要代理才能到）
    os.environ["NO_PROXY"] = "127.0.0.1"
    _ensure_no_proxy_for("https://api.deepseek.com/v1")
    assert "api.deepseek.com" not in os.environ["NO_PROXY"]


def test_idempotent_no_duplicates():
    _ensure_no_proxy_for("http://100.83.19.7:8085/v1")
    _ensure_no_proxy_for("http://100.83.19.7:8085/v1")
    parts = [p.strip() for p in os.environ["NO_PROXY"].split(",")]
    assert parts.count("100.83.19.7") == 1


def test_preserves_existing_no_proxy():
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    _ensure_no_proxy_for("http://100.83.19.7:8085/v1")
    parts = [p.strip() for p in os.environ["NO_PROXY"].split(",")]
    assert "127.0.0.1" in parts and "localhost" in parts and "100.83.19.7" in parts
