from __future__ import annotations

import asyncio
import hashlib
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Callable, Awaitable
from urllib.parse import urlparse, urljoin
from functools import wraps

import httpx
from tenacity import (
    retry, stop_after_attempt, wait_exponential_jitter,
    retry_if_exception_type, before_sleep_log, RetryCallState,
    Retrying, AsyncRetrying
)

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False

from loguru import logger
from crawagent.core.models import CrawlResult
from crawagent.core.frontier import SQLiteFrontier
from crawagent.config.settings import get_settings


# 可重试异常
RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)


def is_retryable_response(response: Any) -> bool:
    """判断响应是否可重试"""
    status_code = getattr(response, 'status_code', None)
    return status_code in (429, 500, 502, 503, 504)


def get_retryable_error_message(response: Any) -> str:
    status_code = getattr(response, 'status_code', 'unknown')
    reason = getattr(response, 'reason_phrase', None) or getattr(response, 'reason', '')
    return f"HTTP {status_code}: {reason}"


class RetryableError(Exception):
    """标记为可重试的错误"""
    pass


def _extract_status_from_exc(exc: BaseException) -> int:
    """从异常中提取 HTTP 状态码（供失败结果保留真实状态供下游 AntiBot 检测）。

    优先级：
    1. httpx.HTTPStatusError 的 response.status_code
    2. 异常消息中的 "HTTP {code}" 模式（如 RetryableError("Playwright HTTP 403")）
    """
    resp = getattr(exc, "response", None)
    if resp is not None:
        status = getattr(resp, "status_code", None)
        if status:
            try:
                return int(status)
            except (TypeError, ValueError):
                pass
    import re
    m = re.search(r"HTTP\s*(\d{3})", str(exc))
    if m:
        return int(m.group(1))
    return 0


@dataclass
class DomainLimiter:
    """每域名限速器：令牌桶 + 最小间隔"""
    rate: float  # requests per second
    _tokens: float = field(init=False, default=1.0)
    _last_update: float = field(init=False, default_factory=time.monotonic)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self):
        self._tokens = self.rate  # 初始满桶

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # 补充令牌
            elapsed = now - self._last_update
            self._tokens = min(self.rate, self._tokens + elapsed * self.rate)
            self._last_update = now

            if self._tokens >= 1:
                self._tokens -= 1
                return

            # 等待令牌
            wait_time = (1 - self._tokens) / self.rate
            self._tokens = 0
        await asyncio.sleep(wait_time)


class ProxyRotator:
    """代理轮换器"""

    def __init__(self, proxies: List[str] = None):
        self.proxies = proxies or []
        self._index = 0
        self._lock = asyncio.Lock()

    def add_proxy(self, proxy: str) -> None:
        self.proxies.append(proxy)

    async def get_proxy(self) -> Optional[str]:
        if not self.proxies:
            return None
        async with self._lock:
            proxy = self.proxies[self._index]
            self._index = (self._index + 1) % len(self.proxies)
            return proxy


class UserAgentRotator:
    """User-Agent 轮换器"""

    DEFAULT_UAS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    def __init__(self, custom_uas: List[str] = None):
        self.uas = custom_uas or self.DEFAULT_UAS
        self._index = 0
        self._lock = asyncio.Lock()

    async def get_ua(self) -> str:
        async with self._lock:
            ua = self.uas[self._index]
            self._index = (self._index + 1) % len(self.uas)
            return ua


