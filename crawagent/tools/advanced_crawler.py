"""高级爬虫核心 - 基于 Scrapling

特性:
- TLS 指纹伪装 (curl_cffi)
- 隐身浏览器 (绕过 Cloudflare 等)
- 自适应解析 (网站结构变化时自动适应)
- XHR 捕获
- 代理轮换支持
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin


def _extract_title_from_html(html: str) -> str:
    """从 HTML 中提取标题"""
    if not html:
        return ""
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""

# Scrapling 核心组件
try:
    from scrapling.fetchers import Fetcher, StealthyFetcher, DynamicFetcher
    from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator
    SCRAPLING_AVAILABLE = True
except ImportError:
    SCRAPLING_AVAILABLE = False
    Fetcher = None
    StealthyFetcher = None
    DynamicFetcher = None
    ProxyRotator = None

from .base_crawler import CrawlResult


# ============================================================
# 数据结构
# ============================================================

@dataclass
class AdvancedResult:
    """高级爬取结果（扩展自 CrawlResult）"""
    url: str
    success: bool = False
    status_code: int = 0
    html: str = ""
    title: str = ""
    text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)
    data: list[dict[str, Any]] = field(default_factory=list)
    strategy: str = ""           # fetcher / stealth / dynamic / adaptive
    error: str = ""
    captured_xhr: list[dict] = field(default_factory=list)  # XHR 捕获
    meta: dict[str, Any] = field(default_factory=dict)       # 元数据


# ============================================================
# TLS 指纹伪装配置
# ============================================================

# 支持的浏览器指纹类型
BROWSER_IMPERSONATES = [
    "chrome",
    "chrome110",
    "chrome116",
    "chrome120",
    "chrome126",
    "edge",
    "edge101",
    "edge110",
    "edge120",
    "firefox",
    "firefox110",
    "firefox120",
    "safari",
    "safari15_5",
    "safari17_0",
    "ios15_5",
    "ios16_0",
    "android",
]


# ============================================================
# 高级爬虫类
# ============================================================

class AdvancedCrawler:
    """基于 Scrapling 的高级爬虫

    支持:
    - TLS 指纹伪装（模拟 Chrome/Firefox/Safari）
    - 隐身浏览器（绕过 Cloudflare 等反爬）
    - 自适应解析（网站结构变化时自动适应）
    - XHR 捕获
    - 代理轮换
    """

    def __init__(
        self,
        timeout: float = 30.0,
        impersonate: str = "chrome",
        max_retries: int = 2,
        follow_redirects: bool = True,
        proxies: list[str] | None = None,
    ):
        """
        Args:
            timeout: 请求超时（秒）
            impersonate: 浏览器指纹类型（chrome/firefox/edge/safari 等）
            max_retries: 最大重试次数
            follow_redirects: 是否跟随重定向
            proxies: 代理列表（用于轮换）
        """
        if not SCRAPLING_AVAILABLE:
            raise ImportError(
                "Scrapling 未安装。请运行: pip install scrapling curl_cffi"
            )

        self.timeout = timeout
        self.impersonate = impersonate
        self.max_retries = max_retries
        self.follow_redirects = follow_redirects

        # 代理轮换器
        self._proxy_rotator: ProxyRotator | None = None
        if proxies:
            self._proxy_rotator = ProxyRotator(proxies)

        # 当前使用的代理
        self._current_proxy: str | None = None

    def _get_proxy(self) -> str | None:
        """获取当前代理（轮换）"""
        if self._proxy_rotator:
            self._current_proxy = self._proxy_rotator.get_next()
            return self._current_proxy
        return None

    def _report_proxy(self, proxy: str | None, success: bool) -> None:
        """报告代理使用结果"""
        if self._proxy_rotator and proxy:
            self._proxy_rotator.report_used_proxy(proxy, success)

    def fetch(
        self,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
        stealth: bool = False,
        dynamic: bool = False,
        capture_xhr: bool = False,
        adaptive: bool = False,
        headless: bool = True,
        network_idle: bool = False,
    ) -> AdvancedResult:
        """执行高级爬取

        Args:
            url: 目标 URL
            extra_headers: 额外的请求头
            stealth: 是否使用隐身模式（绕过 Cloudflare 等）
            dynamic: 是否使用动态浏览器（Playwright）
            capture_xhr: 是否捕获 XHR 请求
            adaptive: 是否启用自适应解析
            headless: 浏览器是否无头
            network_idle: 等待网络空闲（仅 dynamic）

        Returns:
            AdvancedResult: 爬取结果
        """
        result = AdvancedResult(url=url)

        # 准备请求参数
        kwargs: dict[str, Any] = {
            "timeout": self.timeout,
            "impersonate": self.impersonate,
        }

        if extra_headers:
            kwargs["headers"] = extra_headers

        if not self.follow_redirects:
            kwargs["max_redirects"] = 0

        # 获取代理
        proxy = self._get_proxy()
        if proxy:
            kwargs["proxies"] = proxy

        # 选择 fetcher
        if stealth:
            FetcherClass = StealthyFetcher
            result.strategy = "stealth"
        elif dynamic:
            FetcherClass = DynamicFetcher
            result.strategy = "dynamic"
        else:
            FetcherClass = Fetcher
            result.strategy = "fetcher"

        # 动态模式特殊参数
        if dynamic:
            kwargs["headless"] = headless
            kwargs["network_idle"] = network_idle
            if capture_xhr:
                kwargs["capture_xhr"] = True

        # 重试机制
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = FetcherClass.get(url, **kwargs)
                result.status_code = response.status
                result.html = response.text
                # 从 HTML 中提取标题
                result.title = _extract_title_from_html(response.text)
                result.text = response.text
                result.success = True

                # XHR 捕获
                if hasattr(response, "captured_xhr") and response.captured_xhr:
                    result.captured_xhr = [
                        {"url": x.url, "method": x.method, "response": x.text}
                        for x in response.captured_xhr
                    ]

                # 元数据
                result.meta = {
                    "cookies": dict(response.cookies) if response.cookies else {},
                    "headers": dict(response.headers) if response.headers else {},
                    "proxy": proxy,
                    "impersonate": self.impersonate,
                }

                self._report_proxy(proxy, True)
                break

            except Exception as e:
                last_error = str(e)
                self._report_proxy(proxy, False)

                # 如果是代理错误且有其他代理，尝试下一个
                if proxy and self._proxy_rotator:
                    proxy = self._get_proxy()
                    if proxy:
                        kwargs["proxies"] = proxy
                        continue

        if not result.success:
            result.error = last_error

        return result


# ============================================================
# 自适应解析器
# ============================================================

class AdaptiveExtractor:
    """自适应内容提取器

    使用 Scrapling 的 Selector API，支持 CSS/XPath/Regex 选择器，
    并且在网站结构变化时能够自动适应。

    示例:
        extractor = AdaptiveExtractor(html)
        # 基础选择器
        titles = extractor.css('.title')
        # 自适应选择器（网站变化后仍能找到）
        items = extractor.css('.product', adaptive=True)
        # XPath 选择器
        links = extractor.xpath('//a[@class="link"]/@href')
        # Regex 选择器
        emails = extractor.regex(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
    """

    def __init__(self, html: str | bytes):
        """初始化解析器

        Args:
            html: HTML 文本或字节
        """
        if not SCRAPLING_AVAILABLE:
            raise ImportError(
                "Scrapling 未安装。请运行: pip install scrapling curl_cffi"
            )

        try:
            from scrapling.parser import Selector
            self._selector = Selector(html)
        except Exception as e:
            raise ValueError(f"无法解析 HTML: {e}")

    def css(self, selector: str, *, adaptive: bool = False, **kwargs) -> list[str]:
        """CSS 选择器提取

        Args:
            selector: CSS 选择器
            adaptive: 是否启用自适应（网站结构变化时仍能找到）
            **kwargs: 传递给 Scrapling 的其他参数

        Returns:
            匹配元素的文本列表
        """
        try:
            if adaptive:
                elements = self._selector.css(selector, adaptive=True, **kwargs)
            else:
                elements = self._selector.css(selector, **kwargs)

            return [el.text if hasattr(el, 'text') else str(el) for el in elements]
        except Exception:
            return []

    def css_all(self, selector: str, *, adaptive: bool = False, **kwargs) -> list[Any]:
        """CSS 选择器提取（返回完整元素）"""
        try:
            if adaptive:
                return self._selector.css(selector, adaptive=True, **kwargs)
            return self._selector.css(selector, **kwargs)
        except Exception:
            return []

    def xpath(self, xpath: str, **kwargs) -> list[str]:
        """XPath 选择器提取"""
        try:
            elements = self._selector.xpath(xpath, **kwargs)
            return [el.text if hasattr(el, 'text') else str(el) for el in elements]
        except Exception:
            return []

    def xpath_all(self, xpath: str, **kwargs) -> list[Any]:
        """XPath 选择器提取（返回完整元素）"""
        try:
            return self._selector.xpath(xpath, **kwargs)
        except Exception:
            return []

    def regex(self, pattern: str, **kwargs) -> list[str]:
        """正则表达式提取"""
        try:
            return self._selector.re(pattern, **kwargs)
        except Exception:
            # 回退到 Python re
            import re
            return re.findall(pattern, self._selector.page_content, **kwargs)

    def find_all(self, **kwargs) -> list[dict[str, Any]]:
        """查找所有可迭代元素"""
        try:
            results = []
            for item in self._selector.all(**kwargs):
                item_dict = {}
                # 尝试提取常见字段
                if hasattr(item, 'text'):
                    item_dict['text'] = item.text
                if hasattr(item, 'attributes'):
                    item_dict['attributes'] = dict(item.attributes)
                if hasattr(item, 'html'):
                    item_dict['html'] = str(item.html)
                results.append(item_dict)
            return results
        except Exception:
            return []

    @property
    def html(self) -> str:
        """获取完整 HTML"""
        try:
            return str(self._selector.html_content) if hasattr(self._selector, 'html_content') else str(self._selector)
        except Exception:
            return ""

    @property
    def text(self) -> str:
        """获取纯文本"""
        try:
            return self._selector.get_all_text() if hasattr(self._selector, 'get_all_text') else ""
        except Exception:
            return ""

    @property
    def title(self) -> str:
        """获取页面标题"""
        try:
            # 尝试从 title 标签提取
            title_el = self._selector.xpath('//title/text()')
            if title_el:
                return title_el[0] if isinstance(title_el[0], str) else str(title_el[0])
        except Exception:
            pass
        return ""


# ============================================================
# 便捷函数
# ============================================================

def quick_scrape(
    url: str,
    *,
    stealth: bool = False,
    dynamic: bool = False,
    adaptive: bool = False,
    impersonate: str = "chrome",
    timeout: float = 30.0,
) -> AdvancedResult:
    """快速爬取（单次请求）

    Args:
        url: 目标 URL
        stealth: 是否使用隐身模式
        dynamic: 是否使用动态浏览器
        adaptive: 是否启用自适应解析
        impersonate: 浏览器指纹
        timeout: 超时

    Returns:
        AdvancedResult: 爬取结果
    """
    if not SCRAPLING_AVAILABLE:
        return AdvancedResult(
            url=url,
            success=False,
            error="Scrapling 未安装"
        )

    crawler = AdvancedCrawler(timeout=timeout, impersonate=impersonate)
    result = crawler.fetch(url, stealth=stealth, dynamic=dynamic)

    # 如果启用自适应解析，返回解析后的结果
    if adaptive and result.success:
        extractor = AdaptiveExtractor(result.html)
        result.text = extractor.text

    return result


def extract_with_selectors(
    html: str,
    selectors: dict[str, str],
    *,
    adaptive: bool = False,
) -> dict[str, list[str]]:
    """使用选择器批量提取内容

    Args:
        html: HTML 文本
        selectors: 选择器字典，格式 {字段名: 选择器}
        adaptive: 是否启用自适应

    Returns:
        提取结果字典

    示例:
        result = extract_with_selectors(html, {
            "titles": ".product-title",
            "prices": ".price",
            "links": "a::attr(href)",
        })
    """
    if not SCRAPLING_AVAILABLE:
        return {k: [] for k in selectors}

    extractor = AdaptiveExtractor(html)
    results = {}

    for field_name, selector in selectors.items():
        # 处理伪选择器
        if "::attr(" in selector:
            # 属性选择器，如 a::attr(href)
            match = re.match(r"(.+)::attr\((.+)\)", selector)
            if match:
                tag_selector, attr_name = match.groups()
                elements = extractor.css_all(tag_selector, adaptive=adaptive)
                results[field_name] = [
                    el.get(attr_name) or el.get("attributes", {}).get(attr_name, "")
                    for el in elements
                ]
        else:
            results[field_name] = extractor.css(selector, adaptive=adaptive)

    return results


# ============================================================
# 代理管理器
# ============================================================

class ProxyManager:
    """代理管理器

    支持:
    - 代理池管理
    - 自动轮换
    - 失败重试
    - 成功率统计
    """

    def __init__(self, proxies: list[str] | None = None):
        """初始化

        Args:
            proxies: 代理列表
        """
        self._rotator = ProxyRotator(proxies) if proxies and SCRAPLING_AVAILABLE else None
        self._custom_proxies = proxies or []
        self._stats: dict[str, dict[str, Any]] = {}

    def add_proxy(self, proxy: str) -> None:
        """添加代理"""
        if proxy not in self._custom_proxies:
            self._custom_proxies.append(proxy)

    def get_proxy(self) -> str | None:
        """获取代理"""
        if self._rotator:
            return self._rotator.get_next()
        if self._custom_proxies:
            import random
            return random.choice(self._custom_proxies)
        return None

    def report_result(self, proxy: str | None, success: bool) -> None:
        """报告代理使用结果"""
        if not proxy:
            return

        if proxy not in self._stats:
            self._stats[proxy] = {"success": 0, "failure": 0}

        if success:
            self._stats[proxy]["success"] += 1
        else:
            self._stats[proxy]["failure"] += 1

    def get_stats(self) -> dict[str, dict[str, Any]]:
        """获取代理统计"""
        return self._stats.copy()

    def get_best_proxy(self, min_success_rate: float = 0.8) -> str | None:
        """获取最佳代理"""
        if not self._stats:
            return self.get_proxy()

        best = None
        best_rate = 0.0

        for proxy, stats in self._stats.items():
            total = stats["success"] + stats["failure"]
            if total >= 3:  # 至少尝试 3 次
                rate = stats["success"] / total
                if rate >= min_success_rate and rate > best_rate:
                    best_rate = rate
                    best = proxy

        return best or self.get_proxy()
