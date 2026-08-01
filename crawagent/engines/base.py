"""引擎抽象基类

所有抓取引擎实现此接口，由 FallbackChain 统一调度。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from crawagent.core.models import CrawlResult


@dataclass
class EngineConfig:
    """引擎通用配置"""
    timeout: float = 30.0
    follow_redirects: bool = True
    max_redirects: int = 10
    verify_ssl: bool = True
    impersonate: str = "chrome120"  # curl_cffi 用
    headless: bool = True  # Playwright 用
    inject_js: bool = True  # Playwright 用：是否注入 JS 片段
    wait_for: str = "domcontentloaded"  # Playwright 等待策略
    extra_headers: Dict[str, str] = field(default_factory=dict)
    proxy: Optional[str] = None  # 代理 URL


class BaseEngine(abc.ABC):
    """抓取引擎抽象基类

    每个引擎负责一种抓取方式：
    - HttpxEngine: 纯 HTTP 请求，最快
    - CurlCffiEngine: TLS 指纹模拟，绕过 Cloudflare
    - PlaywrightEngine: 完整浏览器渲染，最慢但最强

    引擎不负责重试、限速、代理轮换——这些由上层 FallbackChain/Fetcher 管理。
    """

    def __init__(self, config: Optional[EngineConfig] = None):
        self.config = config or EngineConfig()
        self._closed = False

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """引擎名称"""
        ...

    @property
    @abc.abstractmethod
    def is_available(self) -> bool:
        """引擎是否可用（依赖已安装）"""
        ...

    @abc.abstractmethod
    async def fetch(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        proxy: Optional[str] = None,
        **kwargs,
    ) -> CrawlResult:
        """抓取单个 URL

        Args:
            url: 目标 URL
            headers: 额外请求头
            timeout: 超时秒数
            proxy: 代理 URL
            **kwargs: 引擎特定参数

        Returns:
            CrawlResult

        Raises:
            EngineError: 引擎执行失败
        """
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        """释放资源"""
        ...

    async def __aenter__(self) -> "BaseEngine":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()


class EngineError(Exception):
    """引擎执行错误"""

    def __init__(self, engine_name: str, message: str, status_code: int = 0):
        self.engine_name = engine_name
        self.status_code = status_code
        super().__init__(f"[{engine_name}] {message}")
