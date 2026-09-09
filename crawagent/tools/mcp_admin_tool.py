"""mcp_admin — 聊天即加 MCP：对话式增删停用 MCP server。

4 个 @tool：list_mcp_servers（只读）、add_mcp_server、remove_mcp_server、
disable_mcp_server。写操作复用设置路由抽出的 save_mcp_servers 共用函数
（校验/掩码/落盘/清缓存单一真源），生效走 reset_agent_cache 下轮装入工具箱。

授权闭环：三个写操作必须先 ask_user 确认（add/disable 一次，remove 二次），
ask_user 题面展示完整 server 配置——见 system.md 硬规则。本模块只负责执行，
ask_user 由 Agent 在调用本工具之前发起。
"""
from __future__ import annotations

import json

from langchain_core.tools import tool


def _current_servers() -> list[dict]:
    """读 .env MCP_SERVERS 原始（未脱敏）列表。"""
    from crawagent.config.settings import get_settings

    raw = get_settings().mcp_servers
    try:
        return json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError:
        return []


def _status_snapshot() -> list[dict]:
    """各 server 连通状态（同步探测，不跑 get_mcp_tools，避免 async 坑）。"""
    from crawagent.graph.skills import mcp_servers_status

    try:
        return mcp_servers_status()
    except Exception as e:
        return [{"error": str(e)}]


def _fmt_servers(servers: list[dict], status: list[dict] | None = None) -> str:
    """把 server 列表格式化成 AI 可读文本（鉴权头脱敏）。"""
    from crawagent.web.routers.settings import _mask_mcp_headers

    masked = _mask_mcp_headers(servers)
    st_by_name = {s.get("name"): s for s in (status or [])} if status else {}
    if not masked:
        return "[MCP_EMPTY] 当前没有配置任何 MCP server。"
    lines = [f"共 {len(masked)} 个 MCP server："]
    for s in masked:
        name = s.get("name", "?")
        transport = s.get("transport", "?")
        disabled = s.get("disabled", False)
        tag = "停用" if disabled else "启用"
        conn = st_by_name.get(name, {})
        reachable = conn.get("reachable") if isinstance(conn, dict) else None
        tools_n = conn.get("tools") if isinstance(conn, dict) else None
        if disabled:
            state = "停用"
        elif reachable is True:
            state = f"在线（工具 {tools_n if tools_n else '?'}）"
        elif reachable is False:
            state = "离线"
        else:
            state = "未知"
        if transport == "stdio":
            where = s.get("command", "?")
            args = s.get("args")
            if args:
                where += " " + " ".join(args)
        else:
            where = s.get("url", "?")
        lines.append(f"- {name} [{transport}/{tag}/{state}] {where}")
    return "\n".join(lines)


@tool
def list_mcp_servers() -> str:
    """列出当前已配置的所有 MCP server 及连通状态（只读，无需授权）。

    返回每个 server 的 name / transport / 启用状态 / 在线离线 / 工具数。
    add 前先调它避免重名；remove/disable 前确认目标存在；给用户报现状。
    """
    servers = _current_servers()
    return _fmt_servers(servers, _status_snapshot())


