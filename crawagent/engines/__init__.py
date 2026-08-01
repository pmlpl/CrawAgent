"""引擎抽象层

提供统一的 HTTP 抓取引擎接口：
- BaseEngine: 抽象基类
- HttpxEngine: httpx 引擎（最快，无 JS 渲染）
- CurlCffiEngine: curl_cffi 引擎（TLS 指纹绕过）
- PlaywrightEngine: Playwright 引擎（JS 渲染 + JS 注入）
- FallbackChain: waterfall fallback 执行器

参考 Firecrawl engines/ 设计。
"""
from crawagent.engines.base import BaseEngine, EngineConfig, EngineError
from crawagent.engines.httpx_engine import HttpxEngine
from crawagent.engines.curl_cffi_engine import CurlCffiEngine
from crawagent.engines.playwright_engine import PlaywrightEngine
from crawagent.engines.fallback import FallbackChain, build_fallback_list

__all__ = [
    "BaseEngine",
    "EngineConfig",
    "EngineError",
    "HttpxEngine",
    "CurlCffiEngine",
    "PlaywrightEngine",
    "FallbackChain",
    "build_fallback_list",
]
