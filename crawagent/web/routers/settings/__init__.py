"""settings router 包 —— 设置 / 模型 / MCP / 生态 / 背景图 API。

从单文件 routers/settings.py 拆分为包（027），server.py 的
    from crawagent.web.routers import settings as settings_router
    app.include_router(settings_router.router)
仍可用（router 在此聚合）。
"""
from fastapi import APIRouter

from .core import router as core_router, ENV_FILE, _save_env_updates, _mask_key, _settings_snapshot
from .mcp import router as mcp_router, save_mcp_servers, _mask_mcp_headers, _unmask_mcp_headers
from .ecosystem import router as ecosystem_router
from .background import router as background_router

router = APIRouter()
router.include_router(core_router)
router.include_router(mcp_router)
router.include_router(ecosystem_router)
router.include_router(background_router)

# Re-export for test monkeypatch compatibility:
# tests do monkeypatch.setattr(settings_router, "ENV_FILE", ...) and "get_settings", ...
from crawagent.config.settings import get_settings

__all__ = [
    "router",
    "ENV_FILE",
    "get_settings",
    "save_mcp_servers",
    "_mask_mcp_headers",
    "_unmask_mcp_headers",
    "_mask_key",
    "_settings_snapshot",
    "_save_env_updates",
]
