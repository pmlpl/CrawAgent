"""代理池工具 — 维护可轮换的 http/https/socks5 代理池。

设计意图：
    - 池数据持久化到 data/proxies.json，零侵入热路径（Agent 按需调取，
      不在主循环自动注入环境变量）。
    - run_custom_script / browser_render / browse_and_crawl 调用前可先用
      get_proxy 取一个可用代理，再注入 requests 的 proxies= 参数或
      Playwright launch(proxy=...)。
    - 健康检查用 TCP socket.create_connection + 显式 GET 双重验证：
      socket 探活保证不阻塞；GET 探活保证代理真的能转 HTTP/HTTPS。
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from langchain_core.tools import tool

from crawagent.config.settings import get_settings

_LOCK = threading.Lock()  # 保护 proxies.json 读写


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def _proxies_file() -> Path:
    """data/proxies.json 路径。"""
    return get_settings().project_root / "data" / "proxies.json"


def _format_proxy_url(p: dict) -> str:
    """把代理 dict 格式化为 URL 字串：scheme://user:pass@host:port"""
    scheme = p.get("scheme", "http")
    host = p.get("host", "")
    port = p.get("port", 0)
    user = p.get("username", "")
    pwd = p.get("password", "")
    if user and pwd:
        return f"{scheme}://{user}:{pwd}@{host}:{port}"
    if user:
        return f"{scheme}://{user}@{host}:{port}"
    return f"{scheme}://{host}:{port}"


def _load_pool() -> dict:
    """读 proxies.json；不存在时返回空池结构。"""
    p = _proxies_file()
    if not p.exists():
        return {"version": 1, "cursor": 0, "proxies": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "proxies" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"version": 1, "cursor": 0, "proxies": []}


def _save_pool(data: dict) -> None:
    """原子写 proxies.json（tmp 文件 + os.replace，Windows 安全）。"""
    p = _proxies_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", dir=str(p.parent), delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, str(p))


