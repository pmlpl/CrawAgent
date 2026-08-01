"""输出整理模块（P3）：文件路径模板 + 媒体下载

- FileOrganizer: 路径模板引擎 + 非法字符转义
- MediaDownloader: yt-dlp + httpx 流式下载
"""
from crawagent.output.organizer import (
    FileOrganizer,
    sanitize_filename,
    title_to_slug,
    extract_domain,
    organize_content,
)
from crawagent.output.media_downloader import MediaDownloader

__all__ = [
    "FileOrganizer",
    "MediaDownloader",
    "sanitize_filename",
    "title_to_slug",
    "extract_domain",
    "organize_content",
]
