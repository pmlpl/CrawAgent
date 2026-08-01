"""代理配置与轮换管理

参考 Crawl4AI proxy_strategy.py 和 Firecrawl 的代理设计。

功能：
- ProxyConfig: 代理配置数据模型（支持 http/socks5）
- RoundRobinProxy: 轮换代理池，带健康检查和自动剔除
- ProxyStrategy: 代理策略（按域名/按请求/按失败率选择代理）
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Callable, Awaitable
from urllib.parse import urlparse

from loguru import logger


class ProxyType(str, Enum):
    HTTP = "http"
    HTTPS = "https"
    SOCKS5 = "socks5"


class ProxyHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ProxyConfig:
    """单个代理配置"""
    url: str  # 完整 URL: http://user:pass@host:port 或 socks5://host:port
    proxy_type: ProxyType = ProxyType.HTTP
    username: str = ""
    password: str = ""
    region: str = ""  # 地区标识：us, cn, jp 等
    weight: int = 1  # 权重（高权重的代理被选中概率更大）
    max_failures: int = 3  # 最大失败次数，超过则标记为不可用

    # 运行时状态
    _failures: int = field(init=False, default=0)
    _successes: int = field(init=False, default=0)
    _last_used: float = field(init=False, default=0.0)
    _health: ProxyHealth = field(init=False, default=ProxyHealth.HEALTHY)

    def __post_init__(self):
        """从 URL 解析类型和认证信息"""
        if self.url:
            parsed = urlparse(self.url)
            scheme = parsed.scheme.lower()
            if scheme == "socks5" or scheme == "socks5h":
                self.proxy_type = ProxyType.SOCKS5
            elif scheme == "https":
                self.proxy_type = ProxyType.HTTPS
            else:
                self.proxy_type = ProxyType.HTTP

            if not self.username and parsed.username:
                self.username = parsed.username
            if not self.password and parsed.password:
                self.password = parsed.password

    @property
    def health(self) -> ProxyHealth:
        return self._health

    @property
    def is_available(self) -> bool:
        """代理是否可用"""
        return self._health != ProxyHealth.UNHEALTHY

    @property
    def failure_rate(self) -> float:
        total = self._failures + self._successes
        if total == 0:
            return 0.0
        return self._failures / total

    def record_success(self) -> None:
        self._successes += 1
        self._last_used = time.monotonic()
        if self._health == ProxyHealth.DEGRADED:
            self._health = ProxyHealth.HEALTHY
            logger.debug(f"Proxy {self.url[:30]}... recovered to healthy")

    def record_failure(self) -> None:
        self._failures += 1
        self._last_used = time.monotonic()
        if self._failures >= self.max_failures:
            self._health = ProxyHealth.UNHEALTHY
            logger.warning(
                f"Proxy {self.url[:30]}... marked unhealthy "
                f"(failures={self._failures}/{self.max_failures})"
            )
        elif self._failures >= self.max_failures // 2:
            self._health = ProxyHealth.DEGRADED
            logger.debug(
                f"Proxy {self.url[:30]}... degraded "
                f"(failures={self._failures})"
            )

    def reset_health(self) -> None:
        """重置健康状态（用于手动恢复）"""
        self._failures = 0
        self._health = ProxyHealth.HEALTHY

    def to_httpx_proxy(self) -> str:
        """转换为 httpx 可用的代理 URL"""
        return self.url

    def to_playwright_proxy(self) -> Dict[str, str]:
        """转换为 Playwright 可用的代理配置"""
        parsed = urlparse(self.url)
        result: Dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if self.username:
            result["username"] = self.username
        if self.password:
            result["password"] = self.password
        return result


class RoundRobinProxy:
    """轮换代理池

    - 支持加权轮换
    - 自动健康检查：失败超过阈值自动剔除
    - 冷却恢复：不可用代理冷却一段时间后自动恢复为 degraded
    - 线程安全：asyncio.Lock 保护
    """

    def __init__(
        self,
        proxies: Optional[List[ProxyConfig]] = None,
        cooldown_seconds: float = 60.0,
    ):
        self._proxies: List[ProxyConfig] = proxies or []
        self._index: int = 0
        self._lock = asyncio.Lock()
        self._cooldown_seconds = cooldown_seconds
        self._last_check: float = time.monotonic()

    def add_proxy(self, proxy: ProxyConfig | str) -> None:
        """添加代理"""
        if isinstance(proxy, str):
            proxy = ProxyConfig(url=proxy)
        self._proxies.append(proxy)

    def add_proxies(self, urls: List[str]) -> None:
        """批量添加代理"""
        for url in urls:
            self.add_proxy(url)

    @property
    def available_count(self) -> int:
        return sum(1 for p in self._proxies if p.is_available)

    @property
    def total_count(self) -> int:
        return len(self._proxies)

    async def get_proxy(self) -> Optional[ProxyConfig]:
        """获取下一个可用代理（加权轮换）

        Returns:
            ProxyConfig 或 None（无可用代理时）
        """
        if not self._proxies:
            return None

        async with self._lock:
            # 尝试冷却恢复
            self._try_recover()

            # 收集可用代理
            available = [p for p in self._proxies if p.is_available]
            if not available:
                logger.warning("No available proxies in pool")
                return None

            # 加权轮换：按权重展开，然后轮换
            # 简化实现：直接轮换可用代理列表
            proxy = available[self._index % len(available)]
            self._index = (self._index + 1) % max(len(available), 1)
            return proxy

    async def get_best_proxy(self) -> Optional[ProxyConfig]:
        """获取最优代理（最低失败率）

        Returns:
            ProxyConfig 或 None
        """
        if not self._proxies:
            return None

        async with self._lock:
            self._try_recover()
            available = [p for p in self._proxies if p.is_available]
            if not available:
                return None

            # 按失败率排序，选最低的
            available.sort(key=lambda p: (p.failure_rate, -p.weight))
            return available[0]

    async def get_random_proxy(self) -> Optional[ProxyConfig]:
        """随机获取代理"""
        if not self._proxies:
            return None

        async with self._lock:
            self._try_recover()
            available = [p for p in self._proxies if p.is_available]
            if not available:
                return None

            # 加权随机
            weights = [p.weight for p in available]
            return random.choices(available, weights=weights, k=1)[0]

    def report_success(self, proxy: ProxyConfig) -> None:
        """报告代理成功"""
        proxy.record_success()

    def report_failure(self, proxy: ProxyConfig) -> None:
        """报告代理失败"""
        proxy.record_failure()

    def _try_recover(self) -> None:
        """尝试恢复冷却中的代理"""
        now = time.monotonic()
        if now - self._last_check < self._cooldown_seconds:
            return
        self._last_check = now

        for p in self._proxies:
            if p._health == ProxyHealth.UNHEALTHY:
                # 冷却期满，降级为 degraded（给一次机会）
                p._health = ProxyHealth.DEGRADED
                p._failures = p._failures // 2  # 减半失败计数
                logger.info(f"Proxy {p.url[:30]}... recovered from cooldown (degraded)")

    def get_stats(self) -> Dict:
        """获取代理池统计"""
        return {
            "total": len(self._proxies),
            "healthy": sum(1 for p in self._proxies if p._health == ProxyHealth.HEALTHY),
            "degraded": sum(1 for p in self._proxies if p._health == ProxyHealth.DEGRADED),
            "unhealthy": sum(1 for p in self._proxies if p._health == ProxyHealth.UNHEALTHY),
            "proxies": [
                {
                    "url": p.url[:30] + "...",
                    "health": p._health.value,
                    "failures": p._failures,
                    "successes": p._successes,
                    "failure_rate": round(p.failure_rate, 3),
                }
                for p in self._proxies
            ],
        }

    def reset_all(self) -> None:
        """重置所有代理健康状态"""
        for p in self._proxies:
            p.reset_health()
        self._index = 0