class Fetcher:
    """
    异步抓取器：
    - httpx (主力) + curl_cffi (反爬时)
    - 指数退避 + 抖动重试
    - 每域名限速 (令牌桶)
    - 代理轮换 + UA 轮换
    - curl_cffi impersonate 浏览器 TLS 指纹
    """

    def __init__(
        self,
        frontier: Optional[SQLiteFrontier] = None,
        per_domain_rate: float = 0.5,
        max_concurrent: int = 50,
        default_timeout: float = 30.0,
        max_retries: int = 3,
        proxy_pool: List[str] = None,
        custom_uas: List[str] = None,
        impersonate: str = "chrome120",
        use_curl_cffi: bool = True,
    ):
        self.frontier = frontier
        self.per_domain_rate = per_domain_rate
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self.impersonate = impersonate
        self.use_curl_cffi = use_curl_cffi and CURL_CFFI_AVAILABLE

        # 域名限速器
        self._domain_limiters: Dict[str, DomainLimiter] = {}
        self._limiter_lock = asyncio.Lock()

        # 并发控制
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # 组件
        self._proxy_rotator = ProxyRotator(proxy_pool)
        self._ua_rotator = UserAgentRotator()

        # 客户端
        self._httpx_client: Optional[httpx.AsyncClient] = None
        self._curl_session: Optional[Any] = None
        self._closed = False

        # 统计
        self._stats = {
            "total_requests": 0,
            "successful": 0,
            "failed": 0,
            "retries": 0,
            "curl_cffi_used": 0,
        }

    def _get_domain(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def _init_clients(self) -> None:
        """初始化客户端"""
        if self._httpx_client is None or (hasattr(self._httpx_client, 'is_closed') and self._httpx_client.is_closed):
            limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
            timeout = httpx.Timeout(self.default_timeout, connect=10.0)
            self._httpx_client = httpx.AsyncClient(
                limits=limits,
                timeout=timeout,
                follow_redirects=True,
                http2=False,
                trust_env=False,
            )

        if self.use_curl_cffi and self._curl_session is None:
            try:
                self._curl_session = CurlAsyncSession(
                    impersonate=self.impersonate,
                    timeout=self.default_timeout,
                )
            except Exception as e:
                logger.warning(f"curl_cffi 初始化失败，回退 httpx: {e}")
                self.use_curl_cffi = False

    async def _get_limiter(self, domain: str) -> DomainLimiter:
        async with self._limiter_lock:
            if domain not in self._domain_limiters:
                self._domain_limiters[domain] = DomainLimiter(rate=self.per_domain_rate)
            return self._domain_limiters[domain]

    def _build_headers(self, url: str, extra_headers: Dict = None, ua: str = None) -> Dict[str, str]:
        headers = {
            "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    async def __aenter__(self) -> "Fetcher":
        await self._init_clients()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def _fetch_httpx(
        self,
        url: str,
        headers: Dict,
        proxy: Optional[str],
        timeout: float,
        follow_redirects: bool,
    ) -> CrawlResult:
        """使用 httpx 抓取"""
        client = self._httpx_client
        if not client:
            raise RuntimeError("HTTPX client not initialized")

        result = CrawlResult(url=url)

        # 如果有代理，需要创建新的 client（httpx 不支持 get 时传 proxy）
        if proxy:
            temp_client = httpx.AsyncClient(
                proxy=proxy,
                limits=client._limits,
                timeout=client._timeout,
                follow_redirects=True,
                http2=False,
            )
            client_to_use = temp_client
            close_temp = True
        else:
            client_to_use = self._httpx_client
            close_temp = False

        result = CrawlResult(url=url)

        try:
            response = await client_to_use.get(
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=follow_redirects,
            )
            result.status_code = response.status_code
            result.headers = dict(response.headers)

            if is_retryable_response(response):
                raise RetryableError(get_retryable_error_message(response))

            response.raise_for_status()
            result.html = response.text
            result.success = True
            result.strategy = "httpx"

        except RETRYABLE_EXCEPTIONS as e:
            result.error = f"请求异常: {type(e).__name__}: {e}"
            raise
        except Exception as e:
            result.error = f"未知错误: {e}"
            raise
        finally:
            if close_temp:
                await temp_client.aclose()

        return result

    async def _fetch_curl_cffi(
        self,
        url: str,
        headers: Dict,
        proxy: Optional[str],
        timeout: float,
    ) -> CrawlResult:
        """使用 curl_cffi 抓取（impersonate 浏览器指纹）"""
        if not self._curl_session:
            raise RuntimeError("curl_cffi session not initialized")

        result = CrawlResult(url=url)

        try:
            response = await self._curl_session.get(
                url,
                headers=headers,
                proxy=proxy,
                timeout=timeout,
                impersonate=self.impersonate,
            )
            result.status_code = response.status_code
            result.headers = dict(response.headers)

            if is_retryable_response(response):
                raise RetryableError(get_retryable_error_message(response))

            # curl_cffi response doesn't have raise_for_status, check manually
            if 400 <= response.status_code < 600:
                raise RetryableError(get_retryable_error_message(response))

            result.html = response.text
            result.success = True
            result.strategy = f"curl_cffi:{self.impersonate}"
            self._stats["curl_cffi_used"] += 1

        except RETRYABLE_EXCEPTIONS as e:
            result.error = f"curl_cffi 请求异常: {type(e).__name__}: {e}"
            raise
        except Exception as e:
            result.error = f"curl_cffi 未知错误: {e}"
            raise

        return result

    async def _fetch_playwright(
        self,
        url: str,
        headers: Dict,
        proxy: Optional[str],
        timeout: float,
    ) -> CrawlResult:
        """使用 Playwright 抓取（JS 渲染，第三级回退）

        当 httpx 和 curl_cffi 均失败（通常是 JS 挑战/Cloudflare 5 秒盾）时启用。
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("Playwright not installed")

        result = CrawlResult(url=url)

        async with async_playwright() as p:
            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-proxy-server",
            ]
            launch_kwargs: Dict = {"headless": True, "args": browser_args}
            if proxy:
                launch_kwargs["proxy"] = {"server": proxy}

            browser = await p.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context(
                    user_agent=headers.get("User-Agent", "Mozilla/5.0"),
                    extra_http_headers=headers,
                )
                page = await context.new_page()

                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(timeout * 1000),
                )
                # 等待网络空闲（JS 渲染完成）
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

                result.status_code = response.status if response else 0
                result.headers = dict(response.headers) if response else {}

                if 400 <= result.status_code < 600:
                    raise RetryableError(
                        f"Playwright HTTP {result.status_code}"
                    )

                result.html = await page.content()
                result.title = await page.title()
                result.success = True
                result.strategy = "playwright"

                # 提取链接
                for a in await page.query_selector_all("a[href]"):
                    href = await a.get_attribute("href")
                    if href and not href.startswith(
                        ("#", "javascript:", "mailto:", "tel:")
                    ):
                        abs_url = urljoin(url, href)
                        text = (await a.inner_text()).strip() or href
                        result.links.append((abs_url, text))
            finally:
                await browser.close()

        return result

    async def fetch(
        self,
        url: str,
        *,
        extra_headers: Dict = None,
        timeout: float = None,
        follow_redirects: bool = True,
        use_curl_cffi: bool = None,
        use_browser: bool = False,
    ) -> CrawlResult:
        """
        抓取单个 URL
        - 自动限速、重试、代理、UA 轮换
        - 引擎 Fallback 链: httpx → curl_cffi → Playwright
        - use_browser=True 时直接使用 Playwright
        """
        if self._closed:
            raise RuntimeError("Fetcher closed")

        # 懒初始化客户端：未通过 async with 使用时，首次 fetch 自动初始化
        # httpx/curl_cffi session，避免静默回退 Playwright
        if self._httpx_client is None:
            await self._init_clients()

        domain = self._get_domain(url)
        limiter = await self._get_limiter(domain)
        ua = await self._ua_rotator.get_ua()
        proxy = await self._proxy_rotator.get_proxy()
        headers = self._build_headers(url, extra_headers, ua)
        effective_timeout = timeout or self.default_timeout
        effective_use_curl = use_curl_cffi if use_curl_cffi is not None else self.use_curl_cffi

        # 限速
        await limiter.acquire()

        # 并发控制
        async with self._semaphore:
            self._stats["total_requests"] += 1

            # 重试器
            retryer = self._make_retryer(domain)

            async def _attempt() -> CrawlResult:
                # 浏览器模式：直接使用 Playwright
                if use_browser:
                    return await self._fetch_playwright(url, headers, proxy, effective_timeout)

                # 三级回退链: httpx → curl_cffi → Playwright
                # 1. httpx（最快，适合普通页面）
                try:
                    return await self._fetch_httpx(url, headers, proxy, effective_timeout, True)
                except Exception as e:
                    logger.debug(f"httpx 失败，回退 curl_cffi: {e}")

                # 2. curl_cffi（TLS 指纹绕过，适合 Cloudflare/WAF）
                if effective_use_curl and self.use_curl_cffi:
                    try:
                        return await self._fetch_curl_cffi(url, headers, proxy, effective_timeout)
                    except Exception as e:
                        logger.debug(f"curl_cffi 失败，回退 Playwright: {e}")

                # 3. Playwright（JS 渲染，最终回退，适合 JS 挑战）
                return await self._fetch_playwright(url, headers, proxy, effective_timeout)

            try:
                result = await retryer(_attempt)
                self._stats["successful"] += 1

                # 提取基础信息（Playwright 已提取，跳过）
                if result.success and result.html and result.strategy != "playwright":
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(result.html, "html.parser")
                    title_tag = soup.find("title")
                    result.title = title_tag.get_text(strip=True) if title_tag else ""
                    # 提取链接
                    for a in soup.find_all("a", href=True):
                        href = a["href"].strip()
                        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                            continue
                        abs_url = urljoin(url, href)
                        text = a.get_text(strip=True) or href
                        result.links.append((abs_url, text))

                # 内容哈希用于去重
                if result.success:
                    result.metadata["content_hash"] = hashlib.md5(result.html.encode()).hexdigest()

                return result

            except Exception as e:
                self._stats["failed"] += 1
                # 保留真实 HTTP 状态码，供下游 AntiBot hook 检测反爬（403/429 等）
                status = _extract_status_from_exc(e)
                result = CrawlResult(url=url, error=str(e), status_code=status)
                return result

    async def fetch_batch(
        self,
        urls: List[str],
        **kwargs
    ) -> List[CrawlResult]:
        """批量抓取，自动并发控制"""
        tasks = [self.fetch(url, **kwargs) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    def get_stats(self) -> Dict:
        return self._stats.copy()

    def _make_retryer(self, domain: str) -> AsyncRetrying:
        """构建带域名感知的重试器"""
        return AsyncRetrying(
            wait=wait_exponential_jitter(initial=1, max=30, jitter=2),
            stop=stop_after_attempt(self.max_retries + 1),
            retry=(
                retry_if_exception_type(RETRYABLE_EXCEPTIONS) |
                retry_if_exception_type(RetryableError)
            ),
            before_sleep=before_sleep_log(logger, "WARNING"),
            reraise=True,
        )

    async def close(self) -> None:
        if self._httpx_client:
            await self._httpx_client.aclose()
            self._httpx_client = None
        if self._curl_session:
            await self._curl_session.close()
            self._curl_session = None
        self._closed = True

    async def add_proxy(self, proxy: str) -> None:
        self._proxy_rotator.add_proxy(proxy)