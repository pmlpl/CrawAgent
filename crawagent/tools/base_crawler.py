"""基础爬虫核心实现 - Phase 1

使用 httpx + BeautifulSoup + UA 伪装，抓取静态页面。

此文件仅保留核心基础类，其他功能已拆分到:
- image_utils.py: 图片提取与下载
- wallpaper_crawler.py: 壁纸爬虫
- video_crawler.py: 视频元数据爬虫
- douyin_crawler.py: 抖音爬虫
"""
from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config.settings import get_logger
from .utils import random_user_agent, _UA_POOL as _UA_POOL_IMPORTED

logger = get_logger(__name__)


# 默认请求头：模拟真实浏览器
_DEFAULT_HEADERS = {
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


def _build_headers(url: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """构建更真实的请求头，每次请求稍微随机化"""
    headers = dict(_DEFAULT_HEADERS)

    # 随机选择 UA
    headers["User-Agent"] = random.choice(_UA_POOL_IMPORTED)

    # 根据 URL 派生 Referer
    try:
        parsed = urlparse(url)
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
    except Exception as e:
        logger.debug(f"构建 Referer 失败: {e}")

    # 随机化 Accept-Language（增加多样性）
    lang_variants = [
        "zh-CN,zh;q=0.9,en;q=0.8",
        "zh-CN,zh-Hans;q=0.9,en-US;q=0.8,en;q=0.7",
        "zh,zh-CN;q=0.9,en-US;q=0.8,en;q=0.7",
        "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6",
    ]
    headers["Accept-Language"] = random.choice(lang_variants)

    if extra:
        headers.update(extra)

    return headers


# ============================================================
# 数据结构
# ============================================================

@dataclass
class CrawlResult:
    """单次爬取的结果"""
    url: str
    success: bool = False
    status_code: int = 0
    html: str = ""
    title: str = ""
    text: str = ""               # 页面纯文本
    links: list[tuple[str, str]] = field(default_factory=list)
    data: list[dict[str, Any]] = field(default_factory=list)
    strategy: str = ""           # basic / browser / basic_fallback
    error: str = ""
    xhr_data: list[dict[str, Any]] = field(default_factory=list)  # 捕获的 XHR/Fetch 请求数据


# ============================================================
# 基础爬虫
# ============================================================

class BaseCrawler:
    """Phase 1 基础爬虫

    - HTTP 请求 (httpx)
    - 随机 UA 伪装
    - 失败重试
    """

    def __init__(
        self,
        timeout: float = 15.0,
        max_retries: int = 2,
        follow_redirects: bool = True,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.follow_redirects = follow_redirects

    def fetch(self, url: str, *, extra_headers: dict[str, str] | None = None) -> CrawlResult:
        result = CrawlResult(url=url)

        for attempt in range(self.max_retries + 1):
            headers = _build_headers(url, extra_headers)
            try:
                with httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=self.follow_redirects,
                ) as client:
                    resp = client.get(url, headers=headers)
                    result.status_code = resp.status_code
                    resp.raise_for_status()
                    result.html = resp.text
                    result.success = True
                    break
            except httpx.HTTPStatusError as e:
                result.error = f"HTTP {e.response.status_code}: {e}"
            except httpx.RequestError as e:
                result.error = f"请求错误: {e}"
            except Exception as e:
                result.error = f"未知错误: {e}"

        if result.success:
            self._parse_basic(result)

        return result

    @staticmethod
    def _parse_basic(result: CrawlResult) -> None:
        if not result.html:
            return
        soup = BeautifulSoup(result.html, "html.parser")

        title_tag = soup.find("title")
        result.title = title_tag.get_text(strip=True) if title_tag else ""

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        result.text = soup.get_text(separator="\n", strip=True)

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            text = a.get_text(strip=True) or href
            result.links.append((href, text))

    @staticmethod
    def extract_article_list(html: str, limit: int = 30) -> list[dict[str, Any]]:
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")

        results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            url = a["href"].strip()
            if len(title) < 5:
                continue
            if url.startswith(("#", "javascript:", "mailto:")):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({"title": title, "url": url, "rank": len(results) + 1})
            if len(results) >= limit:
                return results

        return results

    @staticmethod
    def extract_by_css(html: str, selector: str, limit: int = 50) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        return [el.get_text(strip=True) for el in soup.select(selector)[:limit]]


# ============================================================
# URL 检测
# ============================================================

# URL 匹配正则（支持中文引号和各种特殊字符）
# 排除的字符包括：空格、英文引号、中文全角引号、中文逗号、书名号、尖括号等
_SIMPLE_URL_RE = re.compile(
    r"(?i)\bhttps?://[^\s\"'""''「」『』<>，。！？、；：（)]+|www\.[^\s\"'""''「」『』<>，。！？、；：（)]+",
)


def extract_urls(text: str) -> list[str]:
    """从文本中提取 URL，去重保序。"""
    matches = _SIMPLE_URL_RE.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for m in matches:
        url = m.strip().rstrip(".,;:!?)\"']，。！？、；：）」』\"'")
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def looks_like_url(text: str) -> bool:
    text = text.strip()
    return bool(_SIMPLE_URL_RE.search(text)) or text.startswith(
        ("http://", "https://", "www.")
    )


# ============================================================
# 从各模块重新导出，保持向后兼容
# ============================================================

# 图片工具
from .image_utils import (
    extract_image_urls,
    download_images,
)

# 壁纸爬虫
from .wallpaper_crawler import (
    WallpaperItem,
    smart_scrape_wallpapers,
)

# 视频爬虫
from .video_crawler import (
    VideoItem,
    extract_video_metadata,
    smart_scrape_videos,
)

# 抖音爬虫
from .douyin_crawler import (
    DouyinVideoItem,
    scrape_douyin_search,
    scrape_douyin_video,
)

__all__ = [
    # 核心类
    "BaseCrawler",
    "CrawlResult",
    # URL 工具
    "extract_urls",
    "looks_like_url",
    # 图片工具
    "extract_image_urls",
    "download_images",
    # 壁纸
    "WallpaperItem",
    "smart_scrape_wallpapers",
    # 视频
    "VideoItem",
    "extract_video_metadata",
    "smart_scrape_videos",
    # 抖音
    "DouyinVideoItem",
    "scrape_douyin_search",
    "scrape_douyin_video",
]
