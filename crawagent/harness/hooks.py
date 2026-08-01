"""pi Agent Harness - Hook 系统

本模块实现了 Agent 行为的拦截/修改点，参考 pi Agent Harness 的 hooks 设计。

Hook 是 Agent 运行过程中的回调机制，允许在特定事件发生时插入自定义逻辑：
- before_run: Agent 运行开始前
- before_tool: 工具调用前
- after_tool: 工具调用后
- transform_context: 上下文变换
- before_request: 请求发出前
- after_response: 响应返回后
- before_compaction: 压缩前
- before_run_end: Agent 运行结束前

每个 hook callback 接收当前上下文字典，返回修改后的上下文或 None（不修改）。
多个 callback 按优先级顺序依次执行，前一个的输出作为后一个的输入。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from loguru import logger

from crawagent.harness.types import HookEvent

# ---------------------------------------------------------------------------
# HookCallback 类型别名
# ---------------------------------------------------------------------------

HookCallback = Callable[[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]
"""Hook 回调函数类型。

输入：当前上下文（包含 event 类型、相关数据）
输出：修改后的上下文，或 None（不修改）
"""


# ---------------------------------------------------------------------------
# CrawlHooks 类
# ---------------------------------------------------------------------------

class CrawlHooks:
    """pi 风格的 Agent Hook 系统。

    支持在 Agent 运行的各个阶段注册异步回调，按优先级顺序执行。
    """

    def __init__(self) -> None:
        self._hooks: Dict[HookEvent, List[Tuple[int, HookCallback]]] = {}

    def on(self, event: HookEvent, callback: HookCallback, priority: int = 0) -> None:
        """注册 hook callback。

        Args:
            event: hook 事件类型
            callback: 异步回调函数
            priority: 优先级，数字小先执行（默认 0）
        """
        if event not in self._hooks:
            self._hooks[event] = []
        self._hooks[event].append((priority, callback))
        # 按 priority 升序排列，数字小优先执行
        self._hooks[event].sort(key=lambda item: item[0])

    def off(self, event: HookEvent, callback: HookCallback = None) -> None:
        """移除 hook callback。

        Args:
            event: hook 事件类型
            callback: 要移除的回调函数。如果为 None，移除该 event 的所有 callback。
        """
        if event not in self._hooks:
            return
        if callback is None:
            del self._hooks[event]
        else:
            self._hooks[event] = [
                (p, cb) for p, cb in self._hooks[event] if cb is not callback
            ]
            if not self._hooks[event]:
                del self._hooks[event]

    async def fire(self, event: HookEvent, context: Dict[str, Any]) -> Dict[str, Any]:
        """触发 hook 事件。

        按优先级顺序执行所有注册的 callback。
        每个 callback 接收上一个 callback 返回的 context（或原始 context）。
        如果 callback 返回 None，保持当前 context 不变。
        如果 callback 返回 Dict，用返回值更新 context。

        Args:
            event: hook 事件类型
            context: 当前上下文

        Returns:
            最终的 context。
        """
        current = context
        for _priority, callback in self._hooks.get(event, []):
            result = await callback(current)
            if result is not None:
                current.update(result)
        return current

    def has_hooks(self, event: HookEvent) -> bool:
        """检查某事件是否有注册的 hook。

        Args:
            event: hook 事件类型

        Returns:
            是否有注册的 callback。
        """
        return bool(self._hooks.get(event))

    def list_hooks(self) -> Dict[HookEvent, int]:
        """列出各事件的 hook 数量。

        Returns:
            事件到 hook 数量的映射。
        """
        return {event: len(callbacks) for event, callbacks in self._hooks.items()}


# ---------------------------------------------------------------------------
# AntiBot Hook 处理器（P1-D 实现）
# ---------------------------------------------------------------------------

class AntiBotHookHandler:
    """AntiBot Hook 处理器：检测反爬挑战并自动注入升级策略

    工作流程：
    1. after_tool: crawl 工具执行后，检查结果中的反爬迹象（403/429/Cloudflare）
    2. before_tool: 下次 crawl 执行前，根据上次挑战注入升级策略（use_browser/增加超时）
    3. 200 响应清除挑战状态
    """

    def __init__(self) -> None:
        self._last_challenge: Optional[Dict[str, Any]] = None

    async def before_tool(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """before_tool: 如果有上次的反爬挑战，注入升级策略

        支持的工具（LLM 只能看到 supervisor，但内部 crawl 也可被注入）：
        - crawl: 注入 use_browser / timeout 到 crawl 参数
        - supervisor: 注入 use_browser 到 supervisor 的 url 参数（由内部 crawl 继承）

        context 字段:
        - tool_calls: List[Dict] — LLM 生成的工具调用
        """
        if not self._last_challenge:
            return None

        tool_calls = context.get("tool_calls", [])
        modified = False

        for tc in tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "")
            # 只处理 crawl 和 supervisor（LLM 唯一可见入口）
            if tool_name not in ("crawl", "supervisor"):
                continue

            args = func.get("arguments", {})
            if isinstance(args, str):
                import json
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            challenge = self._last_challenge
            category = challenge.get("category", "")

            if category in ("cloudflare", "waf", "forbidden"):
                # Cloudflare/WAF/403 → 升级到浏览器模式
                if not args.get("use_browser"):
                    args["use_browser"] = True
                    modified = True
                    logger.info(
                        f"[AntiBot] 检测到 {category}，注入 use_browser=True (tool={tool_name})"
                    )
            elif category == "rate_limit":
                # 限流 → 增加超时
                current_timeout = args.get("timeout", 30)
                if current_timeout < 60:
                    args["timeout"] = 60
                    modified = True
                    logger.info("[AntiBot] 检测到限流，增加 timeout=60")

            if modified:
                func["arguments"] = args

        if modified:
            return {"tool_calls": tool_calls}
        return None

    async def after_tool(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """after_tool: 检查工具结果中的反爬迹象，存储挑战供下次使用

        兼容两种结果格式：
        - crawl 结果: status, content, error
        - supervisor 结果: status_code, content, stages.crawl.status

        context 字段:
        - tool_results: List[Dict] — 工具执行结果
        """
        tool_results = context.get("tool_results", [])

        for result in tool_results:
            # 兼容 supervisor 返回的 status_code 字段
            status = result.get("status", result.get("status_code", 0))
            # supervisor 返回的 content 是中文消息，需同时检查 stages
            content = str(result.get("content", "")).lower()
            error = result.get("error", False)

            # 如果是 supervisor 结果，从 stages.crawl 提取真实 HTTP 状态
            # 注意：supervisor 的 status_code 是工具执行状态（200=成功/500=失败），
            # 不是 HTTP 状态。真实 HTTP 状态在 stages.crawl.status，优先使用它做反爬检测。
            stages = result.get("stages")
            if isinstance(stages, dict):
                crawl_stage = stages.get("crawl", {})
                if isinstance(crawl_stage, dict):
                    crawl_status = crawl_stage.get("status", 0)
                    if crawl_status:
                        status = crawl_status

            # 成功响应：清除挑战（HTTP 2xx 视为成功，或 supervisor 的 STATUS_OK=200）
            if not error and 200 <= status < 400:
                if self._last_challenge:
                    logger.info("[AntiBot] 请求成功，清除反爬挑战状态")
                self._last_challenge = None
                continue

            # 检测各类反爬
            if status == 403:
                self._last_challenge = {
                    "category": "forbidden",
                    "status": status,
                    "message": "HTTP 403 禁止访问",
                }
                logger.warning(f"[AntiBot] 检测到 403 禁止访问")
            elif status == 429:
                self._last_challenge = {
                    "category": "rate_limit",
                    "status": status,
                    "message": "HTTP 429 限流",
                }
                logger.warning(f"[AntiBot] 检测到 429 限流")
            elif "cloudflare" in content or "cf-ray" in content or "challenge-platform" in content:
                self._last_challenge = {
                    "category": "cloudflare",
                    "status": status,
                    "message": "检测到 Cloudflare 防护",
                }
                logger.warning(f"[AntiBot] 检测到 Cloudflare 防护")
            elif any(waf in content for waf in ("akamai", "incapsula", "sucuri", "imperva")):
                self._last_challenge = {
                    "category": "waf",
                    "status": status,
                    "message": "检测到 WAF 防护",
                }
                logger.warning(f"[AntiBot] 检测到 WAF 防护")

        return None

    def reset(self) -> None:
        """重置挑战状态"""
        self._last_challenge = None


def create_antibot_hooks(hooks: CrawlHooks) -> AntiBotHookHandler:
    """创建并注册 AntiBot hooks 到 CrawlHooks 实例

    返回 AntiBotHookHandler 实例（可用于重置状态等）。
    """
    handler = AntiBotHookHandler()
    hooks.on(HookEvent.BEFORE_TOOL, handler.before_tool)
    hooks.on(HookEvent.AFTER_TOOL, handler.after_tool)
    return handler
