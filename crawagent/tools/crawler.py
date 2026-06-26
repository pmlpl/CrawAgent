"""Crawler Facade - 统一爬虫入口 (Phase 2)

自动选择 httpx 或 Playwright，对用户透明:
- 优先使用 httpx（快速）
- 检测到 JS 渲染/反爬时自动切换 Playwright
- 可选: Scrapling 高级模式 (TLS指纹/隐身浏览器/自适应解析)
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from ..config.settings import get_logger
from .base_crawler import BaseCrawler, CrawlResult
from .browser_crawler import BrowserCrawler, playwright_available

logger = get_logger(__name__)

# 高级爬虫（可选）
try:
    from .advanced_crawler import (
        AdvancedCrawler,
        AdaptiveExtractor,
        quick_scrape,
        SCRAPLING_AVAILABLE,
    )
except ImportError:
    AdvancedCrawler = None
    AdaptiveExtractor = None
    quick_scrape = None
    SCRAPLING_AVAILABLE = False

# 视频网站域名列表
_VIDEO_SITES = (
    "youku.com", "bilibili.com", "v.qq.com", "iqiyi.com",
    "youtube.com", "douyin.com", "mgtv.com", "sohu.com",
    "tudou.com",
)


class Crawler:
    """统一爬虫入口

    用法:
        crawler = Crawler()  # 默认无头模式
        result = crawler.fetch("https://example.com")

        # 开启调试（显示浏览器）
        crawler = Crawler(headless=False)
        result = crawler.fetch("https://example.com")
    """

    # JS 框架特征（更全面）
    _JS_FRAMEWORK_INDICATORS = [
        '<div id="app">',
        '<div id="root">',
        '<div id="__next">',
        'id="__nuxt"',
        'data-vue-app',
        'data-reactroot',
        '__NEXT_DATA__',
        '__NUXT__',
        'window.__INITIAL_STATE__',
        'window.__INITIAL_PROPS__',
        'window.__APP_',
        'react-dom',
        'vue.runtime',
        'angular',
        'next.bundle',
        'nuxt.bundle',
    ]

    # 反爬/蜜罐特征关键词
    _ANTI_CRAWL_MARKERS = [
        '404页面失联',
        '页面失联',
        '安全检测',
        '机器人检测',
        'bot detect',
        'captcha',
        'CAPTCHA',
        '请完成验证',
        '访问受限',
        'rate limit',
        'Rate limit',
        'IP被封',
        'IP 被封',
        '异常流量',
        'unusual traffic',
        '请稍候',
        'just a moment',
        'cloudflare',
        'Cloudflare',
        '验证你是真人',
    ]

    def __init__(
        self,
        headless: bool = True,
        timeout: float = 15.0,
        max_retries: int = 2,
        browser_timeout: float = 30.0,
        # 高级模式选项
        use_advanced: bool = False,
        impersonate: str = "chrome",
        stealth_mode: bool = False,
    ):
        """
        Args:
            headless: 浏览器是否无头模式
            timeout: httpx 请求超时（秒）
            max_retries: httpx 最大重试次数
            browser_timeout: Playwright 页面超时（秒）
            use_advanced: 是否启用 Scrapling 高级模式
            impersonate: 浏览器指纹类型（chrome/firefox/edge/safari）
            stealth_mode: 是否使用隐身模式（绕过 Cloudflare 等）
        """
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self.browser_timeout = browser_timeout
        self.use_advanced = use_advanced and SCRAPLING_AVAILABLE
        self.impersonate = impersonate
        self.stealth_mode = stealth_mode

        self._base = BaseCrawler(timeout=timeout, max_retries=max_retries)
        self._browser: BrowserCrawler | None = None
        self._advanced: AdvancedCrawler | None = None
        self._browser_mode: bool | None = None
        self._last_detection: dict[str, bool] = {}

    @property
    def browser(self) -> BrowserCrawler:
        """懒加载浏览器实例"""
        if self._browser is None:
            self._browser = BrowserCrawler(
                headless=self.headless,
                timeout=self.browser_timeout,
            )
        return self._browser

    @property
    def advanced(self) -> AdvancedCrawler | None:
        """懒加载高级爬虫实例"""
        if not SCRAPLING_AVAILABLE:
            return None
        if self._advanced is None:
            self._advanced = AdvancedCrawler(
                timeout=self.timeout * 2,  # 高级模式超时稍长
                impersonate=self.impersonate,
                max_retries=self.max_retries,
            )
        return self._advanced

    @property
    def is_advanced_available(self) -> bool:
        """高级模式是否可用"""
        return SCRAPLING_AVAILABLE

    def set_browser_mode(self, mode: bool | None) -> None:
        """设置浏览器模式

        Args:
            mode: None=自动检测, True=强制使用浏览器, False=只用httpx
        """
        self._browser_mode = mode

    @staticmethod
    def is_video_site(url: str) -> bool:
        """判断 URL 是否为视频网站（用于自动选择轻量爬取策略）"""
        try:
            host = urlparse(url).netloc.lower()
        except ValueError:
            return False
        return any(site in host for site in _VIDEO_SITES)

    @staticmethod
    def is_douyin_search(url: str) -> bool:
        """判断是否为抖音搜索页面"""
        try:
            host = urlparse(url).netloc.lower()
            path = urlparse(url).path.lower()
            query = urlparse(url).query.lower()
        except ValueError:
            return False
        return "douyin.com" in host and ("search" in path or "modal_id" in query)

    @staticmethod
    def is_douyin_video(url: str) -> bool:
        """判断是否为抖音视频详情页 (video/ID 或 /note/ID)"""
        try:
            host = urlparse(url).netloc.lower()
            path = urlparse(url).path.lower()
        except ValueError:
            return False
        if "douyin.com" not in host:
            return False
        return bool(re.search(r'/video/\d+', path)) or bool(re.search(r'/note/\d+', path))

    def fetch(self, url: str, *, extra_headers: dict[str, str] | None = None) -> CrawlResult:
        """爬取 URL

        自动选择最佳爬取策略:
        1. 先用 httpx 快速尝试
        2. 检测到需要浏览器时自动切换 Playwright
        3. 检测到反爬时优先使用高级模式 (Scrapling)
        4. 视频网站专用策略: 浏览器轻量模式 (domcontentloaded, 不滚动)
        5. 抖音搜索页面专用策略: 浏览器 + XHR 捕获
        6. 抖音视频详情页: 浏览器 + XHR 捕获 + 视频提取

        Args:
            url: 目标 URL
            extra_headers: 额外的请求头

        Returns:
            CrawlResult: 爬取结果
        """
        is_video = self.is_video_site(url)
        is_douyin_search = self.is_douyin_search(url)
        is_douyin_video = self.is_douyin_video(url)

        # 如果强制使用 httpx
        if self._browser_mode is False:
            return self._base.fetch(url, extra_headers=extra_headers)

        # 1. 先用 httpx 快速尝试
        result = self._base.fetch(url, extra_headers=extra_headers)

        # 2. 检测是否需要浏览器（视频网站/抖音直接用浏览器）
        need_browser = is_video or is_douyin_search or is_douyin_video or self._need_browser(result)

        if need_browser and self._browser_mode is not False:
            # 3. 如果检测到反爬/Cloudflare，优先尝试高级模式
            if self._detect_anti_bot(result) and self.use_advanced and self.advanced:
                try:
                    adv_result = self.advanced.fetch(
                        url,
                        extra_headers=extra_headers,
                        stealth=self.stealth_mode,
                        dynamic=not self.stealth_mode,
                    )
                    if adv_result.success:
                        # 转换高级结果为标准结果
                        result.status_code = adv_result.status_code
                        result.html = adv_result.html
                        result.title = adv_result.title
                        result.text = adv_result.text
                        result.success = True
                        result.strategy = f"advanced:{adv_result.strategy}"
                        result.error = ""
                        return result
                except Exception as e:
                    logger.debug(f"高级模式爬取失败: {e}")  # 回退到普通浏览器模式

            # 检查 Playwright 是否可用
            if not playwright_available():
                result.error = (
                    "检测到网站需要浏览器渲染，但 Playwright 浏览器未安装。\n"
                    "请以管理员权限运行: playwright install chromium\n"
                    "或使用 /browser off 禁用浏览器模式"
                )
                result.strategy = "browser_unavailable"
                return result

            # 关闭浏览器实例（确保 clean start）
            if self._browser is not None:
                self._browser.close()
                self._browser = None

            # 切换到 Playwright
            try:
                # 抖音搜索页面: 用浏览器模式 + XHR 捕获 + 滚动加载视频数据
                if is_douyin_search:
                    browser_result = self.browser.fetch(
                        url,
                        extra_headers=extra_headers,
                        wait_until="domcontentloaded",
                        timeout=30.0,
                        light_mode=False,
                        capture_xhr=True,
                    )
                    browser_result.strategy = "browser:douyin-search"
                    return browser_result

                # 抖音视频详情页: 专门提取视频流地址
                if is_douyin_video:
                    browser_result = self.browser.fetch(
                        url,
                        extra_headers=extra_headers,
                        wait_until="load",
                        timeout=45.0,
                        light_mode=False,
                        capture_xhr=True,
                    )
                    browser_result.strategy = "browser:douyin-video"
                    return browser_result

                # 视频网站: 用轻量模式 (domcontentloaded, 避免超时)
                if is_video:
                    browser_result = self.browser.fetch(
                        url,
                        extra_headers=extra_headers,
                        wait_until="domcontentloaded",
                        timeout=15.0,
                        light_mode=True,
                    )
                    browser_result.strategy = "browser:video-light"
                    return browser_result
                # 普通浏览器爬取
                browser_result = self.browser.fetch(url, extra_headers=extra_headers)
                browser_result.strategy = "browser"
                return browser_result
            except TimeoutError as e:
                logger.debug(f"浏览器爬取超时: {e}")
                result.error = f"浏览器爬取超时: {e}（已退回基础爬虫结果）"
                result.strategy = "basic_fallback"
                return result
            except Exception as e:
                logger.debug(f"浏览器爬取失败: {e}")
                result.error = f"浏览器爬取失败: {e}（已退回基础爬虫结果）"
                result.strategy = "basic_fallback"
                return result

        result.strategy = "basic"
        return result

    def _detect_anti_bot(self, result: CrawlResult) -> bool:
        """检测是否为反爬/Cloudflare 保护页面"""
        if not result.success:
            return False

        combined = (result.title or "") + " " + (result.text or "")
        anti_bot_markers = [
            "cloudflare",
            "Cloudflare",
            "checking your browser",
            "please wait",
            "just a moment",
            "attention required",
            "access denied",
            "非人类行为",
            "安全验证",
            "turnstile",
            "hCaptcha",
        ]
        return any(marker in combined for marker in anti_bot_markers)

    def _need_browser(self, result: CrawlResult) -> bool:
        """检测是否需要浏览器渲染

        多维度检测:
        1. 强制模式检查
        2. 状态码检测（403/418/429）
        3. 内容过短（纯文本 < 500 字符）
        4. 文本/HTML 比率过低（JS 渲染特征）
        5. JS 框架特征关键词
        6. 反爬/蜜罐特征关键词（标题+页面文本）
        """
        detection: dict[str, bool] = {}

        # 如果强制使用浏览器
        if self._browser_mode is True:
            detection["force"] = True
            self._last_detection = detection
            return True

        if not result.success:
            self._last_detection = {"failed": True, "result": False}
            return False

        # 状态码检测
        detection["bad_status"] = result.status_code in (403, 418, 429, 503, 521)

        # 内容过短检测
        detection["too_short"] = len(result.text) < 500

        # 文本/HTML 比率检测（JS 渲染页面: HTML 很大，text 很小）
        html_len = len(result.html)
        text_len = len(result.text)
        if html_len > 10000:
            ratio = text_len / html_len if html_len > 0 else 1.0
            detection["low_ratio"] = ratio < 0.01  # 文本率 < 1% 很可疑
        else:
            detection["low_ratio"] = False

        # JS 框架特征检测
        html_lower = result.html.lower()
        detection["js_framework"] = any(
            indicator.lower() in html_lower for indicator in self._JS_FRAMEWORK_INDICATORS
        )

        # 反爬/蜜罐特征检测（看标题和文本）
        combined = (result.title or "") + " " + result.text
        detection["anti_crawl"] = any(
            marker in combined for marker in self._ANTI_CRAWL_MARKERS
        )

        self._last_detection = detection

        # 只要任一维度命中就切换浏览器
        return any(detection.values())

    def close(self) -> None:
        """关闭所有爬虫资源"""
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        # 高级爬虫不需要显式关闭

    def enable_advanced_mode(self, stealth: bool = False) -> bool:
        """启用高级模式 (Scrapling)

        Args:
            stealth: 是否使用隐身模式

        Returns:
            是否成功启用
        """
        if not SCRAPLING_AVAILABLE:
            return False
        self.use_advanced = True
        self.stealth_mode = stealth
        return True

    def disable_advanced_mode(self) -> None:
        """禁用高级模式"""
        self.use_advanced = False
        self.stealth_mode = False

    def set_impersonate(self, browser: str) -> bool:
        """设置浏览器指纹

        Args:
            browser: 浏览器类型 (chrome/firefox/edge/safari)

        Returns:
            是否成功设置
        """
        valid_browsers = ["chrome", "firefox", "edge", "safari"]
        if browser.lower() in valid_browsers:
            self.impersonate = browser.lower()
            # 重置高级实例以应用新配置
            self._advanced = None
            return True
        return False

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
