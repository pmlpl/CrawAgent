"""httpx 引擎 — 最快的纯 HTTP 请求引擎

适合普通页面，不支持 JS 渲染。
"""
from __future__ import annotations

import hashlib
from typing import Dict, Optional
from urllib.parse import urljoin

import httpx
from loguru import logger

from crawagent.core.models import CrawlResult
from crawagent.engines.base import BaseEngine, EngineConfig, EngineError


class HttpxEngine(BaseEngine):
    """httpx 异步引擎

    特点：
    - 最快（纯 HTTP，无浏览器开销）
    - 支持 HTTP/2
    - 不支持 JS 渲染
    - 不绕过 TLS 指纹检测

    适用场景：普通页面、API 接口、无反爬的网站
    """

    def __init__(self, config: Optional[EngineConfig] = None):
        super().__init__(config)
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def name(self) -> str:
        return "httpx"

    @property
    def is_available(self) -> bool:
        return True  # httpx 是核心依赖，始终可用

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or (hasattr(self._client, "is_closed") and self._client.is_closed):
            limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
            timeout = httpx.Timeout(self.config.timeout, connect=10.0)
            self._client = httpx.AsyncClient(
                limits=limits,
                timeout=timeout,
                follow_redirects=self.config.follow_redirects,
                http2=False,
                trust_env=False,
            )
        return self._client

    async def fetch(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        proxy: Optional[str] = None,
        **kwargs,
    ) -> CrawlResult:
        client = await self._ensure_client()

        # 合并请求头
        final_headers = self._default_headers()
        if headers:
            final_headers.update(headers)
        if self.config.extra_headers:
            final_headers.update(self.config.extra_headers)

        effective_timeout = timeout or self.config.timeout
        result = CrawlResult(url=url, strategy=self.name)

        # 代理处理：httpx 需要单独的 client
        if proxy:
            temp_client = httpx.AsyncClient(
                proxy=proxy,
                timeout=httpx.Timeout(effective_timeout, connect=10.0),
                follow_redirects=True,
                http2=False,
            )
            client_to_use = temp_client
            close_temp = True
        else:
            client_to_use = client
            close_temp = False

        try:
            response = await client_to_use.get(
                url,
                headers=final_headers,
                timeout=effective_timeout,
                follow_redirects=self.config.follow_redirects,
            )
            result.status_code = response.status_code
            result.headers = dict(response.headers)
            result.html = response.text
            result.success = 200 <= response.status_code < 400
            result.strategy = self.name

            if not result.success:
                raise EngineError(
                    self.name,
                    f"HTTP {response.status_code}",
                    status_code=response.status_code,
                )

        except EngineError:
            raise
        except httpx.TimeoutException as e:
            result.error = f"timeout: {e}"
            raise EngineError(self.name, f"Timeout: {e}")
        except httpx.ConnectError as e:
            result.error = f"connect error: {e}"
            raise EngineError(self.name, f"ConnectError: {e}")
        except Exception as e:
            result.error = str(e)
            raise EngineError(self.name, str(e))
        finally:
            if close_temp:
                await temp_client.aclose()

        # 后处理：提取标题和链接
        self._post_process(result)
        return result

    def _default_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    def _post_process(self, result: CrawlResult) -> None:
        """提取标题和链接"""
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
        if self._client:
            await self._client.aclose()
            self._client = None
        self._closed = True
