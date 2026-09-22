"""聊天背景图 API（ADR-0001）：存服务端 data/，任何 origin / 浏览器共享。

从 routers/settings.py 拆出（027），router 独立但 URL 路径不变。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

# ---------------------------------------------------------------------------
# 聊天背景图（ADR-0001）：存服务端 data/，任何 origin / 浏览器共享同一份。
# localStorage 按 origin 隔离（localhost:8006  != 127.0.0.1:8006）且 ~5MB 配额易爆，已弃用。
# ---------------------------------------------------------------------------
_DATA_DIR = Path(__file__).resolve().parents[4] / "data"
_BG_IMAGE = _DATA_DIR / "background.jpg"
_BG_META = _DATA_DIR / "background.json"
_BG_PREFIX = "data:image/jpeg;base64,"
_MAX_BG_BYTES = 8 * 1024 * 1024  # 前端统一 canvas 压缩后 ~200-400KB，此为防御上限


def _bg_meta() -> dict:
    try:
        data = json.loads(_BG_META.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _bg_state() -> dict[str, Any]:
    return {
        "image": f"/api/background/image?t={_BG_IMAGE.stat().st_mtime_ns}" if _BG_IMAGE.exists() else None,
        "opacity": _bg_meta().get("opacity", 60),
    }


@router.get("/api/background")
async def get_background_route() -> dict[str, Any]:
    """背景图当前状态（image 为带 mtime 防缓存的相对 URL，无背景时为 null）"""
    return _bg_state()


@router.get("/api/background/image")
async def get_background_image_route() -> FileResponse:
    if not _BG_IMAGE.exists():
        raise HTTPException(status_code=404, detail="no background image")
    return FileResponse(_BG_IMAGE, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post("/api/background")
async def save_background_route(payload: dict = Body(...)) -> dict[str, Any]:
    """保存背景图：data_url 为前端 canvas 压缩后的 JPEG dataURL，落盘 data/background.jpg"""
    data_url = str(payload.get("data_url") or "")
    if not data_url.startswith(_BG_PREFIX):
        return {"ok": False, "error": "data_url 必须是 data:image/jpeg;base64,…（前端统一压缩为 JPEG）"}
    try:
        raw = base64.b64decode(data_url[len(_BG_PREFIX):], validate=False)
    except ValueError as e:
        return {"ok": False, "error": f"base64 解码失败: {e}"}
    if not raw:
        return {"ok": False, "error": "图片内容为空"}
    if len(raw) > _MAX_BG_BYTES:
        return {"ok": False, "error": f"图片解压后 {len(raw) // 1024 // 1024}MB 超出上限，请换小图"}
    _DATA_DIR.mkdir(exist_ok=True)
    _BG_IMAGE.write_bytes(raw)
    return {"ok": True, **_bg_state()}


@router.post("/api/background/opacity")
async def save_background_opacity_route(payload: dict = Body(...)) -> dict[str, Any]:
    """保存遮罩浓度（0-95）。前端拖动滑块防抖后调用，独立于背景图存储"""
    try:
        opacity = int(payload.get("opacity"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "opacity 必须是整数"}
    if not 0 <= opacity <= 95:
        return {"ok": False, "error": "opacity 需在 0-95 之间"}
    _DATA_DIR.mkdir(exist_ok=True)
    meta = _bg_meta()
    meta["opacity"] = opacity
    _BG_META.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "opacity": opacity}


@router.delete("/api/background")
async def clear_background_route() -> dict[str, Any]:
    """清除背景图与遮罩浓度设置"""
    _BG_IMAGE.unlink(missing_ok=True)
    _BG_META.unlink(missing_ok=True)
    return {"ok": True}
