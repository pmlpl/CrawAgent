"""站点档案相关 API（Site Profile）。

站点首次抓取后保存策略，后续复用跳过分析阶段。
存储在 data/sites.json，与 Agent 的 list_site_profiles / save_site_profile 工具共享同一文件。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body

from crawagent.tools.site_profile_tool import list_sites, upsert_site, delete_site

router = APIRouter()


@router.get("/api/sites")
async def list_sites_route() -> dict[str, Any]:
    """列出全部站点档案，供前端站点管理页展示"""
    return {"sites": await asyncio.to_thread(list_sites)}


@router.post("/api/sites")
async def create_site_route(payload: dict = Body(...)) -> dict[str, Any]:
    """创建或更新一个站点档案（以 origin 为唯一键），可携带站点级 Cookie。

    cookies 为 None 时不修改，传空串清除已存 Cookie。
    """
    origin = payload.get("origin")
    if not isinstance(origin, str) or not origin.strip():
        return {"ok": False, "error": "origin is required"}
    cookies = payload.get("cookies")
    if not isinstance(cookies, str):
        cookies = None
    script = payload.get("script")
    if not isinstance(script, str):
        script = None
    try:
        rec = await asyncio.to_thread(
            upsert_site,
            origin.strip(),
            payload.get("title", ""),
            payload.get("strategy", ""),
            payload.get("notes", ""),
            cookies,
            script,
        )
        return {"ok": True, "site": rec}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.delete("/api/sites/{origin:path}")
async def delete_site_route(origin: str) -> dict[str, Any]:
    """删除一个站点档案"""
    deleted = await asyncio.to_thread(delete_site, origin)
    return {"ok": deleted, "id": origin}
