"""Escalation Hook — after_response 自动引擎升级

在 HTTP 响应返回后（after_response 事件）检测 401/403/429，
触发 AddFeatureError 异常，通知 FallbackChain 自动升级到更高级引擎。

参考 Firecrawl 的 escalation 机制：
- 403 → 请求启用 curl_cffi（TLS 指纹绕过）
- 429 → 请求增加延迟 / 切换代理
- Cloudflare → 请求启用 Playwright（JS 渲染）
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from loguru import logger

from crawagent.harness.hooks import CrawlHooks
from crawagent.harness.types import HookEvent


class AddFeatureError(Exception):
    """请求添加引擎能力的异常

    当 after_response 检测到反爬时，抛出此异常通知上层：
    - feature: 需要的能力（curl_cffi / browser / proxy / wait）
    - reason: 触发原因
    - status_code: HTTP 状态码

    上层 FallbackChain 捕获此异常后自动切换到对应引擎。
    """

    def __init__(
        self,
        feature: str,  # "curl_cffi" / "browser" / "proxy" / "wait"
        reason: str = "",
        status_code: int = 0,
        retry_after: float = 0.0,
    ):
        self.feature = feature
        self.reason = reason
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"AddFeature: {feature} (status={status_code}, reason={reason})")


# 升级映射：状态码 → 需要的能力
_STATUS_ESCALATION = {
    401: "auth",  # 需要登录
    403: "curl_cffi",  # 需要 TLS 指纹绕过
    429: "wait",  # 需要等待+重试
    503: "browser",  # 可能是 JS 挑战
}

# 响应头特征 → 需要的能力
_HEADER_ESCALATION = {
    "cf-ray": "browser",  # Cloudflare → 需要浏览器
    "cf-mitigated": "browser",
    "server: cloudflare": "browser",
    "akamai": "curl_cffi",
    "incapsula": "curl_cffi",
    "sucuri": "curl_cffi",
    "imperva": "curl_cffi",
}

# 响应体特征 → 需要的能力
_BODY_ESCALATION = {
    "challenge-platform": "browser",
    "cf_chl_opt": "browser",
    "just a moment": "browser",
    "g-recaptcha": "captcha",
    "h-captcha": "captcha",
    "enable javascript": "browser",
    "access denied": "curl_cffi",
}


class EscalationHookHandler:
    """after_response Hook: 检测反爬并触发引擎升级

    注册在 AFTER_RESPONSE 事件上，检查每个 HTTP 响应：
    1. 状态码 401/403/429/503 → 触发升级
    2. 响应头含 Cloudflare/WAF 指纹 → 触发升级
    3. 响应体含验证码/JS 挑战 → 触发升级

    升级方式：
    - 在 context 中注入 escalation 信息（不直接抛异常，避免中断流程）
    - 如果 context 中有 `raise_on_escalation=True`，则抛出 AddFeatureError
    """

    def __init__(self) -> None:
        self._escalation_count: int = 0
        self._last_escalation: Optional[Dict[str, Any]] = None

    async def after_response(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """after_response: 检测响应并触发升级

        context 字段:
        - response: Dict — HTTP 响应（status_code, headers, body）
        - raise_on_escalation: bool — 是否抛出 AddFeatureError（默认 False）
        """
        response = context.get("response", {})
        if not response:
            return None

        status_code = response.get("status_code", 0) or response.get("status", 0)
        headers = response.get("headers", {})
        body = str(response.get("body", "") or response.get("html", ""))[:8192].lower()
        headers_str = str({k.lower(): v for k, v in (headers or {}).items()}).lower()

        feature = None
        reason = ""

        # 状态码检测
        if status_code in _STATUS_ESCALATION:
            feature = _STATUS_ESCALATION[status_code]
            reason = f"HTTP {status_code}"

        # 响应头检测
        if not feature:
            for sig, feat in _HEADER_ESCALATION.items():
                if sig in headers_str:
                    feature = feat
                    reason = f"Header: {sig}"
                    break

        # 响应体检测
        if not feature:
            for sig, feat in _BODY_ESCALATION.items():
                if sig in body:
                    feature = feat
                    reason = f"Body: {sig}"
                    break

        if not feature:
            return None

        # 检查 Retry-After 头
        retry_after = 0.0
        retry_after_header = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after_header:
            try:
                retry_after = float(retry_after_header)
            except (ValueError, TypeError):
                pass

        self._escalation_count += 1
        self._last_escalation = {
            "feature": feature,
            "reason": reason,
            "status_code": status_code,
            "retry_after": retry_after,
            "timestamp": time.time(),
        }

        logger.warning(
            f"[EscalationHook] 触发升级: feature={feature}, "
            f"reason={reason}, status={status_code}"
        )

        # 注入升级信息到 context
        escalation_info = {"escalation": self._last_escalation}

        # 如果要求抛异常（用于 FallbackChain 直接捕获）
        if context.get("raise_on_escalation", False):
            raise AddFeatureError(
                feature=feature,
                reason=reason,
                status_code=status_code,
                retry_after=retry_after,
            )

        return escalation_info

    @property
    def escalation_count(self) -> int:
        return self._escalation_count

    @property
    def last_escalation(self) -> Optional[Dict[str, Any]]:
        return self._last_escalation

    def reset(self) -> None:
        self._escalation_count = 0
        self._last_escalation = None


def create_escalation_hook(hooks: CrawlHooks) -> EscalationHookHandler:
    """创建并注册 Escalation hook 到 CrawlHooks

    Args:
        hooks: CrawlHooks 实例

    Returns:
        EscalationHookHandler 实例
    """
    handler = EscalationHookHandler()
    hooks.on(HookEvent.AFTER_RESPONSE, handler.after_response, priority=-5)
    return handler