@tool
def add_mcp_server(
    name: str,
    transport: str,
    url: str = "",
    command: str = "",
    args: str = "",
    headers: str = "",
) -> str:
    """添加一个 MCP server 到配置（.env MCP_SERVERS），下轮生效。

    调用前 MUST 先 ask_user 确认，题面展示完整配置（含 command）。用户点"添加"
    后再调本工具。重名返回 [MCP_DUPLICATE]，让用户改用 disable 或先 remove。

    Args:
        name: server 名称（唯一，如 "firecrawl"）。
        transport: "streamable_http" 或 "stdio"。
        url: http 型必填，http(s):// 开头。
        command: stdio 型必填，可执行文件的绝对路径。
        args: stdio 可选，空格分隔的参数字符串（内部 split 成 list）。
        headers: 可选，JSON 字符串，如 '{"Authorization":"Bearer xxx"}'。
    """
    from crawagent.web.routers.settings import save_mcp_servers

    name = (name or "").strip()
    transport = (transport or "").strip()
    if transport not in ("streamable_http", "stdio"):
        return f"[MCP_ERROR] transport 必须是 streamable_http 或 stdio，收到: {transport}"

    entry: dict = {"name": name, "transport": transport, "disabled": False}
    if transport == "stdio":
        cmd = (command or "").strip()
        if not cmd:
            return f"[MCP_ERROR] stdio server '{name}' 缺 command（可执行文件绝对路径）"
        entry["command"] = cmd
        if args and args.strip():
            entry["args"] = args.split()
    else:
        u = (url or "").strip()
        if not u.startswith(("http://", "https://")):
            return f"[MCP_ERROR] server '{name}' 的 url 必须以 http(s):// 开头"
        entry["url"] = u

    if headers and headers.strip():
        try:
            parsed = json.loads(headers)
        except json.JSONDecodeError as e:
            return f"[MCP_ERROR] headers 不是合法 JSON: {e}"
        if isinstance(parsed, dict) and parsed:
            entry["headers"] = parsed

    current = _current_servers()
    if any(s.get("name") == name for s in current):
        return (
            f"[MCP_DUPLICATE] 名为 '{name}' 的 server 已存在。"
            f"改用 disable_mcp_server 停用，或先 remove_mcp_server 再加。"
        )

    res = save_mcp_servers(current + [entry])
    if not res.get("ok"):
        return f"[MCP_ERROR] 添加失败: {res.get('error', '未知错误')}"
    # 用真实连通性回告（不跑 get_mcp_tools 预热，同步探测足够判断在线/离线）
    status = _status_snapshot()
    st = next((s for s in status if isinstance(s, dict) and s.get("name") == name), {})
    reachable = st.get("reachable")
    base = f"[MCP_ADDED] 已添加 '{name}'。下条消息生效（reset_agent_cache 已触发，原生 MCP 工具下轮装入工具箱）。"
    if reachable is True:
        tools_n = st.get("tools", 0) or 0
        return f"{base} 在线，装入 {tools_n} 个工具。"
    if reachable is False:
        return (
            f"{base} 但握手失败（服务未启动/端口未监听/token 错）。"
            f"启动后用 check_mcp_status 诊断，或下轮自动重试装入。"
        )
    return base


@tool
def remove_mcp_server(name: str) -> str:
    """从配置删除一个 MCP server（.env MCP_SERVERS），下轮生效。

    调用前 MUST 先 ask_user 二次确认（删除不可恢复）：
    ask_user("确认删除 MCP server「X」？此操作不可恢复。", ["删除", "取消"])。

    Args:
        name: 要删除的 server 名称。
    """
    from crawagent.web.routers.settings import save_mcp_servers

    name = (name or "").strip()
    current = _current_servers()
    if not any(s.get("name") == name for s in current):
        return f"[MCP_NOT_FOUND] 没有名为 '{name}' 的 server。可用 list_mcp_servers 查看现有配置。"
    filtered = [s for s in current if s.get("name") != name]
    res = save_mcp_servers(filtered)
    if not res.get("ok"):
        return f"[MCP_ERROR] 删除失败: {res.get('error', '未知错误')}"
    return f"[MCP_REMOVED] 已删除 '{name}'。下条消息生效，对应原生 MCP 工具下轮不再装入。"


@tool
def disable_mcp_server(name: str) -> str:
    """停用一个 MCP server（保留配置，标记 disabled，下轮不再装入其工具）。

    调用前 MUST 先 ask_user 确认：
    ask_user("要停用 MCP server「X」吗？停用后其工具下轮不再装入。", ["停用", "取消"])。

    想彻底删除用 remove_mcp_server；想恢复用本工具的反向（目前手动去设置页重新启用，
    或删除后重新 add）。本工具只做停用方向。

    Args:
        name: 要停用的 server 名称。
    """
    from crawagent.web.routers.settings import save_mcp_servers

    name = (name or "").strip()
    current = _current_servers()
    target = next((s for s in current if s.get("name") == name), None)
    if target is None:
        return f"[MCP_NOT_FOUND] 没有名为 '{name}' 的 server。可用 list_mcp_servers 查看现有配置。"
    if target.get("disabled"):
        return f"[MCP_ALREADY] '{name}' 已是停用状态，无需重复操作。"
    target["disabled"] = True
    res = save_mcp_servers(current)
    if not res.get("ok"):
        return f"[MCP_ERROR] 停用失败: {res.get('error', '未知错误')}"
    return f"[MCP_DISABLED] 已停用 '{name}'。下条消息生效，其原生 MCP 工具下轮不再装入（配置保留，可恢复）。"
