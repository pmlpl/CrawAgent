"""本地文件系统辅助 API —— 原生文件夹选择框（变更 034）。

POST /api/fs/pick-folder：后端起 daemon 线程跑 tkinter filedialog.askdirectory()，
弹出系统原生"浏览文件夹"对话框。服务进程就在用户本机（本地单机部署），这是
浏览器沙箱拿不到绝对路径时唯一能给出完整路径的方案。

硬约束：绝不能在 uvicorn 事件循环线程弹 tkinter（mainloop 会卡死整个服务）——
每次新建 tk.Tk() → withdraw() → topmost → askdirectory → destroy()，全程在
daemon 线程里跑，端点阻塞等待至用户选完。

返回：
    {ok: true, path: "<绝对路径>"}          选中
    {ok: true, canceled: true, path: ""}    用户取消
    {ok: false, error: "..."}               tkinter 异常（无显示环境等）→ 前端回退手输
    {ok: false, error: "选择框已打开"}       防抖：已有弹窗未关时二次请求
"""
from __future__ import annotations

import asyncio
import threading

from fastapi import APIRouter

router = APIRouter()

# 进程级防抖标志：Tk 对话框同时只能有一个（多开互相抢焦点且行为未定义）。
# 检查与置位都在事件循环线程内完成，单线程语义下无竞态。
_picking = False


def _pick_folder_sync() -> dict:
    """在工作线程里弹原生对话框并阻塞至选完。绝不抛异常，错误折进返回值。"""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askdirectory(title="选择本会话的工作文件夹")
        finally:
            root.destroy()
        path = path or ""
        if not path:
            return {"ok": True, "canceled": True, "path": ""}
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e) or type(e).__name__}


@router.post("/api/fs/pick-folder")
async def pick_folder() -> dict:
    global _picking
    if _picking:
        return {"ok": False, "error": "选择框已打开"}
    _picking = True
    try:
        # daemon 线程跑 tkinter；经 concurrent Future 桥接回事件循环等待
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        holder: dict = {}

        def _run() -> None:
            try:
                holder["result"] = _pick_folder_sync()
            except Exception as e:  # 兜底：_pick_folder_sync 理论上不抛
                holder["result"] = {"ok": False, "error": str(e)}
            loop.call_soon_threadsafe(_settle)

        def _settle() -> None:
            if not fut.done():
                fut.set_result(holder.get("result") or {"ok": False, "error": "no result"})

        loop = asyncio.get_running_loop()
        threading.Thread(target=_run, daemon=True, name="pick-folder-dialog").start()
        return await fut
    finally:
        _picking = False
