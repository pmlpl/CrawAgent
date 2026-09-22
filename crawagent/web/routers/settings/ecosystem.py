"""生态面板 API：技能索引 / MCP server 快照 / 打开目录。

从 routers/settings.py 拆出（027），router 独立但 URL 路径不变。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body

from crawagent.config.settings import get_settings
from .mcp import _mask_mcp_headers

router = APIRouter()

@router.get("/api/ecosystem")
async def get_ecosystem_route() -> dict[str, Any]:
    """生态面板快照：技能索引 / MCP server 列表（脱敏+状态）/ 已装插件。"""
    from crawagent.graph.skills import load_skill_index, mcp_servers_status
    from crawagent.tools.registry import list_plugins

    s = get_settings()
    try:
        raw_servers = json.loads(s.mcp_servers) if s.mcp_servers.strip() else []
    except json.JSONDecodeError:
        raw_servers = []

    skills = []
    for item in load_skill_index():
        dir_norm = str(item["dir"]).replace("\\", "/")
        is_plugin = "/plugins/" in dir_norm and "/skills/" in dir_norm
        skills.append({
            "name": item["name"],
            "description": item["description"],
            "source": "plugin" if is_plugin else "builtin",
        })

    return {
        "skills": skills,
        "skills_dirs": s.skills_dirs,
        "mcp_servers": _mask_mcp_headers(raw_servers),
        "mcp_status": mcp_servers_status(),
        "plugins": list_plugins(),
    }




@router.post("/api/ecosystem/open-folder")
async def open_ecosystem_folder_route(payload: dict = Body(...)) -> dict[str, Any]:
    """在资源管理器中打开 skills/ / plugins/ / 产物 / 下载目录（本地部署，便于用户放文件与查产物）。"""
    folder = str(payload.get("folder") or "").strip()
    if folder in ("output", "downloads"):
        s = get_settings()
        root = Path(s.output_dir if folder == "output" else s.downloads_dir)
    elif folder in ("skills", "plugins"):
        root = Path(__file__).resolve().parents[4] / folder
    else:
        return {"ok": False, "error": "folder 只支持 skills / plugins / output / downloads"}
    root.mkdir(exist_ok=True)
    import os

    os.startfile(str(root)) if hasattr(os, "startfile") else None
    return {"ok": True, "path": str(root)}


# ---------------------------------------------------------------------------
# 聊天背景图（ADR-0001）：存服务端 data/，任何 origin / 浏览器共享同一份。
# localStorage 按 origin 隔离（localhost:8006 ≠ 127.0.0.1:8006）且 ~5MB 配额易爆，已弃用。
# ---------------------------------------------------------------------------

