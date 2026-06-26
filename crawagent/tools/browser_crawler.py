"""BrowserCrawler - Playwright 浏览器自动化爬虫 (Phase 2)

使用 Playwright 实现动态页面的爬取，支持:
- 无头/有头模式切换
- 智能等待 DOM 渲染
- 滚动加载（无限滚动页面）
- 点击"加载更多"按钮
- 反自动化指纹伪装
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from ..config.settings import get_logger
from .base_crawler import CrawlResult

logger = get_logger(__name__)

try:
    from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None  # type: ignore
    Browser = None  # type: ignore
    Page = None  # type: ignore
    BrowserContext = None  # type: ignore


# 常见"加载更多"按钮选择器
_LOAD_MORE_SELECTORS = [
    "button:has-text('加载更多')",
    "a:has-text('加载更多')",
    "button:has-text('加载')",
    "button:has-text('更多')",
    "[class*='load-more']",
    "[class*='loadMore']",
    "[class*='load_more']",
    "[id*='load-more']",
    "[id*='loadMore']",
    "button:has-text('More')",
    "a:has-text('More')",
    "[class*='more-btn']",
]


def playwright_available() -> bool:
    """检查 Playwright 是否可用（包括浏览器是否已安装）"""
    if not _PLAYWRIGHT_AVAILABLE:
        return False

    try:
        from playwright.sync_api import sync_playwright
        import os
        
        with sync_playwright() as p:
            exe_path = p.chromium.executable_path
            if exe_path and os.path.exists(exe_path):
                return True
    except Exception:
        pass
    return False


# 视频网站域名列表（用于自动切换轻量级策略）
_VIDEO_SITES = (
    "youku.com", "v.youku.com", "player.youku.com",
    "bilibili.com", "www.bilibili.com", "m.bilibili.com",
    "v.qq.com", "film.qq.com", "m.v.qq.com",
    "iqiyi.com", "www.iqiyi.com", "m.iqiyi.com",
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "douyin.com", "www.douyin.com", "v.douyin.com",
    "tudou.com", "sohu.com", "tv.sohu.com",
    "mgtv.com", "www.mgtv.com",
)


class BrowserCrawler:
    """Playwright 浏览器爬虫

    用法:
        crawler = BrowserCrawler(headless=True)
        result = crawler.fetch("https://example.com")

        # 针对视频/动态网站，可指定 wait_until 策略:
        result = crawler.fetch(url, wait_until="domcontentloaded")
    """

    def __init__(
        self,
        headless: bool = True,
        timeout: float = 30.0,
        scroll_pause: float = 1.2,
        max_scrolls: int = 5,
    ):
        """
        Args:
            headless: 是否无头模式（不显示浏览器窗口）
            timeout: 页面加载超时（秒）- 可在 fetch() 中覆盖
            scroll_pause: 滚动后等待时间（秒）
            max_scrolls: 最大滚动次数（视频/轻量模式会自动置 0）
        """
        if not _PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright 未安装。请运行: pip install playwright && playwright install chromium"
            )

        self.headless = headless
        self.timeout = timeout
        self.scroll_pause = scroll_pause
        self.max_scrolls = max_scrolls

        self._playwright = None
        self._browser: Any = None
        self._context: Any = None

    # ---- URL 检测工具 ----
    @staticmethod
    def looks_like_video_site(url: str) -> bool:
        """判断 URL 是否为视频网站（用于自动选择轻量爬取策略）"""
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return False
        return any(site == host or host.endswith("." + site) or host in site
                   for site in _VIDEO_SITES)

    def _ensure_browser(self) -> None:
        """确保浏览器已启动"""
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-infobars",
                    "--window-size=1920,1080",
                    "--start-maximized",
                ],
            )
            self._context = self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )

    def fetch(self, url: str, *, extra_headers: dict[str, str] | None = None,
              wait_until: str | None = None, timeout: float | None = None,
              light_mode: bool | None = None, capture_xhr: bool = False) -> CrawlResult:
        """执行浏览器爬取

        Args:
            url: 目标 URL
            extra_headers: 额外的请求头
            wait_until: 等待策略: "domcontentloaded" | "load" | "networkidle"
                        (默认自动判断：视频网站用 domcontentloaded, 其他用 networkidle)
            timeout: 超时（秒）- 默认用 self.timeout
            light_mode: 轻量模式（不滚动、不点击加载更多）
                        (默认自动判断：视频网站自动启用)
            capture_xhr: 是否捕获 XHR/Fetch 请求数据（用于提取视频流等）

        Returns:
            CrawlResult: 爬取结果
        """
        result = CrawlResult(url=url)

        # 自动决定模式：视频网站用更轻量的策略避免超时
        is_video = self.looks_like_video_site(url)
        if wait_until is None:
            wait_until = "domcontentloaded" if is_video else "networkidle"
        if timeout is None:
            timeout = 15.0 if is_video else self.timeout
        if light_mode is None:
            light_mode = is_video

        result.strategy = f"browser:{wait_until}{':light' if light_mode else ''}"

        try:
            self._ensure_browser()
            page = self._context.new_page()

            # 设置额外请求头
            if extra_headers:
                page.set_extra_http_headers(extra_headers)

            # 捕获 XHR/Fetch 请求数据
            xhr_data: list[dict[str, Any]] = []
            if capture_xhr:
                def handle_response(response):
                    try:
                        req = response.request
                        url = req.url
                        method = req.method
                        content_type = response.headers.get("content-type", "")

                        is_json = "json" in content_type.lower()
                        is_api = any(keyword in url.lower() for keyword in
                                   ("api", "video", "douyin", "stream", "media", "play",
                                    "search", "aweme", "item", "data", "query"))

                        if is_json or is_api or len(url) > 50:
                            try:
                                resp_text = response.text()
                                if resp_text and len(resp_text) > 20:
                                    xhr_data.append({
                                        "url": url,
                                        "method": method,
                                        "status": response.status,
                                        "content_type": content_type,
                                        "text": resp_text[:10000],
                                    })
                            except Exception:
                                pass
                    except Exception:
                        pass

                page.on("response", handle_response)

            # 访问页面 - 用指定的等待策略
            # 注意：抖音等网站可能有反爬验证，需要等待 JavaScript 执行完成
            response = page.goto(
                url,
                wait_until=wait_until,
                timeout=timeout * 1000,
            )

            if response:
                result.status_code = response.status

            # 额外短暂等待给首屏 JS 执行
            time.sleep(1.0 if light_mode else self.scroll_pause)

            # 如果启用了 XHR 捕获，再等待一会儿让 API 请求完成
            if capture_xhr:
                time.sleep(2.0)

            # 获取结果（先获取 content，再做其他操作）
            result.html = page.content()
            try:
                result.title = page.title() or ""
            except Exception:
                result.title = ""

            # 轻量模式：跳过滚动加载和点击加载更多，
            # 但仍然尝试滚动一屏获取懒加载内容
            if not light_mode:
                # 执行滚动加载
                self._scroll_to_load(page)

                # 点击"加载更多"按钮
                self._click_load_more(page)

                # 再次滚动到顶部到底部以获取全部内容
                try:
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(0.3)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(0.5)
                except Exception:
                    pass

                # 更新 HTML（包含滚动加载的内容）
                try:
                    result.html = page.content()
                except Exception:
                    pass
            else:
                # 轻量模式：仅做一次浅滚动触发首屏懒加载
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                    time.sleep(0.8)
                    result.html = page.content()
                except Exception:
                    pass

            # 纯文本提取（从 HTML 解析，比 page.inner_text 更可靠）
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(result.html, "html.parser")
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                result.text = soup.get_text(separator="\n", strip=True)
            except Exception:
                try:
                    page.evaluate("document.querySelectorAll('script, style, noscript, iframe').forEach(el => el.remove())")
                    result.text = page.inner_text("body")
                except Exception:
                    result.text = ""

            # 提取链接（轻量模式也做）
            try:
                anchors = page.query_selector_all("a[href]")
                for a in anchors:
                    href = a.get_attribute("href")
                    if href and not href.startswith(("javascript:", "mailto:", "tel:", "#")):
                        text_content = (a.inner_text() or "").strip()
                        result.links.append((href, text_content[:100]))
            except Exception:
                pass

            # 保存捕获的 XHR 数据
            result.xhr_data = xhr_data

            # 如果启用了 XHR 捕获，尝试执行 JavaScript 获取页面数据
            if capture_xhr:
                try:
                    page_data = page.evaluate("""
                        () => {
                            const videos = [];
                            const videoElements = document.querySelectorAll('video');
                            videoElements.forEach(v => {
                                if (v.src && v.src.includes('.mp4')) {
                                    videos.push({
                                        type: 'video_element',
                                        url: v.src,
                                        poster: v.poster || '',
                                    });
                                }
                            });
                            
                            const scripts = document.querySelectorAll('script');
                            scripts.forEach(s => {
                                if (s.textContent && s.textContent.includes('aweme')) {
                                    try {
                                        const regex = /"play_addr"\s*:\s*["']([^"']+\.mp4[^"']*)["']/;
                                        const match = s.textContent.match(regex);
                                        if (match) {
                                            videos.push({ type: 'script', url: match[1] });
                                        }
                                    } catch (e) {}
                                }
                            });
                            
                            for (const key in window) {
                                try {
                                    const val = window[key];
                                    if (val && typeof val === 'object') {
                                        const str = JSON.stringify(val);
                                        if (str.includes('play_addr') && str.includes('.mp4')) {
                                            const regex = /"play_addr"\s*:\s*["']([^"']+\.mp4[^"']*)["']/g;
                                            let match;
                                            while ((match = regex.exec(str)) !== null) {
                                                videos.push({ type: 'window_var', url: match[1] });
                                            }
                                        }
                                    }
                                } catch (e) {}
                            }
                            
                            return videos;
                        }
                    """)
                    if page_data:
                        for item in page_data:
                            if item.get('url') and item['url'] not in [x.get('text', '') for x in xhr_data]:
                                xhr_data.append({
                                    "url": item['url'],
                                    "method": "JS_EXTRACT",
                                    "status": 200,
                                    "content_type": "video/mp4",
                                    "text": item['url'],
                                })
                        result.xhr_data = xhr_data
                except Exception:
                    pass

            result.success = True
            page.close()

        except Exception as e:
            # 如果超时且用的是 networkidle，尝试降级到 domcontentloaded 重试一次
            if "Timeout" in str(e) and wait_until == "networkidle" and not light_mode:
                try:
                    result2 = self.fetch(url, extra_headers=extra_headers,
                                          wait_until="domcontentloaded",
                                          timeout=15.0, light_mode=True)
                    return result2
                except Exception:
                    result.error = f"浏览器爬取失败: {e}"
                    result.success = False
            else:
                result.error = f"浏览器爬取失败: {e}"
                result.success = False

        return result

    def _scroll_to_load(self, page: Any) -> None:
        """滚动页面触发懒加载内容"""
        last_height = 0
        no_change_count = 0

        for i in range(self.max_scrolls):
            try:
                new_height = page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    no_change_count += 1
                    if no_change_count >= 2:
                        break
                else:
                    no_change_count = 0
                last_height = new_height

                # 滚动到底部
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(self.scroll_pause)

            except Exception:
                break

    def _click_load_more(self, page: Any) -> None:
        """尝试点击"加载更多"按钮"""
        for selector in _LOAD_MORE_SELECTORS:
            try:
                elements = page.query_selector_all(selector)
                if elements:
                    for elem in elements[:2]:
                        try:
                            elem.scroll_into_view_if_needed()
                            elem.click(timeout=3000, force=False)
                            time.sleep(self.scroll_pause)
                        except Exception:
                            pass
            except Exception:
                continue

    def screenshot(self, url: str, path: str | None = None) -> bytes | None:
        """截取页面快照"""
        try:
            self._ensure_browser()
            page = self._context.new_page()
            page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
            time.sleep(self.scroll_pause)
            png_bytes = page.screenshot(type="png", full_page=False)
            if path:
                with open(path, "wb") as f:
                    f.write(png_bytes)
            page.close()
            return png_bytes
        except Exception as e:
            logger.error(f"[BrowserCrawler] 截图失败: {e}")
            return None

    def close(self) -> None:
        """关闭浏览器和 Playwright"""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def __enter__(self) -> "BrowserCrawler":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
