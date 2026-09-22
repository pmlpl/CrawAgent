"""通用工具进度上报模块 — 替代 video_finder 私有的 PROGRESS_INDEX。

设计目标：
1. 任何长耗时工具（browse_and_crawl / run_custom_script / download_* / video_site_expert …）
   都能通过 report_progress() 在执行过程中汇报里程碑，server 心跳统一消费推前端。
2. 工具本身不需要写进度代码也能获得"运行中… 已耗时 Xs"的自动心跳
   （consume_new 里检测超过阈值且无新里程碑时自动注入）。
3. 零循环依赖：本模块只依赖标准库，不 import graph / web / 其他 tools。

使用方式（工具内部，可选）：
    from crawagent.tools.progress import report_progress
    def my_long_tool(url):
        report_progress("正在连接服务器…")
        ...
        report_progress("解析页面结构…")
        ...

使用方式（工具包装，agent.py 自动完成）：
    from crawagent.tools.progress import with_progress
    wrapped_tool = with_progress(base_tool)  # 自动 start / finish

server 心跳：
    from crawagent.tools.progress import consume_new
    for ev in consume_new():
        ws.send({"type": "progress", **ev})
"""
from __future__ import annotations

import functools
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ToolProgress:
    """一个工具调用实例的进度快照。"""
    tool_name: str
    args: str
    started_at: float
    last_update: float
    done: bool = False
    error: str | None = None
    milestones: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 全局状态（线程安全）
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
# key = progress_id ("tool_name@timestamp_ms")
_INDEX: dict[str, ToolProgress] = {}
# 消费端已读到的 milestones 序号
_CONSUMED: dict[str, int] = {}
# 当前正在执行的工具 id（report_progress 无参时自动挂到它上面）
# LangGraph 单线程顺序执行工具，同一时刻只有一个 active tool
_active_id: str | None = None

# 自动心跳阈值：工具运行超过这么多秒且无新里程碑时，consume_new 自动注入"运行中"
_AUTO_HEARTBEAT_AFTER: float = 4.0
# 两次自动心跳之间的最小间隔（避免每 2s 心跳都刷一条）
_AUTO_HEARTBEAT_INTERVAL: float = 5.0


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def start_tool(tool_name: str, args: str = "") -> str:
    """工具开始执行时调用（由 with_progress 包装器自动调用）。

    Returns:
        progress_id，用于 finish_tool。
    """
    pid = f"{tool_name}@{int(time.time() * 1000)}"
    with _LOCK:
        _INDEX[pid] = ToolProgress(
            tool_name=tool_name,
            args=args,
            started_at=time.time(),
            last_update=time.time(),
        )
        _CONSUMED[pid] = 0
        global _active_id
        _active_id = pid
    return pid


def report_progress(message: str, tool_name: str = "") -> None:
    """工具内部调用：汇报一个里程碑。

    Args:
        message: 一句话描述当前进展（< 80 字最佳）。
        tool_name: 可选。为空时自动挂到当前 active 工具；
                   若当前无 active 工具且提供了 tool_name，则自动创建一个。
    """
    if not message:
        return
    global _active_id
    with _LOCK:
        pid = _active_id
        if not pid and tool_name:
            # 无 active 工具但指定了名称 → 自动创建（兜底，正常不会走到）
            pid = f"{tool_name}@{int(time.time() * 1000)}"
            _INDEX[pid] = ToolProgress(
                tool_name=tool_name, args="",
                started_at=time.time(), last_update=time.time(),
            )
            _CONSUMED[pid] = 0
            _active_id = pid
        if not pid:
            return
        tp = _INDEX.get(pid)
        if tp is None:
            return
        # 避免完全相同的连续重复
        if tp.milestones and tp.milestones[-1] == message:
            return
        tp.milestones.append(message)
        tp.last_update = time.time()


def finish_tool(pid: str, error: str | None = None) -> None:
    """工具执行结束时调用（由 with_progress 包装器自动调用）。"""
    with _LOCK:
        tp = _INDEX.get(pid)
        if tp:
            tp.done = True
            tp.error = error
            tp.last_update = time.time()
        global _active_id
        if _active_id == pid:
            _active_id = None


def consume_new() -> list[dict[str, Any]]:
    """server 心跳调用：返回自上次消费后新增的进度事件列表。

    每个事件的结构与 video_finder 旧版兼容：
        {"run_id", "query"(=tool_name), "lines", "done", "error",
         "elapsed", "total_milestones"}

    自动心跳：若某工具运行超过 _AUTO_HEARTBEAT_AFTER 秒且距上次里程碑
    超过 _AUTO_HEARTBEAT_INTERVAL 秒，则自动注入一条"运行中… 已耗时 Xs"，
    让没有内部 report_progress 的工具也能在前端看到进展。
    """
    events: list[dict[str, Any]] = []
    now = time.time()
    with _LOCK:
        for pid, tp in list(_INDEX.items()):
            # ---- 自动耗时心跳 ----
            if (not tp.done
                    and now - tp.started_at > _AUTO_HEARTBEAT_AFTER
                    and now - tp.last_update > _AUTO_HEARTBEAT_INTERVAL):
                elapsed = round(now - tp.started_at, 1)
                heartbeat_msg = f"运行中… 已耗时 {elapsed}s"
                # 不重复注入相同内容
                if not tp.milestones or tp.milestones[-1] != heartbeat_msg:
                    tp.milestones.append(heartbeat_msg)
                    tp.last_update = now

            # ---- 消费新增里程碑 ----
            last = _CONSUMED.get(pid, 0)
            new_lines = tp.milestones[last:]
            _CONSUMED[pid] = len(tp.milestones)
            if new_lines or tp.done:
                events.append({
                    "run_id": pid,
                    "query": tp.tool_name,
                    "lines": new_lines,
                    "done": tp.done,
                    "error": tp.error,
                    "elapsed": round(now - tp.started_at, 1),
                    "total_milestones": len(tp.milestones),
                })

        # ---- 清理已完成超过 30s 的条目（防内存泄漏） ----
        for pid in list(_INDEX.keys()):
            tp = _INDEX[pid]
            if tp.done and now - tp.last_update > 30:
                _INDEX.pop(pid, None)
                _CONSUMED.pop(pid, None)

    return events


