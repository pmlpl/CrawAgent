"""Waterfall fallback 链

按引擎优先级依次尝试：httpx → curl_cffi → playwright
每个引擎失败后自动切换到下一个，记录失败原因。

参考 Firecrawl engines/index.ts 的 waterfall 设计。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from loguru import logger

from crawagent.core.models import CrawlResult
from crawagent.engines.base import BaseEngine, EngineConfig, EngineError
from crawagent.engines.httpx_engine import HttpxEngine
from crawagent.engines.curl_cffi_engine import CurlCffiEngine
from crawagent.engines.playwright_engine import PlaywrightEngine
from crawagent.graph.anti_bot import is_blocked


@dataclass
class FallbackAttempt:
    """单次 fallback 尝试记录"""
    engine_name: str
    success: bool
    error: str = ""
    duration_ms: float = 0.0
    blocked: bool = False
    block_type: str = ""


@dataclass
class FallbackResult:
    """fallback 链执行结果"""
    result: Optional[CrawlResult] = None
    attempts: List[FallbackAttempt] = field(default_factory=list)
    engine_used: str = ""
    total_duration_ms: float = 0.0

    @property
    def success(self) -> bool:
        return self.result is not None and self.result.success


# 默认引擎优先级（由快到慢）
DEFAULT_ENGINE_ORDER: List[Type[BaseEngine]] = [
    HttpxEngine,
    CurlCffiEngine,
    PlaywrightEngine,
]


def build_fallback_list(
    config: Optional[EngineConfig] = None,
    engines: Optional[List[Type[BaseEngine]]] = None,
    skip_unavailable: bool = True,
) -> List[BaseEngine]:
    """构建 fallback 引擎列表

    Args:
        config: 引擎配置（所有引擎共享）
        engines: 自定义引擎类列表（默认 httpx → curl_cffi → playwright）
        skip_unavailable: 跳过不可用的引擎（如 curl_cffi 未安装）

    Returns:
        引擎实例列表，按优先级排序
    """
    engine_classes = engines or DEFAULT_ENGINE_ORDER
    config = config or EngineConfig()

    result: List[BaseEngine] = []
    for engine_cls in engine_classes:
        engine = engine_cls(config=config)
        if skip_unavailable and not engine.is_available:
            logger.debug(f"Skipping unavailable engine: {engine.name}")
            continue
        result.append(engine)

    return result


class FallbackChain:
    """Waterfall fallback 执行器

    按引擎列表依次尝试抓取：
    1. 首先尝试最快的引擎（httpx）
    2. 失败后检查是否被反爬阻断（is_blocked）
    3. 如果被阻断，跳过到更高级引擎（curl_cffi / playwright）
    4. 如果是网络错误（超时/连接失败），也尝试下一个引擎
    5. 记录每次尝试的结果，用于分析和调试

    用法:
        chain = FallbackChain()
        result = await chain.fetch("https://example.com")
        print(result.engine_used)  # "httpx" or "curl_cffi" or "playwright"
    """

    def __init__(
        self,
        engines: Optional[List[BaseEngine]] = None,
        config: Optional[EngineConfig] = None,
    ):
        self.config = config or EngineConfig()
        self.engines = engines or build_fallback_list(self.config)
        self._owned = engines is None  # 是否自己创建的引擎（close 时需清理）

    async def fetch(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        proxy: Optional[str] = None,
        use_browser: bool = False,
        **kwargs,
    ) -> FallbackResult:
        """执行 waterfall fallback 抓取

        Args:
            url: 目标 URL
            headers: 额外请求头
            timeout: 超时秒数
            proxy: 代理 URL
            use_browser: 直接使用 Playwright（跳过 httpx/curl_cffi）
            **kwargs: 传递给引擎的额外参数

        Returns:
            FallbackResult: 包含最终结果和所有尝试记录
        """
        import time
        start = time.monotonic()

        # use_browser=True 时直接使用 Playwright 引擎
        if use_browser:
            playwright_engines = [e for e in self.engines if e.name == "playwright"]
            if not playwright_engines:
                # 强制浏览器模式但引擎缺失时明确报错，避免静默退化成 httpx 优先
                raise EngineError(
                    engine_name="playwright",
                    message="强制浏览器模式但 Playwright 引擎不可用"
                            "（请安装 playwright 并执行 playwright install chromium）",
                )
            engines_to_try = playwright_engines
        else:
            engines_to_try = self.engines

        attempts: List[FallbackAttempt] = []
        final_result: Optional[CrawlResult] = None

        for engine in engines_to_try:
            attempt_start = time.monotonic()

            try:
                logger.debug(f"[FallbackChain] Trying {engine.name} for {url[:80]}")

                result = await engine.fetch(
                    url,
                    headers=headers,
                    timeout=timeout,
                    proxy=proxy,
                    **kwargs,
                )

                duration_ms = (time.monotonic() - attempt_start) * 1000

                # 检查是否被反爬阻断
                block_check = is_blocked(
                    status_code=result.status_code,
                    headers=result.headers,
                    body=result.html,
                    error=result.error or "",
                )

                attempt = FallbackAttempt(
                    engine_name=engine.name,
                    success=result.success,
                    duration_ms=duration_ms,
                    blocked=block_check.blocked,
                    block_type=block_check.challenge_type,
                )
                attempts.append(attempt)

                if result.success and not block_check.blocked:
                    final_result = result
                    logger.info(
                        f"[FallbackChain] {engine.name} succeeded "
                        f"({duration_ms:.0f}ms, status={result.status_code})"
                    )
                    break

                if block_check.blocked:
                    logger.warning(
                        f"[FallbackChain] {engine.name} blocked: "
                        f"{block_check.challenge_type} ({block_check.evidence})"
                    )
                    # 保存部分结果用于调试
                    if final_result is None:
                        final_result = result
                else:
                    # 非阻断但失败
                    logger.debug(
                        f"[FallbackChain] {engine.name} failed: {result.error}"
                    )
                    if final_result is None:
                        final_result = result

            except EngineError as e:
                duration_ms = (time.monotonic() - attempt_start) * 1000
                # 检查是否是反爬阻断（从异常状态码）
                block_check = is_blocked(
                    status_code=e.status_code,
                    body="",
                    error=str(e),
                )

                attempts.append(FallbackAttempt(
                    engine_name=engine.name,
                    success=False,
                    error=str(e),
                    duration_ms=duration_ms,
                    blocked=block_check.blocked,
                    block_type=block_check.challenge_type,
                ))

                logger.debug(f"[FallbackChain] {engine.name} error: {e}")

            except Exception as e:
                duration_ms = (time.monotonic() - attempt_start) * 1000
                attempts.append(FallbackAttempt(
                    engine_name=engine.name,
                    success=False,
                    error=str(e),
                    duration_ms=duration_ms,
                ))
                logger.debug(f"[FallbackChain] {engine.name} unexpected error: {e}")

        total_ms = (time.monotonic() - start) * 1000

        return FallbackResult(
            result=final_result,
            attempts=attempts,
            engine_used=final_result.strategy if final_result else "",
            total_duration_ms=total_ms,
        )

    async def close(self) -> None:
        """关闭所有自己创建的引擎"""
        if self._owned:
            for engine in self.engines:
                try:
                    await engine.close()
                except Exception as e:
                    logger.debug(f"Error closing {engine.name}: {e}")

    async def __aenter__(self) -> "FallbackChain":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
