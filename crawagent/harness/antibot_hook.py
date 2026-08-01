"""AntiBot Hook — before_tool 反爬拦截

在工具调用前检查上次响应是否有反爬迹象，自动注入升级策略：
- Cloudflare/WAF/403 → 注入 use_browser=True
- 429 限流 → 注入 timeout=60
- 验证码 → 标记需要人工介入

与 hooks.py 中的 AntiBotHookHandler 的区别：
- 使用 anti_bot.is_blocked() 三层检测（更精准）
- 支持 escalation_hook 的 AddFeatureError 触发
- 记录挑战历史，避免重复升级
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from crawagent.harness.hooks import CrawlHooks, HookCallback
from crawagent.harness.types import HookEvent


class AntiBotInterceptor:
    """反爬拦截器：before_tool 阶段注入升级策略

    工作流程：
    1. after_tool 检测上次响应的反爬迹象（使用 is_blocked 三层检测）
    2. before_tool 下次工具调用前注入升级参数（use_browser / timeout）
    3. 成功响应（2xx）后清除挑战状态
    """

    def __init__(self) -> None:
        self._last_challenge: Optional[Dict[str, Any]] = None
        self._challenge_history: List[Dict[str, Any]] = []
        self._max_history = 10

    async def before_tool(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """before_tool: 注入升级策略到工具调用参数

        context 字段:
        - tool_calls: List[Dict] — LLM 生成的工具调用
        """
        if not self._last_challenge:
            return None

        tool_calls = context.get("tool_calls", [])
        modified = False
        challenge = self._last_challenge
        action = challenge.get("recommended_action", "")
        category = challenge.get("challenge_type", "")

        for tc in tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "")

            # 只处理 crawl 和 supervisor 工具
            if tool_name not in ("crawl", "supervisor"):
                continue

            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            # 根据挑战类型注入不同策略
            if action in ("use_browser",) or category in ("cloudflare", "js_challenge"):
                if not args.get("use_browser"):
                    args["use_browser"] = True
                    modified = True
                    logger.info(
                        f"[AntiBotInterceptor] 注入 use_browser=True "
                        f"(challenge={category}, tool={tool_name})"
                    )

            elif action in ("use_curl_cffi",) or category in ("waf", "forbidden"):
                if not args.get("use_curl_cffi"):
                    args["use_curl_cffi"] = True
                    modified = True
                    logger.info(
                        f"[AntiBotInterceptor] 注入 use_curl_cffi=True "
                        f"(challenge={category}, tool={tool_name})"
                    )

            elif action == "wait_and_retry" or category == "rate_limit":
                current_timeout = args.get("timeout", 30)
                if current_timeout < 60:
                    args["timeout"] = 60
                    modified = True
                    logger.info(
                        f"[AntiBotInterceptor] 增加超时 timeout=60 "
                        f"(challenge=rate_limit, tool={tool_name})"
                    )

            elif action == "solve_captcha" or category == "captcha":
                # 验证码无法自动处理，标记需要人工介入
                logger.warning(
                    f"[AntiBotInterceptor] 检测到验证码 ({category})，"
                    f"需要人工介入或打码平台"
                )
                # 注入标记，让 LLM 知道遇到验证码
                args["_antibot_captcha"] = True
                modified = True

            if modified:
                func["arguments"] = args

        if modified:
            return {"tool_calls": tool_calls}
        return None

    async def after_tool(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """after_tool: 使用 is_blocked() 三层检测分析工具结果

        context 字段:
        - tool_results: List[Dict] — 工具执行结果
        """
        from crawagent.graph.anti_bot import is_blocked

        tool_results = context.get("tool_results", [])

        for result in tool_results:
            # 兼容多种结果格式
            status = result.get("status", result.get("status_code", 0))
            content = str(result.get("content", ""))
            error = result.get("error", "")
            headers = result.get("headers", {})

            # supervisor 结果：从 stages.crawl 提取真实状态
            stages = result.get("stages")
            if isinstance(stages, dict):
                crawl_stage = stages.get("crawl", {})
                if isinstance(crawl_stage, dict):
                    crawl_status = crawl_stage.get("status", 0)
                    if crawl_status:
                        status = crawl_status
                    crawl_html = crawl_stage.get("html", "")
                    if crawl_html:
                        content = crawl_html

            # 成功响应：清除挑战
            if not error and 200 <= status < 400:
                if self._last_challenge:
                    logger.info("[AntiBotInterceptor] 请求成功，清除反爬挑战状态")
                    self._record_challenge_resolved()
                self._last_challenge = None
                continue

            # 三层检测
            block_result = is_blocked(
                status_code=status,
                headers=headers,
                body=content,
                error=str(error),
            )

            if block_result.blocked:
                self._last_challenge = {
                    "challenge_type": block_result.challenge_type,
                    "confidence": block_result.confidence,
                    "evidence": block_result.evidence,
                    "recommended_action": block_result.recommended_action,
                    "status_code": status,
                    "timestamp": time.time(),
                }
                logger.warning(
                    f"[AntiBotInterceptor] 检测到反爬: "
                    f"type={block_result.challenge_type}, "
                    f"confidence={block_result.confidence:.2f}, "
                    f"action={block_result.recommended_action}, "
                    f"evidence={block_result.evidence}"
                )

        return None

    def _record_challenge_resolved(self) -> None:
        """记录挑战被解决"""
        if self._last_challenge:
            entry = {**self._last_challenge, "resolved": True}
            self._challenge_history.append(entry)
            if len(self._challenge_history) > self._max_history:
                self._challenge_history = self._challenge_history[-self._max_history:]

    def reset(self) -> None:
        """重置挑战状态"""
        self._last_challenge = None

    @property
    def has_active_challenge(self) -> bool:
        return self._last_challenge is not None

    @property
    def current_challenge(self) -> Optional[Dict[str, Any]]:
        return self._last_challenge

    @property
    def challenge_history(self) -> List[Dict[str, Any]]:
        return list(self._challenge_history)


def create_antibot_hook(hooks: CrawlHooks) -> AntiBotInterceptor:
    """创建并注册 AntiBot 拦截器到 CrawlHooks

    Args:
        hooks: CrawlHooks 实例

    Returns:
        AntiBotInterceptor 实例（可用于查询状态/重置）
    """
    interceptor = AntiBotInterceptor()
    hooks.on(HookEvent.BEFORE_TOOL, interceptor.before_tool, priority=-10)  # 高优先级（先执行）
    hooks.on(HookEvent.AFTER_TOOL, interceptor.after_tool, priority=10)  # 低优先级（后执行）
    return interceptor
