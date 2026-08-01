"""视频 URL 提取器

从 HTML 中提取视频资源：
- <video> 标签的 src / poster
- <source> 标签的 src + type
- <iframe> 嵌入视频（YouTube/Bilibili 等）
- data-video / data-src 属性中的视频 URL
- m3u8/mp4 直链（正则扫描）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from loguru import logger


@dataclass
class VideoInfo:
    """提取到的视频信息"""
    url: str = ""
    video_type: str = ""  # mp4 / m3u8 / webm / iframe_embed
    quality: str = ""  # 720p / 1080p 等
    source_tag: str = ""  # video / source / iframe / data-attr / regex
    poster: str = ""  # 封面图 URL
    mime_type: str = ""  # video/mp4 等
    title: str = ""


@dataclass
class VideoExtractionResult:
    """视频提取结果"""
    videos: List[VideoInfo] = field(default_factory=list)
    iframes: List[VideoInfo] = field(default_factory=list)
    m3u8_urls: List[str] = field(default_factory=list)
    raw_mp4_urls: List[str] = field(default_factory=list)

    @property
    def has_videos(self) -> bool:
        return bool(self.videos or self.iframes or self.m3u8_urls or self.raw_mp4_urls)

    @property
    def total_count(self) -> int:
        return len(self.videos) + len(self.iframes) + len(self.m3u8_urls) + len(self.raw_mp4_urls)


# m3u8 / mp4 / webm URL 正则
_M3U8_PATTERN = re.compile(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', re.IGNORECASE)
_MP4_PATTERN = re.compile(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', re.IGNORECASE)
_WEBM_PATTERN = re.compile(r'https?://[^\s"\'<>]+\.webm[^\s"\'<>]*', re.IGNORECASE)

# 已知嵌入视频平台的 iframe 模式
_EMBED_PATTERNS = {
    "youtube": re.compile(r'youtube\.com/embed/|youtu\.be/', re.IGNORECASE),
    "bilibili": re.compile(r'player\.bilibili\.com|bilibili\.com/blackboard/html5player', re.IGNORECASE),
    "vimeo": re.compile(r'player\.vimeo\.com', re.IGNORECASE),
    "youku": re.compile(r'player\.youku\.com', re.IGNORECASE),
    "tencent": re.compile(r'v\.qq\.com/iframe', re.IGNORECASE),
    "iqiyi": re.compile(r'player\.iqiyi\.com', re.IGNORECASE),
}


class VideoExtractor:
    """视频 URL 提取器

    用法:
        extractor = VideoExtractor()
        result = extractor.extract(html, base_url="https://example.com/page")
        for video in result.videos:
            print(video.url, video.video_type)
    """

    def extract(self, html: str, base_url: str = "") -> VideoExtractionResult:
        """从 HTML 中提取所有视频资源

        Args:
            html: HTML 内容
            base_url: 页面 URL（用于拼接相对路径）

        Returns:
            VideoExtractionResult
        """
        if not html:
            return VideoExtractionResult()

        soup = BeautifulSoup(html, "html.parser")
        result = VideoExtractionResult()

        # 1. <video> 标签
        self._extract_video_tags(soup, base_url, result)

        # 2. <source> 标签（通常在 <video> 内）
        self._extract_source_tags(soup, base_url, result)

        # 3. <iframe> 嵌入视频
        self._extract_iframes(soup, base_url, result)

        # 4. data-* 属性
        self._extract_data_attrs(soup, base_url, result)

        # 5. 正则扫描 m3u8/mp4/webm 直链
        self._extract_raw_urls(html, base_url, result)

        # 去重
        result.m3u8_urls = list(set(result.m3u8_urls))
        result.raw_mp4_urls = list(set(result.raw_mp4_urls))

        return result

    def _extract_video_tags(self, soup: BeautifulSoup, base_url: str, result: VideoExtractionResult) -> None:
        """提取 <video> 标签"""
        for video_tag in soup.find_all("video"):
            video = VideoInfo(source_tag="video")

            # src 属性
            src = video_tag.get("src")
            if src:
                video.url = urljoin(base_url, src)
                video.video_type = self._infer_type(video.url)

            # poster 属性（封面图）
            poster = video_tag.get("poster")
            if poster:
                video.poster = urljoin(base_url, poster)

            # data-src 属性
            data_src = video_tag.get("data-src") or video_tag.get("data-video")
            if data_src and not video.url:
                video.url = urljoin(base_url, data_src)
                video.video_type = self._infer_type(video.url)

            if video.url:
                result.videos.append(video)

    def _extract_source_tags(self, soup: BeautifulSoup, base_url: str, result: VideoExtractionResult) -> None:
        """提取 <source> 标签"""
        for source_tag in soup.find_all("source"):
            src = source_tag.get("src")
            if not src:
                continue

            video = VideoInfo(
                url=urljoin(base_url, src),
                source_tag="source",
                mime_type=source_tag.get("type", ""),
            )
            video.video_type = self._infer_type(video.url)

            # 从 type 属性提取 quality
            type_attr = source_tag.get("type", "")
            if "mp4" in type_attr:
                video.video_type = "mp4"
            elif "webm" in type_attr:
                video.video_type = "webm"

            result.videos.append(video)

    def _extract_iframes(self, soup: BeautifulSoup, base_url: str, result: VideoExtractionResult) -> None:
        """提取 <iframe> 嵌入视频"""
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src") or iframe.get("data-src")
            if not src:
                continue

            abs_url = urljoin(base_url, src)

            # 识别嵌入平台
            platform = "unknown"
            for name, pattern in _EMBED_PATTERNS.items():
                if pattern.search(abs_url):
                    platform = name
                    break

            video = VideoInfo(
                url=abs_url,
                video_type="iframe_embed",
                source_tag="iframe",
                title=platform,
            )

            result.iframes.append(video)

            # 如果是已知平台的嵌入链接，也作为 m3u8/mp4 候选
            if platform != "unknown":
                result.raw_mp4_urls.append(abs_url)

    def _extract_data_attrs(self, soup: BeautifulSoup, base_url: str, result: VideoExtractionResult) -> None:
        """提取 data-* 属性中的视频 URL"""
        video_attr_names = ["data-video", "data-video-url", "data-src", "data-media", "data-mp4", "data-m3u8"]

        for tag in soup.find_all(attrs={"data-video": True}):
            for attr_name in video_attr_names:
                val = tag.get(attr_name)
                if val and (".mp4" in val.lower() or ".m3u8" in val.lower()):
                    abs_url = urljoin(base_url, val)
                    result.videos.append(VideoInfo(
                        url=abs_url,
                        video_type=self._infer_type(abs_url),
                        source_tag=f"data-attr:{attr_name}",
                    ))

    def _extract_raw_urls(self, html: str, base_url: str, result: VideoExtractionResult) -> None:
        """正则扫描 m3u8/mp4/webm 直链"""
        # m3u8
        for match in _M3U8_PATTERN.finditer(html):
            url = match.group(0)
            if base_url and not url.startswith("http"):
                url = urljoin(base_url, url)
            result.m3u8_urls.append(url)

        # mp4
        for match in _MP4_PATTERN.finditer(html):
            url = match.group(0)
            if base_url and not url.startswith("http"):
                url = urljoin(base_url, url)
            result.raw_mp4_urls.append(url)

    def _infer_type(self, url: str) -> str:
        """从 URL 推断视频类型"""
        url_lower = url.lower().split("?")[0]
        if url_lower.endswith(".m3u8"):
            return "m3u8"
        elif url_lower.endswith(".mp4"):
            return "mp4"
        elif url_lower.endswith(".webm"):
            return "webm"
        elif url_lower.endswith(".flv"):
            return "flv"
        return "unknown"

    def extract_video_info_from_url(self, url: str) -> Optional[VideoInfo]:
        """从单个 URL 提取视频信息（不下载，仅解析）"""
        info = VideoInfo(url=url, video_type=self._infer_type(url))

        # 如果是已知视频平台 URL，尝试用 yt-dlp 获取信息
        try:
            from yt_dlp import YoutubeDL

            opts = {
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
            }

            with YoutubeDL(opts) as ydl:
                info_dict = ydl.extract_info(url, download=False)
                if info_dict:
                    info.title = info_dict.get("title", "")
                    info.url = info_dict.get("url", url)
                    formats = info_dict.get("formats", [])
                    if formats:
                        best = formats[-1]
                        info.quality = best.get("format_note", "")
                        info.mime_type = best.get("ext", "")

        except ImportError:
            logger.debug("yt-dlp not available for video info extraction")
        except Exception as e:
            logger.debug(f"Failed to extract video info: {e}")

        return info