def _health_check(proxy_url: str, timeout: float = 3.0) -> bool:
    """TCP socket + GET generate_204 双重探活。

    第一层：TCP socket.create_connection 探 host:port（快，1.5s 超时）。
    第二层：通过代理 GET https://www.gstatic.com/generate_204（验证真的能转发 HTTP）。
    任一失败即返回 False。
    """
    parsed = urlparse(proxy_url if "://" in proxy_url else "http://" + proxy_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host or not port:
        return False

    # 第一层：TCP 探活
    try:
        with socket.create_connection((host, port), timeout=min(timeout, 1.5)):
            pass
    except OSError:
        return False

    # 第二层：通过代理 GET generate_204
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.get(
            "https://www.gstatic.com/generate_204",
            proxies=proxies,
            timeout=timeout,
            allow_redirects=False,
        )
        return resp.status_code == 204
    except Exception:
        return False


def _find_proxy(data: dict, host: str, port: int) -> dict | None:
    """在池里找 host+port 匹配的代理 dict。"""
    for p in data.get("proxies", []):
        if p.get("host") == host and p.get("port") == port:
            return p
    return None


# ---------------------------------------------------------------------------
# @tool 工具
# ---------------------------------------------------------------------------

@tool
def add_proxy(scheme: str, host: str, port: int,
              username: str = "", password: str = "", label: str = "") -> str:
    """把一个代理加入代理池（持久化到 data/proxies.json）。

    适用场景：用户主动提供代理或自己搭建了代理服务时，把它入池后，
    后续 run_custom_script / browser_render 调用前可先用 get_proxy 取一个
    可用代理，再注入 requests 的 proxies= 参数或 Playwright launch(proxy=...)。

    参数：
        scheme:   代理协议，http / https / socks5 三选一。
        host:     代理 IP 或域名，例 "127.0.0.1" / "proxy.example.com"。
        port:     代理端口，例 7890 / 1080。
        username: 可选，需要鉴权的代理填用户名。
        password: 可选，代理密码（只存本地文件，不进 LLM 上下文）。
        label:    可选自由标签，例 "clash-vpn" / "home-relay"。
                  后续 get_proxy(label=...) 可以指定只从带这个标签的代理里选。

    返回：
        成功："已添加: <proxy_url> (label=..., 池大小 N)"
        失败："ERROR add_proxy: <原因>"
    """
    scheme = (scheme or "http").strip().lower()
    if scheme not in ("http", "https", "socks5", "socks5h"):
        return f"ERROR add_proxy: scheme 只支持 http/https/socks5/socks5h，收到 {scheme}"
    host = (host or "").strip()
    if not host:
        return "ERROR add_proxy: host 不能为空"
    try:
        port = int(port)
    except (TypeError, ValueError):
        return f"ERROR add_proxy: port 必须是整数，收到 {port}"
    if not (1 <= port <= 65535):
        return f"ERROR add_proxy: port 范围 1-65535，收到 {port}"

    with _LOCK:
        data = _load_pool()
        existing = _find_proxy(data, host, port)
        now = datetime.now().isoformat(timespec="seconds")
        if existing:
            # 更新已有条目
            existing["scheme"] = scheme
            if username:
                existing["username"] = username
            if password:
                existing["password"] = password
            if label:
                existing["label"] = label
            existing["updated_at"] = now
            _save_pool(data)
            return f"已更新: {_format_proxy_url(existing)} (label={label or existing.get('label','')}, 池大小 {len(data['proxies'])})"

        new_entry = {
            "host": host,
            "port": port,
            "scheme": scheme,
            "username": username or "",
            "password": password or "",
            "label": label or "",
            "added_at": now,
            "last_used_at": "",
            "last_check_at": "",
            "last_check_ok": False,
            "fail_count": 0,
            "success_streak": 0,
            "disabled": False,
            "disabled_reason": "",
        }
        data["proxies"].append(new_entry)
        _save_pool(data)
        return f"已添加: {_format_proxy_url(new_entry)} (label={label}, 池大小 {len(data['proxies'])})"


@tool
def remove_proxy(host: str, port: int) -> str:
    """从代理池移除一个代理（按 host+port 唯一定位）。

    不存在的代理会返回 NOT_FOUND；删除后立刻持久化。

    参数：
        host: 代理 host
        port: 代理 port

    返回：
        "已删除: host:port (剩余 N)" 或 "NOT_FOUND: host:port"
    """
    host = (host or "").strip()
    try:
        port = int(port)
    except (TypeError, ValueError):
        return f"ERROR remove_proxy: port 必须是整数"

    with _LOCK:
        data = _load_pool()
        proxies = data.get("proxies", [])
        before = len(proxies)
        data["proxies"] = [p for p in proxies if not (p.get("host") == host and p.get("port") == port)]
        if len(data["proxies"]) == before:
            return f"NOT_FOUND: {host}:{port}"
        _save_pool(data)
        return f"已删除: {host}:{port} (剩余 {len(data['proxies'])})"


@tool
def mark_proxy_failed(host: str, port: int, reason: str = "") -> str:
    """把某代理标记为本次使用失败（fail_count +1，连续 3 次失败自动 disable）。

    适用场景：Agent 用某代理抓取失败（连接超时/返回 403）后，立刻调它
    把代理状态降级，避免后续 get_proxy 再优先取这个。
    失败原因是可选的简短描述（"timeout" / "403 forbidden" 等）。

    被 disable 的代理默认不会出现在 get_proxy 候选池里，
    但仍保留在 proxies.json 备查（list_proxies(include_disabled=True) 可见）。

    参数：
        host:   代理 host
        port:   代理 port
        reason: 失败简述，最长 100 字

    返回：
        "已记录失败 (host:port, fail_count=N, status=active|disabled)"
    """
    host = (host or "").strip()
    try:
        port = int(port)
    except (TypeError, ValueError):
        return "ERROR mark_proxy_failed: port 必须是整数"
    reason = (reason or "")[:100]

    with _LOCK:
        data = _load_pool()
        p = _find_proxy(data, host, port)
        if not p:
            return f"NOT_FOUND: {host}:{port}"

        p["fail_count"] = p.get("fail_count", 0) + 1
        p["success_streak"] = 0
        p["last_check_ok"] = False
        p["last_check_at"] = datetime.now().isoformat(timespec="seconds")
        if reason:
            p["disabled_reason"] = reason

        status = "active"
        if p["fail_count"] >= 3 and not p.get("disabled"):
            p["disabled"] = True
            p["disabled_reason"] = f"连续失败 {p['fail_count']} 次" + (f": {reason}" if reason else "")
            status = "disabled"

        _save_pool(data)
        return f"已记录失败 ({host}:{port}, fail_count={p['fail_count']}, status={status})"


@tool
def get_proxy(label: str = "", healthy_only: bool = True,
              timeout: float = 3.0) -> str:
    """从代理池里轮询取一个可用代理（带 TCP 健康检查）。

    Agent 拿到代理 URL 后，决定怎么用：
      - run_custom_script：直接在脚本里 requests.get(url, proxies={"http": "<url>", "https": "<url>"})
      - browser_render：传 proxy="<url>" 参数给 browser_render
      - browse_and_crawl：目前不直接接 proxy 参数，需先调 add_proxy 入池
        再在 settings 里设 default_proxy

    健康检查：
      1) TCP socket.create_connection((host, port), timeout) 探活
      2) healthy_only=True 时额外发一次 GET https://www.gstatic.com/generate_204，
         非 204 或超时即视为不健康，自动跳下一个

    settings.default_proxy 非空时直接返回它，跳过池轮询（"我就一个 clash"场景）。

    参数：
        label:        可选标签过滤；只从带此 label 的代理里选
        healthy_only: True（默认）= 跳过 disabled / fail_count>=3 的代理；
                      False = 即使 unhealthy 也返回（用于调试）
        timeout:      单次健康检查超时秒数，默认 3.0

    返回：
        成功："PROXY: <scheme>://<user:pass>@<host>:<port>"
        池空或全部不可用："NO_PROXY: 代理池为空或全部不可用"
    """
    # default_proxy 短路
    settings = get_settings()
    if settings.default_proxy:
        return f"PROXY: {settings.default_proxy}"

    with _LOCK:
        data = _load_pool()

    proxies = data.get("proxies", [])
    if not proxies:
        return "NO_PROXY: 代理池为空，调 add_proxy 添加"

    # 过滤
    candidates = []
    for p in proxies:
        if label and p.get("label", "") != label:
            continue
        if healthy_only:
            if p.get("disabled"):
                continue
            if p.get("fail_count", 0) >= 3:
                continue
        candidates.append(p)

    if not candidates:
        return "NO_PROXY: 没有符合条件的代理（可能全部 disabled 或 label 不匹配）"

    # 轮询：从 cursor 开始
    cursor = data.get("cursor", 0) % len(candidates)
    now = datetime.now().isoformat(timespec="seconds")

    for i in range(len(candidates)):
        idx = (cursor + i) % len(candidates)
        p = candidates[idx]
        proxy_url = _format_proxy_url(p)

        if not healthy_only:
            # 跳过健康检查，直接返回
            p["last_used_at"] = now
            data["cursor"] = (idx + 1) % len(candidates)
            with _LOCK:
                _save_pool(data)
            return f"PROXY: {proxy_url}"

        # 健康检查
        ok = _health_check(proxy_url, timeout=timeout)
        p["last_check_at"] = now
        p["last_check_ok"] = ok
        if ok:
            p["fail_count"] = 0
            p["success_streak"] = p.get("success_streak", 0) + 1
            p["last_used_at"] = now
            data["cursor"] = (idx + 1) % len(candidates)
            with _LOCK:
                _save_pool(data)
            return f"PROXY: {proxy_url}"
        else:
            p["fail_count"] = p.get("fail_count", 0) + 1
            p["success_streak"] = 0
            if p["fail_count"] >= 3:
                p["disabled"] = True
                p["disabled_reason"] = f"健康检查连续失败 {p['fail_count']} 次"

    # 全部不健康
    with _LOCK:
        _save_pool(data)
    return "NO_PROXY: 代理池中所有代理健康检查均失败"


@tool
def list_proxies(include_disabled: bool = False) -> str:
    """列出代理池当前状态（持久化文件内容快照）。

    默认隐藏 disabled 的代理；设 include_disabled=True 可看全量（含失败次数）。
    每条记录显示：host:port / scheme / label / fail_count / status。

    参数：
        include_disabled: 是否显示 disabled 的代理（默认 False）

    返回：
        "代理池（N 条）:\\n1. host:port  scheme  label  fail=0  status=active\\n..."
        池空返回 "代理池为空，调 add_proxy 添加"
    """
    data = _load_pool()
    proxies = data.get("proxies", [])

    if not include_disabled:
        proxies = [p for p in proxies if not p.get("disabled")]

    if not proxies:
        return "代理池为空，调 add_proxy 添加"

    lines = [f"代理池（{len(proxies)} 条）:"]
    for i, p in enumerate(proxies, 1):
        host = p.get("host", "?")
        port = p.get("port", "?")
        scheme = p.get("scheme", "http")
        label = p.get("label", "")
        fail = p.get("fail_count", 0)
        streak = p.get("success_streak", 0)
        status = "disabled" if p.get("disabled") else "active"
        check = "✓" if p.get("last_check_ok") else "✗"
        line = f"{i}. {host}:{port}  {scheme}  label={label}  fail={fail}  streak={streak}  {check}  status={status}"
        lines.append(line)

    return "\n".join(lines)
