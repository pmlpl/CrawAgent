"""MCP server 管理 API：脱敏 / 保存 / 预热。

从 routers/settings.py 拆出（027），router 独立但 URL 路径不变。
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, Body

from crawagent.config.settings import get_settings
from .core import _save_env_updates

router = APIRouter()

def _mask_mcp_headers(servers: list[dict]) -> list[dict]:
    """MCP server 配置出前端前的鉴权头脱敏：值只留前 10 字符 + ***。

    历史教训（Key 覆盖 Bug）：回写时含 * 的值视为掩码、还原为已存原值，
    绝不把掩码写回 .env（见 _unmask_mcp_headers）。
    """
    out: list[dict] = []
    for srv in servers:
        s2 = {k: v for k, v in srv.items() if k != "headers"}
        headers = srv.get("headers") or {}
        if isinstance(headers, dict) and headers:
            s2["headers"] = {
                k: (str(v)[:10] + "***") if len(str(v)) > 12 else str(v)
                for k, v in headers.items()
            }
        out.append(s2)
    return out


def _unmask_mcp_headers(incoming: list[dict], current: list[dict]) -> list[dict]:
    """回写还原：头值含 *** 的视为掩码，用当前 .env 里的原值顶回（按 name+header key 对位）。"""
    cur_by_name = {str(s.get("name")): s for s in current if isinstance(s, dict)}
    out: list[dict] = []
    for srv in incoming:
        s2 = dict(srv)
        headers = srv.get("headers")
        if isinstance(headers, dict) and headers:
            fixed = dict(headers)
            old = (cur_by_name.get(str(srv.get("name"))) or {}).get("headers") or {}
            for k, v in fixed.items():
                if isinstance(v, str) and "***" in v and isinstance(old.get(k), str):
                    fixed[k] = old[k]
            s2["headers"] = fixed
        out.append(s2)
    return out




def save_mcp_servers(incoming: list[dict]) -> dict:
    """整表保存 MCP server 列表（共用真源：设置页路由 + 聊天即加 @tool 都调它）。

    同步：校验 → _unmask_mcp_headers 保鉴权 → 写 .env → 清 settings/MCP/Agent
    三层缓存。不做预热握手（async，留给调用方：路由 await to_thread(get_mcp_tools)；
    @tool 用同步 mcp_servers_status 回告连通性即可）。

    Returns: {"ok": bool, "error"?: str, "mcp_servers": list[dict]（脱敏后)}
    """
    if not isinstance(incoming, list):
        return {"ok": False, "error": "servers 必须是数组"}

    s = get_settings()
    try:
        current = json.loads(s.mcp_servers) if s.mcp_servers.strip() else []
    except json.JSONDecodeError:
        current = []

    cleaned: list[dict] = []
    seen_names: set[str] = set()
    for srv in incoming:
        if not isinstance(srv, dict):
            continue
        name = str(srv.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "每个 server 都需要 name"}
        if name in seen_names:
            return {"ok": False, "error": f"server 名重复: {name}"}
        seen_names.add(name)
        entry = {
            "name": name,
            "transport": str(srv.get("transport") or "stdio"),
            "disabled": bool(srv.get("disabled")),
        }
        if entry["transport"] == "stdio":
            cmd = str(srv.get("command") or "").strip()
            if not cmd:
                return {"ok": False, "error": f"stdio server '{name}' 缺 command"}
            entry["command"] = cmd
            args = srv.get("args")
            if isinstance(args, list) and args:
                entry["args"] = [str(a) for a in args]
        else:
            url = str(srv.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                return {"ok": False, "error": f"server '{name}' 的 url 必须以 http(s):// 开头"}
            entry["url"] = url
        headers = srv.get("headers")
        if isinstance(headers, dict) and headers:
            entry["headers"] = headers
        cleaned.append(entry)

    cleaned = _unmask_mcp_headers(cleaned, current)
    _save_env_updates({"MCP_SERVERS": json.dumps(cleaned, ensure_ascii=False)})
    # os.environ["MCP_SERVERS"]（auto_sync_mcp_token 启动时写入的 Electron 同步快照）
    # 在 pydantic 里优先级高于 .env——不同步的话 .env 写了也被启动快照盖住：
    # 开关翻转"看起来无效" + _mcp_config_signature 缓存指纹不变（失效机制被致盲）
    os.environ["MCP_SERVERS"] = json.dumps(cleaned, ensure_ascii=False)
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    from crawagent.graph.skills import reset_mcp_cache
    from crawagent.web.state import reset_agent_cache

    reset_mcp_cache()
    reset_agent_cache()

    try:
        raw = json.loads(get_settings().mcp_servers)
    except json.JSONDecodeError:
        raw = []
    return {"ok": True, "mcp_servers": _mask_mcp_headers(raw)}


@router.post("/api/mcp/servers/save")
async def save_mcp_servers_route(payload: dict = Body(...)) -> dict[str, Any]:
    """整表保存 MCP server 列表（设置页生态面板：开关/增删/编辑）。

    - 含 *** 的 header 值视为脱敏掩码，还原为 .env 里的原值
    - 保存后清 settings/MCP/Agent 三层缓存，并在后台预热一次握手
      （stdio server 冷启动需数秒，前端保存按钮期间完成）
    """
    res = save_mcp_servers(payload.get("servers"))
    if not res.get("ok"):
        return res

    # 预热握手改 fire-and-forget：stdio 冷启动数秒 + enabled-but-down 等连接超时，
    # await 会把响应卡住——前端看不到开关翻转以为没点上（012 后踩）。工具数由前端
    # 保存成功后延迟拉一次 /api/ecosystem 补上。
    async def _warmup() -> None:
        try:
            from crawagent.graph.skills import get_mcp_tools

            await asyncio.to_thread(get_mcp_tools)
        except Exception:
            pass

    asyncio.create_task(_warmup())

    from crawagent.graph.skills import mcp_servers_status
    res["mcp_status"] = mcp_servers_status()
    return res