# ---------------------------------------------------------------------------
# 轮次事件出口 — ask_user 等工具需要把结构化事件推进当前轮次的事件日志
# （事件日志重连会重放，保证刷新页面后选择按钮仍可见）。turn_engine._run_turn
# 启动时 set_turn_emitter()，finally 时 clear；工具线程与轮次同线程，
# ctx_session_id（graph.agent）可直接读出当前会话。
# ---------------------------------------------------------------------------

_turn_emitters: dict[str, Any] = {}


def set_turn_emitter(session_id: str, fn) -> None:
    """登记 ``session_id`` 的轮次事件 emitter（turn_engine 启动时调）。

    Args:
        session_id: 会话 ID。
        fn: 接受 ``event_dict`` 的回调，由 ``emit_turn_event`` 调用。
    """
    with _LOCK:
        _turn_emitters[session_id] = fn


def clear_turn_emitter(session_id: str) -> None:
    """注销 ``session_id`` 的轮次事件 emitter（turn_engine 退出时 finally 调）。

    Args:
        session_id: 会话 ID；不存在时静默 no-op。
    """
    with _LOCK:
        _turn_emitters.pop(session_id, None)


def emit_turn_event(event: dict[str, Any]) -> bool:
    """把结构化 UI 事件推进当前轮次事件日志（可重放）。无活动轮次时静默失败。

    与 report_progress 的区别：progress 是一次性轨迹行（不重放），
    这里是 UI 状态事件（ask 选择题），必须在页面刷新后依然可见。
    """
    try:
        from crawagent.graph.agent import ctx_session_id
        sid = ctx_session_id.get(None)
    except Exception:
        return False
    if not sid:
        return False
    with _LOCK:
        fn = _turn_emitters.get(sid)
    if fn is None:
        return False
    try:
        fn(event)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 工具包装器 — agent.py 用它给长耗时工具套上自动 start/finish
# ---------------------------------------------------------------------------

def with_progress(tool: Any) -> Any:
    """给一个 LangChain BaseTool 套上进度监控（自动 start_tool / finish_tool）。

    工具内部可自由调用 report_progress() 汇报细粒度里程碑；
    不调用也能获得自动耗时心跳。

    Args:
        tool: LangChain StructuredTool / BaseTool 实例。

    Returns:
        同一个 tool 实例（func 已被替换为带监控的版本）。
    """
    original_func = getattr(tool, "func", None)
    if original_func is None:
        return tool  # 不是 StructuredTool，无法包装
    tool_name = getattr(tool, "name", original_func.__name__)

    @functools.wraps(original_func)
    def monitored_func(*args: Any, **kwargs: Any) -> Any:
        """``@monitor`` 装饰后的工具函数：跑前推 trajectory + start/end 进度事件。

        Args:
            *args: 透传给被装饰工具的位置参数。
            **kwargs: 透传给被装饰工具的关键字参数。

        Returns:
            被装饰工具的返回值。
        """
        # 拼接参数摘要（给前端轨迹显示用，截断防过长）
        try:
            if kwargs:
                args_str = json.dumps(kwargs, ensure_ascii=False, default=str)[:120]
            elif args:
                args_str = json.dumps(args, ensure_ascii=False, default=str)[:120]
            else:
                args_str = ""
        except Exception:
            args_str = ""

        pid = start_tool(tool_name, args_str)
        try:
            result = original_func(*args, **kwargs)
            finish_tool(pid)
            return result
        except Exception as exc:
            finish_tool(pid, error=str(exc))
            raise

    tool.func = monitored_func
    return tool


# 潜在长耗时工具名单（agent.py 据此决定哪些工具套 with_progress）
# 判断标准：涉及网络请求 / 浏览器渲染 / 子进程 / 子 Agent / 多文件下载
LONG_RUNNING_TOOLS: set[str] = {
    "browse_and_crawl",       # Playwright 浏览器渲染
    "run_custom_script",       # 任意子进程
    "download_social_media",   # yt-dlp 视频下载
    "download_images",         # 多图下载
    "video_site_expert",       # 子 Agent（多工具串联）
    "crawl_webpage",           # 网络请求
    "extract_social_media",    # 网络请求
    "extract_wallpaper_list",  # 网络请求
    "wallpaper_detail",        # 网络请求
    "list_weread_chapters",    # 网络请求
    "get_weread_chapter",      # 网络请求
    "get_proxy",               # TCP 健康检查 + HTTP 探活
    "login_site",              # Playwright 登录流程
    "check_login_status",      # 带 Cookie 请求页面
    # ---- Android 逆向（frida hook / adb 交互）----
    "list_adb_devices",        # adb 设备探测
    "install_apk",             # adb install 文件传输
    "push_file",               # adb push 文件传输
    "frida_hook_function",     # frida-trace 长时挂载
    "frida_dump_so",           # frida 内联 JS dump
    "frida_bypass_ssl_pinning",# frida 挂载绕过脚本
}
