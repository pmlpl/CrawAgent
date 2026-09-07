"""MCP 抓包工具包装 — 带 Human-in-the-loop 引导的 Agent 友好接口。

原生 MCP start_capture / stop_capture 存在 bug：
  - start_capture 在 CDP debugger 未就绪的 blank tab 上会 hang >= 120s
  - stop_capture 同样可能 hang
因此包装策略：
  1. 从原生 MCP 工具列表中 移除 start_capture / stop_capture
  2. 添加 wait_capture_ready 工具 —— 轮询 list_sessions，超时后返回引导提示
  3. Agent 的工作流变成：
     create_session → wait_capture_ready
       ↓ (未超时，running) → get_requests / get_hooks / run_analysis
       ↓ (超时，stopped) → Agent 用中文引导用户手动点 Start Capture → 等待用户回复

Token 自动同步：
  不再需要手动维护 .env 里的 MCP token。启动时自动读取
  Electron anything-analyzer 的 mcp-server-config.json，拿真实 token 覆盖
  MCP_SERVERS 配置。Electron 里改 token、重开 Electron 都不用重新配置 CrawAgent。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from crawagent.graph.skills import ensure_mcp_started, get_mcp_tools as _get_raw_mcp_tools


# ---------------------------------------------------------------------------
# 自动从 Electron anything-analyzer 的配置里拿 token
# ---------------------------------------------------------------------------

def _electron_mcp_config_path() -> Path | None:
    """返回 anything-analyzer 的 mcp-server-config.json 路径。

    Windows: %APPDATA%/anything-analyzer/mcp-server-config.json
    macOS:   ~/Library/Application Support/anything-analyzer/mcp-server-config.json
    Linux:   ~/.config/anything-analyzer/mcp-server-config.json
    """
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "anything-analyzer" / "mcp-server-config.json"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "anything-analyzer" / "mcp-server-config.json"
    else:
        return Path.home() / ".config" / "anything-analyzer" / "mcp-server-config.json"


def _load_electron_mcp_config() -> dict | None:
    """读 Electron 配置。不存在或坏了返回 None（不是抛异常）。"""
    cfg_path = _electron_mcp_config_path()
    if not cfg_path or not cfg_path.exists():
        return None
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def auto_sync_mcp_token() -> bool:
    """读 Electron 的 mcp-server-config.json，用真实 token 覆盖 MCP_SERVERS。

    返回 True = 成功同步，False = 没找到 Electron 配置或同步失败。
    如果 Electron 的 enabled=false，会在 MCP_SERVERS 里保留配置但不启
    动 MCP 工具（由 Agent 通过 check_electron_mcp_status 引导用户去开）。
    """
    electron_cfg = _load_electron_mcp_config()
    if not electron_cfg:
        print("[MCP] 未找到 anything-analyzer 配置文件，跳过 token 自动同步")
        return False

    token = electron_cfg.get("authToken", "")
    host = electron_cfg.get("host", "0.0.0.0")
    port = int(electron_cfg.get("port", 23816))
    enabled = bool(electron_cfg.get("enabled", False))

    # 如果 .env 里根本没配 MCP_SERVERS，我们也帮用户生成一份默认值
    try:
        raw = os.environ.get("MCP_SERVERS", "")
        servers = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        servers = []

    # 找已有的 anything-analyzer server 或新建一个
    target = None
    for srv in servers:
        if srv.get("transport") in ("streamable_http", "sse"):
            target = srv
            break

    if target is None:
        target = {
            "name": "anything-analyzer",
            "transport": "streamable_http",
            "url": f"http://127.0.0.1:{port}/mcp",
            "headers": {"Authorization": f"Bearer {token}"},
        }
        servers.append(target)

    # 用 Electron 的真实 token + 地址覆盖
    target["url"] = f"http://127.0.0.1:{port}/mcp"
    headers = target.setdefault("headers", {})
    headers["Authorization"] = f"Bearer {token}"

    os.environ["MCP_SERVERS"] = json.dumps(servers)

    if not enabled:
        print(f"[MCP] Electron anything-analyzer 未启用 MCP Server（enabled=false）")
    else:
        print(f"[MCP] 已从 Electron 同步 token: port={port} enabled={enabled}")

    return True


def _mcp_endpoint() -> dict | None:
    """从 MCP_SERVERS 配置里解析出 streamable_http server 的 URL + headers.

    注意：os.environ['MCP_SERVERS'] 已被 auto_sync_mcp_token() 覆盖成
    Electron 的真实 token，所以这里读到的永远是最新的。
    """
    try:
        raw = os.environ.get("MCP_SERVERS", "")
        servers = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return None
    for srv in servers:
        if srv.get("transport") in ("streamable_http", "sse"):
            return srv
    return None


# Streamable HTTP 协议要求：先 initialize 拿 session ID，后续每个请求都得
# 带 Mcp-Session-Id 头，否则 server 返 400 "missing session ID"。
# _call_mcp 是无状态直连，必须自己维护这个会话 ID。
_mcp_session_id: str | None = None
_mcp_rpc_id: int = 0


def _next_rpc_id() -> int:
    global _mcp_rpc_id
    _mcp_rpc_id += 1
    return _mcp_rpc_id


def _reset_mcp_session() -> None:
    """丢弃缓存的 session ID（server 重启 / 会话过期后强制重新握手）。"""
    global _mcp_session_id
    _mcp_session_id = None


def _ensure_mcp_session(
    base_headers: dict, url: str, timeout: float = 15.0
) -> tuple[str | None, dict | None]:
    """完成 MCP streamable HTTP 握手并缓存 session ID。

    流程：initialize → 取响应头 Mcp-Session-Id → 发 notifications/initialized。
    已缓存则直接返回，不重复握手。

    Returns: (session_id, error_dict)。成功时 error_dict=None；失败时 session_id=None。
    """
    global _mcp_session_id
    if _mcp_session_id:
        return _mcp_session_id, None

    import httpx

    init_body = {
        "jsonrpc": "2.0",
        "id": _next_rpc_id(),
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "crawagent", "version": "1.0"},
        },
    }
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as c:
            resp = c.post(url, json=init_body, headers=base_headers)
    except httpx.TransportError as e:
        # 连接被拒（WinError 10061）/ 端口未监听 / 服务未启动 → 返回错误字典，
        # 让 check_mcp_status 的错误分支接管（自动拉起 / 引导用户），而不是裸异常漏到任务层
        return None, {
            "error": {
                "kind": "connection_refused",
                "message": f"MCP 连接失败: {e}（anything-analyzer 未启动或 MCP 端口未监听）",
            }
        }

    if resp.status_code != 200:
        err_text = _parse_sse_body(resp.text)
        return None, {
            "error": {
                "status": resp.status_code,
                "message": err_text or f"initialize HTTP {resp.status_code}",
            }
        }
    sid = resp.headers.get("mcp-session-id")
    if not sid:
        return None, {"error": {"message": "initialize 成功但无 mcp-session-id 头"}}

    # 协议要求：initialize 之后必须发 notifications/initialized 通知
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as c:
            c.post(url, json=notif, headers={**base_headers, "mcp-session-id": sid})
    except httpx.TransportError as e:
        # notif 失败不致命：session id 已拿到，后续请求仍可尝试
        print(f"[MCP] notifications/initialized 发送失败（忽略）: {e}")

    _mcp_session_id = sid
    return sid, None


def _parse_sse_body(text: str) -> str:
    """从 SSE envelope 里抠出 data 行的 JSON 文本；非 SSE 原样返回。"""
    text = text.strip()
    if text.startswith("event:"):
        for line in text.split("\n"):
            if line.startswith("data:"):
                return line[5:].strip()
    return text


def _call_mcp(method: str, params: dict | None = None, timeout: float = 30.0) -> Any:
    """直接用 httpx 调 MCP streamable_http endpoint（不经过 langchain adapter，轻量快速）。

    自动管理 streamable HTTP 会话：首次调用先 initialize 握手拿 session ID，
    之后所有请求带 Mcp-Session-Id 头。会话过期（400 session 错误）时自动重试一次。
    """
    import httpx

    endpoint = _mcp_endpoint()
    if not endpoint:
        return {"error": "MCP not configured"}

    url = endpoint["url"]
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    # Authorization 等鉴权头从配置里继承
    for k, v in endpoint.get("headers", {}).items():
        headers[k] = v

    body: dict = {"jsonrpc": "2.0", "id": _next_rpc_id(), "method": method}
    if params is not None:
        body["params"] = params

    for attempt in (0, 1):  # 第二轮：重置 session 后重试一次
        sid, err = _ensure_mcp_session(headers, url)
        if not sid:
            # 握手失败（token 错 / 端口没监听）→ 直接返回，带原始错误信息
            return err or {"error": {"message": "MCP session initialization failed"}}
        req_headers = {**headers, "mcp-session-id": sid}

        try:
            with httpx.Client(trust_env=False, timeout=timeout) as c:
                resp = c.post(url, json=body, headers=req_headers)
        except httpx.TransportError as e:
            # 服务中途掉线 / 连接被拒 → 重置 session 并返回错误，交给上层优雅处理
            _reset_mcp_session()
            return {
                "error": {
                    "kind": "connection_refused",
                    "message": f"MCP 连接失败: {e}（anything-analyzer 未启动或中途退出）",
                }
            }

        # 会话过期 / 未识别 → 重置 session 重试一次
        if attempt == 0 and resp.status_code in (400, 406) and "session" in resp.text.lower():
            print(f"[MCP] session 失效（{resp.status_code}），重新握手重试: {resp.text[:120]}")
            _reset_mcp_session()
            continue

        text = _parse_sse_body(resp.text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text, "status": resp.status_code}

    return {"error": f"MCP call {method} failed after session retry"}


def _extract_tool_result(mcp_response: dict) -> Any:
    """从 MCP tools/call 响应里提取实际返回值（自动识别 JSON 字符串）。"""
    content = mcp_response.get("result", {}).get("content", [])
    if not content:
        return mcp_response
    text = content[0].get("text", "")
    if isinstance(text, str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


@tool
def wait_capture_ready(session_id: str, timeout: int = 15) -> str:
    """等待抓包会话进入 running 状态。

    用于 create_session 之后、调用 get_requests 之前。MCP start_capture API 在
    Electron CDP 调试器未就绪的 blank tab 上会 hang，因此改为：
    1. 轮询 list_sessions 检查状态
    2. 超时仍未 running → 返回引导提示，让 Agent 用中文请用户手动点 Start Capture

    Args:
        session_id: create_session 返回的会话 ID
        timeout: 最多等待秒数（默认 15）

    Returns:
        "[CAPTURE_READY]" → 会话已 running，可以继续抓包
        "[CAPTURE_NOT_STARTED] ..." → 用户需要手动操作，Agent 应引导用户
        "[ERROR] ..." → 查询失败
    """
    deadline = time.time() + max(timeout, 1)

    while time.time() < deadline:
        resp = _call_mcp("tools/call", {"name": "list_sessions", "arguments": {}}, timeout=10)
        sessions = _extract_tool_result(resp)
        if isinstance(sessions, list):
            for s in sessions:
                if isinstance(s, dict) and s.get("id") == session_id:
                    if s.get("status") == "running":
                        return "[CAPTURE_READY]"
                    break  # 会话存在但还没 running
        time.sleep(1.5)

    # 超时 → 返回结构化引导信息
    # 先拿到会话的人类可读名称
    name_hint = ""
    resp = _call_mcp("tools/call", {"name": "list_sessions", "arguments": {}}, timeout=10)
    sessions = _extract_tool_result(resp)
    if isinstance(sessions, list):
        for s in sessions:
            if isinstance(s, dict) and s.get("id") == session_id:
                name_hint = f"（名称: {s.get('name', '')}）"
                break

    return (
        f"[CAPTURE_NOT_STARTED] 会话 {session_id}{name_hint} 仍处于 stopped 状态，"
        f"MCP start_capture API 在此环境下会 hang。\n"
        f"请用中文引导用户：\n"
        f"1. 在 anything-analyzer 的 Electron 应用界面左侧 Sessions 列表里找到该会话\n"
        f"2. 点击会话右侧的 Start Capture 按钮\n"
        f"3. 等状态变成 Running 后，让用户回来跟你说「好了」\n"
        f"用户回复后，你再调用 wait_capture_ready 确认状态。"
    )


@tool
def check_mcp_status() -> str:
    """检查 anything-analyzer MCP Server 是否就绪。

    在调用任何抓包工具之前，先跑一下这个确认连通性。它会：
    1. 重新从 Electron 配置里同步 token（如果用户刚在 Electron UI 里改了）
    2. 发 ping 验证 MCP server 是否监听 + token 是否正确

    Returns:
        "[MCP_READY] port=xxx" → MCP 可用
        "[MCP_NOT_ENABLED] ..." → Electron 里 MCP 开关没开
        "[MCP_UNREACHABLE] ..." → Electron 开了但端口没监听（可能 Electron 没运行）
        "[MCP_AUTH_FAILED] ..." → token 不对
    """
    # 重新同步一次 token（允许用户中途改 Electron 配置后 Agent 继续）
    auto_sync_mcp_token()

    electron_cfg = _load_electron_mcp_config()
    if electron_cfg and not electron_cfg.get("enabled", False):
        return (
            "[MCP_NOT_ENABLED] anything-analyzer 的 MCP Server 开关还没打开。\n"
            "请用中文引导用户：\n"
            "1. 打开 anything-analyzer 的 Electron 应用\n"
            "2. 点右上角齿轮 → Settings\n"
            "3. 找到 MCP Server 那一节\n"
            "4. 把 Enable 开关打开（token 自动填好的，不用改）\n"
            "5. 点 Save，等 3 秒让 MCP server 监听"
        )

    resp = _call_mcp("ping", {}, timeout=5)
    if "error" in resp:
        err = resp["error"]
        msg = str(err.get("message", err)) if isinstance(err, dict) else str(err)
        if "401" in msg or "Unauthorized" in msg or "auth" in msg.lower():
            return (
                f"[MCP_AUTH_FAILED] token 认证失败: {msg}\n"
                f"请让用户在 Electron 设置 → MCP Server 里检查 token 是否正确。"
            )
        # AI 显式检查状态 = 有抓包意图 → 授权自动拉起 anything-analyzer（绕过开关）
        if ensure_mcp_started(wait=True, force=True):
            resp2 = _call_mcp("ping", {}, timeout=5)
            if "error" not in resp2:
                # 拉起成功但本 Agent 的工具箱是构建时装好的：重建缓存让下一轮
                # 就能拿到原生 MCP 工具（create_session / get_requests ...）
                note = "若你本轮工具列表里没有 create_session 等抓包工具，请让用户新发一条消息触发重建"
                try:
                    from crawagent.web.state import reset_agent_cache
                    reset_agent_cache()
                except Exception:
                    note = "工具缓存重建失败：请让用户重启后端后再试"
                return (
                    f"[MCP_READY] （已自动拉起 anything-analyzer）\n"
                    f"MCP 服务已就绪，{note}，然后即可继续抓包流程。"
                )
        return (
            f"[MCP_UNREACHABLE] MCP server 没响应: {msg}\n"
            f"自动拉起未成功（可能没配 ANYTHING_ANALYZER_PATH / 自动启动开关没开）。"
            f"请引导用户手动打开 anything-analyzer 应用并开启 MCP Server。"
        )

    cfg = _load_electron_mcp_config() or {}
    return f"[MCP_READY] port={cfg.get('port', 23816)} enabled={cfg.get('enabled')}"


def build_mcp_tools() -> list:
    """构建最终给 Agent 的 MCP 工具列表。

    1. 先从 Electron anything-analyzer 的 mcp-server-config.json 同步 token
       （Electron 里改了 token / 重开 Electron 都不用手动改 .env）
    2. 如果 Electron enabled=false → 返回 [check_mcp_status] 让 Agent 引导用户去开
    3. 如果 MCP 可连 → 加载原生 MCP 工具 + 注入 sync wrapper + 移除有 bug 的 start/stop_capture
    4. 加 wait_capture_ready + check_mcp_status
    """
    import asyncio

    # 先同步 Electron 的真实 token
    auto_sync_mcp_token()

    # 连不上 MCP 的话也返回工具 —— Agent 可以用 check_mcp_status 引导用户
    electron_cfg = _load_electron_mcp_config() or {}
    mcp_enabled = bool(electron_cfg.get("enabled", False))

    BLOCKED = {"start_capture", "stop_capture"}

    def _async_to_sync(arun_func):
        def _sync_run(*args, **kwargs):
            merged = dict(kwargs)
            for a in args:
                if isinstance(a, dict):
                    merged.update(a)
            return asyncio.run(arun_func(**merged, config=None))
        return _sync_run

    final = [check_mcp_status, wait_capture_ready]

    if mcp_enabled:
        # 不做启动期自动拉起（双开根源）：MCP server 没运行时其工具本轮装不进来，
        # AI 需要时会经 ask_user 询问用户，同意后走 check_mcp_status(force) 拉起
        # 并重建 Agent 工具箱。这里只做装配，绝不启动服务。
        try:
            raw_tools = _get_raw_mcp_tools()
            for t in raw_tools:
                if t.name in BLOCKED:
                    continue
                if hasattr(t, "_arun"):
                    t._run = _async_to_sync(t._arun)
                final.append(t)
        except Exception as e:
            print(f"[MCP] 加载工具失败: {e}")
            # 继续返回 check_mcp_status 让 Agent 引导用户

    return final
