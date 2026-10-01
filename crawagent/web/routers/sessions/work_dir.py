"""会话工作文件夹绑定 API（变更 035 —— 038 1:1 锁定）。

POST /api/sessions/{session_id}/work-dir，body {path: str}：
    新会话（未绑定）→ set_work_dir 落库（034 已有：abspath 规范化 +
    makedirs 自动创建 + upsert——meta.db 还没有该会话记录的新会话也能直接
    建，无需等首条消息）；同路径重绑幂等放行并刷历史 last_used。
    已绑定会话 → 换绑/清除一律被 meta_store 锁定守卫拒绝（变更 038：
    一个会话只支持一个项目，选错 = 新建会话），抛 WorkDirLockedError。

「选择项目」按钮选完立即调本端点落库；工具层零改动——current_work_dir()
每次现读，绑定下一轮工具调用即生效。

返回：
    {ok: true, work_dir: "<规范化后的绝对路径>"}   绑定/幂等重绑成功
    {ok: false, error: "<人话错误>"}                锁定拒绝或 mkdir 失败

错误分流（038）：WorkDirLockedError（含旧路径 + 新建会话指引的人话文案）
≠ mkdir OSError（文件夹创建失败），两类文案分开返回。

GET /api/sessions/work-dirs/history：最近用过的 work_dir（去重 + 按最近使用
排序，最多 10 个）。变更 038 起返回 [{path, exists}]——一次请求同时拿路径
与磁盘存在性，供未绑定新会话的「历史项目」菜单展示（失效项标灰，点击仍
允许绑定 = 主动重建）；绑定历史 append-only 逻辑（c027be5）保持原样。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body

from crawagent.storage.meta_store import (
    WorkDirLockedError,
    get_work_dir,
    list_work_dir_history,
    set_work_dir,
)

router = APIRouter()


@router.get("/api/sessions/work-dirs/history")
async def get_work_dir_history() -> dict:
    """最近用过的 work_dir（去重 + 按最近使用排序，最多 10 个，变更 037）。

    变更 038：结构升级为 [{path, exists}]，exists 现查磁盘（Path.exists()
    不缓存，文件夹建回来自动恢复）。实现委托 meta_store.list_work_dir_history
    （按 work_dir_ts 时间戳排序——rowid 在 conflict-update 时不变，无法反映
    同路径重开后的真实时序）。
    """
    items = [
        {"path": p, "exists": Path(p).exists()} for p in list_work_dir_history()
    ]
    return {"work_dirs": items}


@router.post("/api/sessions/{session_id}/work-dir")
async def set_session_work_dir(session_id: str, body: Any = Body(default={})) -> dict:
    """绑定/幂等重绑会话工作文件夹；换绑与清除被 1:1 锁定拒绝，返回人话错误。"""
    path = ""
    if isinstance(body, dict):
        path = str(body.get("path") or "").strip()
    if not session_id.strip():
        return {"ok": False, "error": "缺少会话 ID"}
    try:
        work_dir = set_work_dir(session_id, path)
        return {"ok": True, "work_dir": work_dir}
    except WorkDirLockedError as e:
        # 锁定拒绝：message 本身就是人话（含旧路径 + 新建会话指引），原样透传
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"文件夹创建失败：{e}"}


@router.get("/api/sessions/{session_id}/work-dir")
async def get_session_work_dir(session_id: str) -> dict:
    """读当前绑定 + 磁盘存在性（前端失效标注用；常规回读走 /api/history）。

    work_dir 为空时 exists 恒 true（无意义），前端只在 workDir 非空时消费。
    """
    work_dir = get_work_dir(session_id)
    exists = True if not work_dir else Path(work_dir).exists()
    return {"ok": True, "work_dir": work_dir, "exists": exists}
