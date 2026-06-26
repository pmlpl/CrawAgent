"""常用工具集合 —— 对外导出

- User-Agent 池 (tools/utils.py)
- 数据导出 (JSON / CSV / Markdown)
- 基础爬虫实现 (tools/base_crawler.py)
- 浏览器爬虫 (tools/browser_crawler.py)
- 统一爬虫入口 (tools/crawler.py)
- 高级爬虫 (tools/advanced_crawler.py) - Scrapling 集成
- URL 检测工具
- 图片提取与下载 (tools/image_utils.py)
- 壁纸爬虫 (tools/wallpaper_crawler.py)
- 视频元数据爬虫 (tools/video_crawler.py)
- 抖音爬虫 (tools/douyin_crawler.py)
- 视频解析器 (tools/video_parser.py) - 凤凰影院视频解析
- 视频播放工具 (tools/video_agent.py) - Agent 播放视频
"""
from .utils import (
    UserAgentPool,
    random_user_agent,
    export_json,
    export_csv,
    export_markdown,
)
from .base_crawler import (
    BaseCrawler,
    CrawlResult,
    extract_urls,
    looks_like_url,
    extract_image_urls,
    download_images,
    # 壁纸
    WallpaperItem,
    smart_scrape_wallpapers,
    # 视频
    VideoItem,
    extract_video_metadata,
    smart_scrape_videos,
    # 抖音
    DouyinVideoItem,
    scrape_douyin_search,
    scrape_douyin_video,
)
from .browser_crawler import BrowserCrawler
from .crawler import Crawler
from .video_parser import (
    FengHuangVideoParser,
    parse_video,
    get_video_info,
    get_play_url,
)
from .video_agent import (
    watch_video,
    get_player_url,
)

# 网络抓包爬虫 - 基于 DrissionPage（可选）
try:
    from .packet_crawler import (
        PacketCrawler,
        VideoItem,
        PacketCrawlResult,
        scrape_and_download,
        is_available,
    )
    _PACKET_AVAILABLE = True
except ImportError:
    _PACKET_AVAILABLE = False
    PacketCrawler = None
    VideoItem = None
    PacketCrawlResult = None
    scrape_and_download = None
    is_available = None

# 高级爬虫 - Scrapling 集成（可选）
try:
    from .advanced_crawler import (
        AdvancedCrawler,
        AdvancedResult,
        AdaptiveExtractor,
        ProxyManager,
        quick_scrape,
        extract_with_selectors,
        SCRAPLING_AVAILABLE,
        BROWSER_IMPERSONATES,
    )
    _ADVANCED_AVAILABLE = True
except ImportError:
    _ADVANCED_AVAILABLE = False
    AdvancedCrawler = None
    AdvancedResult = None
    AdaptiveExtractor = None
    ProxyManager = None
    quick_scrape = None
    extract_with_selectors = None
    SCRAPLING_AVAILABLE = False
    BROWSER_IMPERSONATES = []

__all__ = [
    "UserAgentPool",
    "random_user_agent",
    "export_json",
    "export_csv",
    "export_markdown",
    "BaseCrawler",
    "CrawlResult",
    "extract_urls",
    "looks_like_url",
    "extract_image_urls",
    "download_images",
    "BrowserCrawler",
    "Crawler",
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
    # 视频解析器
    "FengHuangVideoParser",
    "parse_video",
    "get_video_info",
    "get_play_url",
    # 视频播放工具
    "watch_video",
    "get_player_url",
    # 网络抓包爬虫
    "PacketCrawler",
    "VideoItem",
    "PacketCrawlResult",
    "scrape_and_download",
    "is_available",
    # 高级爬虫
    "AdvancedCrawler",
    "AdvancedResult",
    "AdaptiveExtractor",
    "ProxyManager",
    "quick_scrape",
    "extract_with_selectors",
    "SCRAPLING_AVAILABLE",
    "BROWSER_IMPERSONATES",
]
