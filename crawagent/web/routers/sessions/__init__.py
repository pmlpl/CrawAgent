"""sessions router 包 —— 会话列表 / 历史 / 删除 / 归档 / 指标 API。

从单文件 routers/sessions.py 拆分为包（027），server.py 的
    from crawagent.web.routers import sessions as sessions_router
    app.include_router(sessions_router.router)
仍可用（router 在此聚合）。
"""
from fastapi import APIRouter

from .history import router as history_router, _ARCHIVE_SUMMARY_PROMPT, _reconstruct_status
from .archive import router as archive_router, _archive_session_sync, _background_summarize
from .crud import router as crud_router, _delete_session_sync

router = APIRouter()
router.include_router(history_router)
router.include_router(archive_router)
router.include_router(crud_router)

__all__ = [
    "router",
    "_ARCHIVE_SUMMARY_PROMPT",
    "_reconstruct_status",
    "_archive_session_sync",
    "_delete_session_sync",
    "_background_summarize",
]
