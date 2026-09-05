"""CrawAgent WebUI — FastAPI + WebSocket 服务

启动方式：
    uv run --extra api python -m crawagent.web.server
    # 或已 sync 过 api 依赖： uv run python -m crawagent.web.server

设计要点：
    - 复用 crawagent.graph.agent.get_agent + SqliteSaver 会话持久化
      （data/sessions.db）。
    - Agent 的同步 stream 生成器跑在线程里，事件经 asyncio.Queue 桥接
      推送到 WebSocket，前端实时看到 工具调用 → 工具结果 → AI 回复 的全过程。
    - 每轮结束推送一条与终端同款的 metrics.status_line() 状态栏。
    - sessions/settings/sites 三组 REST API 拆分在 crawagent/web/routers/ 下，
      共享单例见 crawagent/web/state.py，事件日志见 crawagent/web/event_log.py。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from crawagent.config.settings import get_settings, clear_dead_proxy_env
from crawagent.web.routers import sessions as sessions_router
from crawagent.web.routers import settings as settings_router
from crawagent.web.routers import sites as sites_router
from crawagent.web.state import _active_turns
from crawagent.web.turn_engine import _stream_turn


# Vue 工程（项目根目录 web/）的构建产物
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

app = FastAPI(title="CrawAgent WebUI")

# ---- 启动时清除不可达代理，避免 HTTP 请求走死代理 ----
clear_dead_proxy_env()


def _startup_prune():
    """启动时后台线程扫描 logs/output/downloads，按 retention days 清理过期文件。

    不阻塞启动线程；失败只打 print，不影响服务可用性。
    """
    try:
        from crawagent.config.settings import get_settings
        _s = get_settings()
        now = time.time()
        tasks = [
            ("logs", _s.log_dir, _s.logs_retention_days),
            ("output", _s.output_dir, _s.output_retention_days),
            ("downloads", _s.downloads_dir, _s.downloads_retention_days),
        ]
        total_freed = 0
        total_removed = 0
        for label, root, days in tasks:
            if days is None or not root.exists():
                continue
            cutoff = now - days * 86400
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                try:
                    mtime = p.stat().st_mtime
                    if mtime < cutoff:
                        total_freed += p.stat().st_size
                        p.unlink()
                        total_removed += 1
                except OSError:
                    continue
        # 顺带清掉砍空了的子目录（不含根目录本身）
        for label, root, _ in tasks:
            if not root.exists():
                continue
            for d in sorted((q for q in root.rglob("*") if q.is_dir()), reverse=True):
                try:
                    d.rmdir()
                except OSError:
                    pass
        if total_removed:
            print(f"[prune] 启动清理: 删 {total_removed} 个文件, "
                  f"释放 {total_freed / 1024 / 1024:.1f} MB")
    except Exception as e:
        print(f"[prune] 启动清理异常（非致命）: {e}")


threading.Thread(target=_startup_prune, daemon=True).start()

# ---- REST API 路由注册 ----
app.include_router(sessions_router.router)
app.include_router(settings_router.router)
app.include_router(sites_router.router)


@app.get("/")
async def index() -> FileResponse:
    # index.html 禁用浏览器缓存：它引用带哈希的 assets 文件名，
    # 重新构建后旧文件已不存在，缓存旧 index 会导致 404（动态模块加载失败）
    return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.{ext}")
async def favicon(ext: str) -> FileResponse:
    # 浏览器默认请求 /favicon.ico；同时支持 .png / .svg
    candidates = [WEB_DIST / f"favicon.{ext}", WEB_DIST / "favicon.png", WEB_DIST / "favicon.svg"]
    for path in candidates:
        if path.exists():
            return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})
    return FileResponse(WEB_DIST / "index.html")


@app.websocket("/ws/{session_id}")
async def chat_ws(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    stream_task: asyncio.Task | None = None

    # 重连：如果有正在运行的任务且未被取消，恢复订阅
    if session_id in _active_turns:
        active = _active_turns[session_id]
        if active.get("cancelled"):
            # 任务已被用户取消，不恢复
            _active_turns.pop(session_id, None)
            await ws.send_text(json.dumps({"type": "done"}, ensure_ascii=False))
        else:
            # 先告知前端"任务仍在运行"，让输入框立刻恢复红色停止按钮（刷新后 busy 状态不丢）
            await ws.send_text(json.dumps({"type": "resumed"}, ensure_ascii=False))
            await ws.send_text(json.dumps(
                {"type": "status", "line": "恢复进行中的任务…"}, ensure_ascii=False))
            stream_task = asyncio.create_task(_stream_turn(session_id, "", ws, create=False))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # ---- 停止当前任务 ----
            if payload.get("type") == "stop":
                active = _active_turns.get(session_id)
                if active:
                    active["cancelled"] = True
                if stream_task and not stream_task.done():
                    stream_task.cancel()
                    try:
                        await stream_task
                    except (asyncio.CancelledError, Exception):
                        pass
                stream_task = None
                await ws.send_text(json.dumps({"type": "done"}, ensure_ascii=False))
                continue

            if payload.get("type") != "message":
                continue
            text = str(payload.get("content", "")).strip()
            if not text:
                continue
            model = str(payload.get("model") or "").strip() or None

            # 检查是否有正在运行的任务
            active = _active_turns.get(session_id)
            if active:
                if active.get("cancelled"):
                    # 已取消但旧 _run_turn 还没退出
                    fut = active.get("future")
                    if fut and not fut.done():
                        await ws.send_text(json.dumps(
                            {"type": "error", "message": "正在停止上一任务，请稍候…"}, ensure_ascii=False))
                        continue
                    # 旧任务已退出，清理
                    _active_turns.pop(session_id, None)
                else:
                    await ws.send_text(json.dumps(
                        {"type": "error", "message": "上一任务仍在爬行中，请稍候…"}, ensure_ascii=False))
                    continue

            stream_task = asyncio.create_task(_stream_turn(session_id, text, ws, model=model))

    except WebSocketDisconnect:
        pass
    finally:
        if stream_task and not stream_task.done():
            stream_task.cancel()


def _silence_proactor_reset_noise() -> None:
    """静音 Windows ProactorEventLoop 的 ConnectionResetError(10054) 刷屏。

    浏览器刷新/WS 断开时对端强制关连接，_call_connection_lost 里的
    sock.shutdown 抛 ConnectionResetError，asyncio 把整段栈打到终端——
    属于正常断连噪音而非故障，这里捕获吞掉（仅 win32）。
    """
    import asyncio
    import sys

    if sys.platform != "win32":
        return
    try:
        import asyncio.proactor_events as _pe

        _orig = _pe._ProactorBasePipeTransport._call_connection_lost

        def _quiet(self, exc=None):
            try:
                return _orig(self, exc)
            except ConnectionResetError:
                pass

        _pe._ProactorBasePipeTransport._call_connection_lost = _quiet
    except Exception:
        pass  # 静音失败不影响功能


def _open_browser_later(url: str, delay: float = 2.0) -> None:
    """服务开始监听后自动打开浏览器（CRAWAGENT_NO_OPEN=1 可禁用）。"""
    import os
    import threading
    import webbrowser

    if os.environ.get("CRAWAGENT_NO_OPEN", "").strip().lower() in ("1", "true", "yes"):
        return

    def _open() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass  # 无 GUI 环境（Docker/服务器）打开失败不影响服务

    threading.Timer(delay, _open).start()


def main() -> None:
    import os
    import uvicorn

    from fastapi.staticfiles import StaticFiles

    _silence_proactor_reset_noise()

    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    # Vue 构建产物的静态资源目录（存在才挂载，挂载晚于 API/WS 路由注册）
    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    # host/port 可通过环境变量覆盖：Docker 里设 CRAWAGENT_HOST=0.0.0.0，
    # 本地开发默认 127.0.0.1（更安全，只本机可访问）。
    host = os.environ.get("CRAWAGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("CRAWAGENT_PORT", "8006"))
    # 本机启动时自动打开 WebUI；绑定 0.0.0.0 视为服务器/容器部署，不开
    if host not in ("0.0.0.0", "::"):
        _open_browser_later(f"http://127.0.0.1:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
