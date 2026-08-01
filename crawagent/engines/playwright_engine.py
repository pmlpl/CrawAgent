"""Playwright 引擎 — 完整浏览器渲染引擎

支持 JS 渲染、JS 注入（反检测 + 移除遮罩）、登录态持久化。
是最慢但最强大的引擎，用于 httpx 和 curl_cffi 都失败时的最终 fallback。
"""
from __future__ import annotations

import hashlib
from typing import Dict, Optional
from urllib.parse import urljoin

from loguru import logger

from crawagent.core.models import CrawlResult
from crawagent.engines.base import BaseEngine, EngineConfig, EngineError

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class PlaywrightEngine(BaseEngine):
    """Playwright 引擎

    特点：
    - 完整 JS 渲染（支持 SPA、JS 挑战）
    - JS 注入：navigator_overrider（反自动化检测）+ remove_overlay（移除遮罩）
    - 支持登录态持久化（通过 user_data_dir）
    - 最慢（浏览器启动 + JS 执行开销）

    适用场景：JS 挑战、SPA 页面、Cloudflare 5 秒盾、需要登录的页面
    """

    def __init__(self, config: Optional[EngineConfig] = None):
        super().__init__(config)
        self._playwright = None
        self._browser: Optional[Browser] = None

    @property
    def name(self) -> str:
        return "playwright"

    @property
    def is_available(self) -> bool:
        return PLAYWRIGHT_AVAILABLE

    async def _ensure_browser(self):
        if self._browser is None:
            self._playwright = await async_playwright().start()
            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
            launch_kwargs: Dict = {"headless": self.config.headless, "args": browser_args}
            if self.config.proxy:
                launch_kwargs["proxy"] = {"server": self.config.proxy}
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        return self._browser

    async def fetch(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        proxy: Optional[str] = None,
        **kwargs,
    ) -> CrawlResult:
        if not PLAYWRIGHT_AVAILABLE:
            raise EngineError(self.name, "playwright not installed")

        effective_timeout = timeout or self.config.timeout
        wait_for = kwargs.get("wait_for", self.config.wait_for)
        inject_js = kwargs.get("inject_js", self.config.inject_js)
        user_data_dir = kwargs.get("user_data_dir")  # 登录态持久化目录

        result = CrawlResult(url=url, strategy=self.name)

        # 如果指定了 user_data_dir，使用 persistent context（保持登录态）
        if user_data_dir and PLAYWRIGHT_AVAILABLE:
            return await self._fetch_with_persistent_context(
                url, user_data_dir, headers, effective_timeout, wait_for, inject_js, proxy
            )

        browser = await self._ensure_browser()

        # 代理覆盖
        if proxy:
            # 需要新 context 带代理
            context = await browser.new_context(
                proxy={"server": proxy},
                user_agent=(headers or {}).get("User-Agent", "Mozilla/5.0"),
                extra_http_headers=headers or {},
            )
        else:
            context = await browser.new_context(
                user_agent=(headers or {}).get("User-Agent", "Mozilla/5.0"),
                extra_http_headers=headers or {},
            )

        try:
            # 注入 JS（在页面导航前生效）
            if inject_js:
                await self._inject_scripts(context)

            page = await context.new_page()

            response = await page.goto(
                url,
                wait_until=wait_for,
                timeout=int(effective_timeout * 1000),
            )

            # 等待网络空闲
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            result.status_code = response.status if response else 0
            result.headers = dict(response.headers) if response else {}
            result.html = await page.content()
            result.title = await page.title()
            result.success = 200 <= result.status_code < 400
            result.strategy = self.name

            if not result.success:
                raise EngineError(
                    self.name,
                    f"HTTP {result.status_code}",
                    status_code=result.status_code,
                )

            # 提取链接
            for a in await page.query_selector_all("a[href]"):
                href = await a.get_attribute("href")
                if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    abs_url = urljoin(url, href)
                    text = (await a.inner_text()).strip() or href
                    result.links.append((abs_url, text))

            result.metadata["content_hash"] = hashlib.md5(result.html.encode()).hexdigest()

        except EngineError:
            raise
        except Exception as e:
            result.error = str(e)
            raise EngineError(self.name, str(e))
        finally:
            await context.close()

        return result

    async def _fetch_with_persistent_context(
        self,
        url: str,
        user_data_dir: str,
        headers: Optional[Dict],
        timeout: float,
        wait_for: str,
        inject_js: bool,
        proxy: Optional[str],
    ) -> CrawlResult:
        """使用 persistent context（保持登录态）"""
        from playwright.async_api import async_playwright

        result = CrawlResult(url=url, strategy="playwright:persistent")

        async with async_playwright() as p:
            launch_kwargs: Dict = {"headless": self.config.headless}
            if proxy:
                launch_kwargs["proxy"] = {"server": proxy}

            context = await p.chromium.launch_persistent_context(
                user_data_dir,
                **launch_kwargs,
                user_agent=(headers or {}).get("User-Agent", "Mozilla/5.0"),
                extra_http_headers=headers or {},
            )

            try:
                if inject_js:
                    await self._inject_scripts(context)

                page = context.pages[0] if context.pages else await context.new_page()

                response = await page.goto(
                    url,
                    wait_until=wait_for,
                    timeout=int(timeout * 1000),
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

                result.status_code = response.status if response else 0
                result.headers = dict(response.headers) if response else {}
                result.html = await page.content()
                result.title = await page.title()
                result.success = 200 <= result.status_code < 400
                result.strategy = "playwright:persistent"

                if not result.success:
                    raise EngineError(
                        self.name,
                        f"HTTP {result.status_code}",
                        status_code=result.status_code,
                    )

                for a in await page.query_selector_all("a[href]"):
                    href = await a.get_attribute("href")
                    if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                        abs_url = urljoin(url, href)
                        text = (await a.inner_text()).strip() or href
                        result.links.append((abs_url, text))

                result.metadata["content_hash"] = hashlib.md5(result.html.encode()).hexdigest()

            except EngineError:
                raise
            except Exception as e:
                result.error = str(e)
                raise EngineError(self.name, str(e))
            finally:
                await context.close()

        return result

    async def _inject_scripts(self, context: BrowserContext) -> None:
        """注入 JS 脚本到所有新页面"""
        try:
            from crawagent.js_snippets import get_all_snippets
            js_code = get_all_snippets()
            await context.add_init_script(js_code)
            logger.debug("[PlaywrightEngine] JS snippets injected")
        except Exception as e:
            logger.debug(f"[PlaywrightEngine] JS injection failed: {e}")

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._closed = True
