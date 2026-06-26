"""CrawAgent FastAPI 服务

用法:
    uvicorn crawagent.api.server:app --reload --port 8000
    或
    python -m crawagent.api.server

端点:
    GET  /           - 健康检查
    POST /crawl      - 爬取 URL (支持视频/壁纸/普通网页)
    GET  /video      - 查看视频爬虫配置
    GET  /wallpaper  - 查看壁纸爬虫配置
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any

# 确保 crawagent 包可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..config.settings import load_settings, get_logger
from ..llm.factory import LLMFactory
from ..graph.workflow import CrawWorkflow
from ..tools import Crawler
from ..tools.base_crawler import smart_scrape_videos, smart_scrape_wallpapers, VideoItem, WallpaperItem

logger = get_logger(__name__)


# ============================================================
# Pydantic 模型
# ============================================================

class CrawlRequest(BaseModel):
    """爬取请求"""
    url: str = Field(..., description="目标 URL", examples=["https://v.youku.com/v_show/id_xxx.html"])
    mode: str = Field(
        default="auto",
        description="爬取模式: auto=自动识别 | video=视频网站 | wallpaper=壁纸网站 | basic=普通网页",
    )
    max_wallpapers: int = Field(default=12, ge=1, le=100, description="壁纸最大下载数量")
    max_videos: int = Field(default=10, ge=1, le=100, description="视频最大解析数量")
    image_output_dir: str = Field(default="output/img", description="图片/壁纸输出目录")
    video_output_dir: str = Field(default="output/video", description="视频元数据输出目录")
    force_browser: bool | None = Field(default=None, description="True=强制浏览器, False=只用httpx")
    debug_mode: bool = Field(default=False, description="开启有头调试模式")


class CrawlResponse(BaseModel):
    """爬取响应"""
    success: bool
    url: str
    strategy: str
    status_code: int = 0
    title: str = ""
    text_preview: str = ""
    html_length: int = 0
    links_count: int = 0

    # 视频元数据（视频网站时填充）
    video: dict[str, Any] | None = None

    # 壁纸信息（壁纸网站时填充）
    wallpapers: list[dict[str, Any]] = []

    # 图片下载结果
    images: list[dict[str, Any]] = []

    # 错误
    error: str = ""
    warnings: list[str] = []

    # 耗时
    duration_ms: int = 0


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str = "1.0.0"


class ConfigResponse(BaseModel):
    """配置响应"""
    video_output_dir: str
    image_output_dir: str
    max_wallpapers: int
    video_sites: list[str]
    wallpaper_sites: list[str]


# ============================================================
# FastAPI 应用
# ============================================================

app = FastAPI(
    title="CrawAgent API",
    description=(
        "CrawAgent 爬虫工具的 REST API 接口。\n\n"
        "支持：\n"
        "- 普通网页爬取（文本、链接、结构化数据）\n"
        "- 视频网站元数据提取（优酷/B站/爱奇艺/腾讯视频等）\n"
        "- 壁纸网站智能爬取（haowallpaper 等）\n"
        "- 自动识别 URL 类型，选择最佳爬取策略\n"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS - 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局爬虫实例（懒加载）
_crawler: Crawler | None = None


def get_crawler() -> Crawler:
    global _crawler
    if _crawler is None:
        _crawler = Crawler(headless=True)
    return _crawler


# ============================================================
# 辅助函数
# ============================================================

def _video_to_dict(v: VideoItem) -> dict[str, Any]:
    return {
        "title": v.title,
        "site": v.site,
        "page_url": v.page_url,
        "description": v.description,
        "directors": v.directors,
        "actors": v.actors,
        "tags": v.tags,
        "episodes": v.episodes,
        "episode_count": v.episode_count,
        "play_count": v.play_count,
        "rating": v.rating,
        "stream_urls": v.stream_urls,
        "poster_urls": v.poster_urls,
    }


def _wallpaper_to_dict(w: WallpaperItem) -> dict[str, Any]:
    return {
        "title": w.title,
        "media_url": w.media_url,
        "media_type": w.media_type,
        "detail_url": w.detail_url,
        "resolution": w.resolution,
        "size": w.size,
        "filename": w.filename,
        "local_path": w.local_path,
    }


# ============================================================
# 端点
# ============================================================

@app.get("/", response_model=HealthResponse, tags=["系统"])
async def health_check():
    """健康检查"""
    return HealthResponse(
        status="ok",
        timestamp=datetime.now().isoformat(),
        version="1.0.0",
    )


@app.post("/crawl", response_model=CrawlResponse, tags=["爬取"])
async def crawl_url(req: CrawlRequest):
    """爬取目标 URL

    自动识别 URL 类型并选择最佳爬取策略：
    - 视频网站（youku/bilibili/iqiyi/qq/youtube 等）→ 提取元数据
    - 壁纸网站（haowallpaper 等）→ 智能下载壁纸
    - 普通网页 → 提取文本、链接、结构化数据
    """
    import time
    start = time.time()

    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空")

    # 基本的 URL 格式校验
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url 必须以 http:// 或 https:// 开头")

    crawler = get_crawler()
    result = None
    video_item: VideoItem | None = None
    wallpapers: list[WallpaperItem] = []
    warnings: list[str] = []

    try:
        # ---- 视频网站处理 ----
        is_video_site = req.mode == "video" or (
            req.mode == "auto" and
            any(site in url.lower() for site in (
                "youku.com", "bilibili.com", "v.qq.com", "iqiyi.com",
                "youtube.com", "douyin.com", "mgtv.com", "sohu.com",
            ))
        )
        is_wallpaper = req.mode == "wallpaper" or (
            req.mode == "auto" and
            ("haowallpaper" in url.lower() or "hwallpaper" in url.lower())
        )

        # ---- 壁纸网站处理 ----
        if is_wallpaper:
            wps, msg = smart_scrape_wallpapers(
                crawler, url, req.image_output_dir,
                max_wallpapers=req.max_wallpapers,
            )
            wallpapers = wps
            result = crawler.fetch(url)  # 同时保留基础爬取结果
            if msg and "❌" in msg:
                warnings.append(msg)

        # ---- 视频网站处理 ----
        elif is_video_site:
            video_item, msg = smart_scrape_videos(crawler, url)
            result = crawler.fetch(url)
            if msg and "❌" in msg:
                warnings.append(msg)
            # 主流视频网站可能拿不到直链，提醒用户
            if video_item and not video_item.stream_urls:
                warnings.append(
                    "该视频网站使用了加密/需登录的流协议，CrawAgent 已提取元数据，但无法提供可下载的直链地址。"
                )

        # ---- 普通网页处理 ----
        else:
            result = crawler.fetch(url)

        # ---- 构造响应 ----
        if result is None:
            return CrawlResponse(
                success=False,
                url=url,
                strategy="none",
                error="爬取失败，未获取到任何结果",
                duration_ms=int((time.time() - start) * 1000),
            )

        return CrawlResponse(
            success=result.success,
            url=url,
            strategy=result.strategy or "basic",
            status_code=result.status_code or 0,
            title=result.title or "",
            text_preview=(result.text or "")[:2000],
            html_length=len(result.html or ""),
            links_count=len(result.links or []),
            video=_video_to_dict(video_item) if video_item else None,
            wallpapers=[_wallpaper_to_dict(w) for w in wallpapers],
            images=[],  # 简化：暂不返回图片下载列表
            error=result.error or "",
            warnings=warnings,
            duration_ms=int((time.time() - start) * 1000),
        )

    except Exception as e:
        return CrawlResponse(
            success=False,
            url=url,
            strategy="error",
            error=f"爬取出错: {str(e)}",
            duration_ms=int((time.time() - start) * 1000),
        )


@app.get("/video", response_model=ConfigResponse, tags=["配置"])
async def get_video_config():
    """查看视频爬虫配置"""
    return ConfigResponse(
        video_output_dir="output/video",
        image_output_dir="output/img",
        max_wallpapers=12,
        video_sites=[
            "youku.com", "bilibili.com", "v.qq.com", "iqiyi.com",
            "youtube.com", "douyin.com", "mgtv.com", "sohu.com",
        ],
        wallpaper_sites=["haowallpaper.com", "hwallpaper.com"],
    )


@app.get("/wallpaper", response_model=ConfigResponse, tags=["配置"])
async def get_wallpaper_config():
    """查看壁纸爬虫配置"""
    return ConfigResponse(
        video_output_dir="output/video",
        image_output_dir="output/img",
        max_wallpapers=12,
        video_sites=[
            "youku.com", "bilibili.com", "v.qq.com", "iqiyi.com",
            "youtube.com", "douyin.com", "mgtv.com", "sohu.com",
        ],
        wallpaper_sites=["haowallpaper.com", "hwallpaper.com"],
    )


# ============================================================
# 启动脚本
# ============================================================

if __name__ == "__main__":
    import uvicorn

    logger.info("=" * 60)
    logger.info("CrawAgent API 服务")
    logger.info("=" * 60)
    logger.info("  文档: http://localhost:8000/docs")
    logger.info("  ReDoc: http://localhost:8000/redoc")
    logger.info("  示例: POST /crawl  { \"url\": \"https://...\" }")
    logger.info("=" * 60)

    uvicorn.run(
        "crawagent.api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
