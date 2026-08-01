"""CrawlEnv 执行环境抽象

抽象 Agent 与外部世界的交互：
- 文件系统：读写文件、创建目录
- HTTP：httpx / curl_cffi 客户端管理
- 浏览器：Playwright 浏览器实例管理

Agent 不直接 import httpx/playwright，而是通过 CrawlEnv 访问，
方便 hook 拦截、测试 mock、资源管理。

参考 pi Agent Harness 的 ExecutionEnv 设计。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class CrawlEnv:
    """执行环境抽象
    
    抽象 Agent 与外部世界的交互：
    - 文件系统：读写文件、创建目录
    - HTTP：httpx / curl_cffi 客户端管理
    - 浏览器：Playwright 浏览器实例管理
    
    Agent 不直接 import httpx/playwright，而是通过 CrawlEnv 访问，
    这样方便 hook 拦截、测试 mock、资源管理。
    """
    
    def __init__(
        self,
        output_base_dir: str = "~/crawagent",
        max_file_size: int = 100 * 1024 * 1024,  # 100MB
    ):
        self.output_base_dir = Path(os.path.expanduser(output_base_dir))
        self.max_file_size = max_file_size
        self._httpx_client = None
        self._curl_session = None
        self._playwright_instance = None
        self._browser = None
    
    # ---- 文件系统 ----
    
    def ensure_dir(self, path: str) -> Path:
        """确保目录存在"""
        p = Path(os.path.expanduser(path))
        p.mkdir(parents=True, exist_ok=True)
        return p
    
    async def write_file(self, path: str, content: str, append: bool = False) -> str:
        """写入文件，返回绝对路径"""
        p = Path(os.path.expanduser(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        
        # 检查文件大小
        if len(content) > self.max_file_size:
            raise ValueError(f"Content too large: {len(content)} > {self.max_file_size}")
        
        mode = "a" if append else "w"
        encoding = "utf-8"
        
        # 根据扩展名决定是否用二进制
        with open(p, mode, encoding=encoding) as f:
            f.write(content)
        
        return str(p.resolve())
    
    async def read_file(self, path: str) -> str:
        """读取文件"""
        p = Path(os.path.expanduser(path))
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        return p.read_text(encoding="utf-8")
    
    # ---- HTTP ----
    
    async def get_httpx_client(self):
        """获取/创建 httpx 异步客户端"""
        if self._httpx_client is None or (hasattr(self._httpx_client, 'is_closed') and self._httpx_client.is_closed):
            import httpx
            self._httpx_client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
                trust_env=False,
            )
        return self._httpx_client
    
    async def get_curl_session(self, impersonate: str = "chrome120"):
        """获取/创建 curl_cffi 会话"""
        if self._curl_session is None:
            try:
                from curl_cffi.requests import AsyncSession as CurlAsyncSession
                self._curl_session = CurlAsyncSession(
                    impersonate=impersonate,
                    timeout=30.0,
                )
            except ImportError:
                logger.warning("curl_cffi not available, falling back to httpx")
                return None
        return self._curl_session
    
    # ---- 浏览器 ----
    
    async def get_browser(self, headless: bool = True):
        """获取/创建 Playwright 浏览器实例"""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._playwright_instance = await async_playwright().start()
            self._browser = await self._playwright_instance.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled", "--no-proxy-server"]
            )
        return self._browser
    
    # ---- 生命周期 ----
    
    async def close(self) -> None:
        """关闭所有资源"""
        if self._httpx_client:
            await self._httpx_client.aclose()
            self._httpx_client = None
        if self._curl_session:
            await self._curl_session.close()
            self._curl_session = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright_instance:
            await self._playwright_instance.stop()
            self._playwright_instance = None
    
    async def __aenter__(self) -> "CrawlEnv":
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.close()
