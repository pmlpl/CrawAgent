"""curl_cffi 引擎 — TLS 指纹绕过引擎

通过 impersonate 浏览器 TLS 指纹绕过 Cloudflare/WAF 检测。
比 Playwright 快得多，但不支持 JS 渲染。
"""
from __future__ import annotations

import hashlib
from typing import Dict, Optional
from urllib.parse import urljoin

from loguru import logger

from crawagent.core.models import CrawlResult
from crawagent.engines.base import BaseEngine, EngineConfig, EngineError

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False


class CurlCffiEngine(BaseEngine):
    """curl_cffi 引擎

    特点：
    - impersonate 浏览器 TLS 指纹（chrome120, safari17 等）
    - 绕过 Cloudflare/WAF 的 TLS 指纹检测
    - 不支持 JS 渲染
    - 速度介于 httpx 和 Playwright 之间

    适用场景：Cloudflare 5 秒盾、WAF 防护、TLS 指纹检测
    """

    def __init__(self, config: Optional[EngineConfig] = None):
        super().__init__(config)
        self._session = None

    @property
    def name(self) -> str:
        return "curl_cffi"

    @property
    def is_available(self) -> bool:
        return CURL_CFFI_AVAILABLE

    async def _ensure_session(self):
        if self._session is None:
            self._session = CurlAsyncSession(
                impersonate=self.config.impersonate,
                timeout=self.config.timeout,
            )
        return self._session

    async def fetch(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        proxy: Optional[str] = None,
        **kwargs,
    ) -> CrawlResult:
        if not CURL_CFFI_AVAILABLE:
            raise EngineError(self.name, "curl_cffi not installed")

        session = await self._ensure_session()

        final_headers = self._default_headers()
        if headers:
            final_headers.update(headers)

        effective_timeout = timeout or self.config.timeout
        impersonate = kwargs.get("impersonate", self.config.impersonate)
        result = CrawlResult(url=url, strategy=f"{self.name}:{impersonate}")

        try:
            response = await session.get(
                url,
                headers=final_headers,
                proxy=proxy,
                timeout=effective_timeout,
                impersonate=impersonate,
            )
            result.status_code = response.status_code
            result.headers = dict(response.headers)
            result.html = response.text
            result.success = 200 <= response.status_code < 400
            result.strategy = f"{self.name}:{impersonate}"

            if not result.success:
                raise EngineError(
                    self.name,
                    f"HTTP {response.status_code}",
                    status_code=response.status_code,
                )

        except EngineError:
            raise
        except Exception as e:
            result.error = str(e)
            raise EngineError(self.name, str(e))

        self._post_process(result)
        return result

    def _default_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def _post_process(self, result: CrawlResult) -> None:
        if not result.html:
            return
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(result.html, "html.parser")

        title_tag = soup.find("title")
        result.title = title_tag.get_text(strip=True) if title_tag else ""

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            abs_url = urljoin(result.url, href)
            text = a.get_text(strip=True) or href
            result.links.append((abs_url, text))

        result.metadata["content_hash"] = hashlib.md5(result.html.encode()).hexdigest()

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        self._closed = True
