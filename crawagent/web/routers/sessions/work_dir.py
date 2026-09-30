"""会话工作文件夹绑定 API（变更 035 —— 034 交互重构）。

POST /api/sessions/{session_id}/work-dir，body {path: str}：
    path 非空 → set_work_dir（034 已有：abspath 规范化 + makedirs 自动创建 +
    upsert——meta.db 还没有该会话记录的新会话也能直接建，无需等首条消息）；
    path 空   → 清除该会话 work_dir，退回 014 默认落点。

「选择项目」按钮选完立即调本端点落库；中途更换项目也走这里（旧文件不动，
后续产物落新文件夹）。工具层零改动——current_work_dir() 每次现读，绑定/
更换下一轮工具调用即生效。

返回：
    {ok: true, work_dir: "<规范化后的绝对路径>"}   绑定/更换/清除成功
    {ok: false, error: "<人话错误>"}                mkdir 失败等异常

GET /api/sessions/work-dirs/history：最近用过的 work_dir（去重 + 按最近使用
排序，最多 10 个），供提示条「切换项目」菜单一键重选（变更 037）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from crawagent.storage.meta_store import get_work_dir, list_work_dir_history, set_work_dir

router = APIRouter()


@router.get("/api/sessions/work-dirs/history")
async def get_work_dir_history() -> dict:
    """最近用过的 work_dir（去重 + 按最近使用排序，最多 10 个，变更 037）。

    供聊天页提示条「切换项目」菜单展示历史项。实现委托 meta_store.list_work_dir_history
    （按 work_dir_ts 时间戳排序——rowid 在 conflict-update 时不变，无法反映
    「同会话换项目」后的真实时序）。
    """
    return {"work_dirs": list_work_dir_history()}


@router.post("/api/sessions/{session_id}/work-dir")
async def set_session_work_dir(session_id: str, body: Any = Body(default={})) -> dict:
    """绑定/更换/清除会话工作文件夹；返回规范化路径或人话错误。"""
    path = ""
    if isinstance(body, dict):
        path = str(body.get("path") or "").strip()
    if not session_id.strip():
        return {"ok": False, "error": "缺少会话 ID"}
    try:
        work_dir = set_work_dir(session_id, path)
        return {"ok": True, "work_dir": work_dir}
    except Exception as e:
        return {"ok": False, "error": f"文件夹{'创建' if path else '清除'}失败：{e}"}


@router.get("/api/sessions/{session_id}/work-dir")
async def get_session_work_dir(session_id: str) -> dict:
    """读当前绑定（前端排查用；常规回读走 /api/history 的 work_dir 字段）。"""
    return {"ok": True, "work_dir": get_work_dir(session_id)}
