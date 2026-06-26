from dataclasses import dataclass, field
from typing import Any


@dataclass
class VideoItem:
    """从网络数据包提取的视频项"""
    title: str = ""
    url: str = ""
    video_url: str = ""
    cover_url: str = ""
    author: str = ""
    duration: str = ""
    platform: str = ""
    raw_data: dict = field(default_factory=dict)


@dataclass
class EpisodeInfo:
    """剧集信息（用于批量下载预览）"""
    title: str = ""  # 剧集标题（如"第1集"）
    url: str = ""    # 剧集页面 URL


@dataclass
class ShowInfo:
    """电视剧/综艺信息（用于批量下载预览）"""
    show_name: str = ""           # 节目名称（如"妻本善良"）
    show_url: str = ""            # 专辑页面 URL
    episodes: list[EpisodeInfo] = field(default_factory=list)  # 剧集列表


@dataclass
class PacketCrawlResult:
    """抓包爬取结果"""
    url: str
    success: bool = False
    videos: list[VideoItem] = field(default_factory=list)
    captured_requests: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    page_title: str = ""
