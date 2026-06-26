"""智能壁纸爬虫"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config.settings import get_logger

logger = get_logger(__name__)


# 默认请求头
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


@dataclass
class WallpaperItem:
    """单个壁纸项"""
    title: str = ""
    media_url: str = ""       # 视频或图片 URL
    media_type: str = ""      # "video" 或 "image"
    detail_url: str = ""      # 详情页 URL
    resolution: str = ""      # 分辨率（如 4k、3k）
    size: str = ""
    filename: str = ""        # 下载后保存的文件名
    local_path: str = ""      # 本地保存路径


def _clean_title(raw_title: str) -> str:
    """清理壁纸标题：去掉广告、网站名、中文字符等"""
    if not raw_title:
        return "未命名"
    cleaned = raw_title.strip()

    # 去重
    # 去掉"哲风壁纸"
    cleaned = cleaned.replace("哲风壁纸", "").strip()
    # 去掉"hwallpaper"/"wallpaper"
    cleaned = re.sub(r"[Hh]wallpaper", "", cleaned)
    # 去掉中文字符：「 」 『 』 【 】
    cleaned = re.sub(r"[「」『』【】\[\]]", "", cleaned)
    # 去掉引号："" '' ""
    cleaned = re.sub(r"[\"\"'']", "", cleaned)

    # 去掉多余空格和末尾的分隔符
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|·—_")
    # 限制长度
    if len(cleaned) > 80:
        cleaned = cleaned[:80]
    return cleaned or "未命名"


def _extract_resolution(title: str) -> str:
    """从标题中提取分辨率信息"""
    m = re.search(r"(\d+[kK])", title)
    if m:
        return m.group(1).lower() + " "
    return ""


def extract_wallpaper_detail_urls(html: str, base_url: str, limit: int = 20) -> list[str]:
    """从首页 HTML 中提取壁纸详情页 URL

    Args:
        html: 首页 HTML
        base_url: 首页 URL
        limit: 最多提取多少个

    Returns:
        详情页 URL 列表
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base_url).netloc
    urls: list[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # 只取 homeViewLook/xxx 格式（壁纸详情页）
        if "homeViewLook" in href or "/wallpaperDetail" in href.lower():
            full = urljoin(base_url, href)
            if host in full and full not in seen:
                urls.append(full)
                seen.add(full)
                if len(urls) >= limit:
                    break

    return urls


def extract_wallpaper_from_detail_page(html: str, base_url: str) -> WallpaperItem | None:
    """从详情页 HTML 中提取主壁纸（优先 video 动态壁纸）

    Args:
        html: 详情页 HTML
        base_url: 详情页 URL

    Returns:
        WallpaperItem 或 None
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 1. 优先找第一个 video 元素（动态壁纸）
    for v in soup.find_all("video"):
        src = v.get("src") or v.get("data-src") or ""
        if not src:
            continue
        full = urljoin(base_url, src)
        title = v.get("title", "")
        # 过滤广告/占位 video（只下载看起来是壁纸的 src）
        if "getCroppingImg" in full and "previewFileImg" not in full and "getVideoReduce" not in full:
            continue
        if any(k in src.lower() for k in ("advert", "banner", "ad_")):
            continue
        return WallpaperItem(
            title=_clean_title(title),
            media_url=full,
            media_type="video",
            detail_url=base_url,
            resolution=_extract_resolution(title),
        )

    # 2. 没有 video 的话，找第一张有具体标题的静态壁纸
    # 过滤掉背景图、通用占位图等
    for img in soup.find_all("img"):
        src = (img.get("src") or img.get("data-src") or
               img.get("data-original") or "")
        alt = (img.get("alt") or "").strip()
        if not src:
            continue

        # 过滤：头像、主题背景图、favicon、通用占位图
        if any(k in alt for k in ("头像", "主题背景", "背景", "favicon", "最喜欢的图像")):
            continue
        if "favicon" in src.lower() or "icon" in src.lower():
            continue

        full = urljoin(base_url, src)

        # 只保留核心壁纸图片
        if "getCroppingImg" in full or "getFile" in full:
            return WallpaperItem(
                title=_clean_title(alt or "壁纸图片"),
                media_url=full,
                media_type="image",
                detail_url=base_url,
                resolution=_extract_resolution(alt),
            )

    return None


def download_media(items: list[WallpaperItem], output_dir: str, *,
                   page_url: str = "", timeout: float = 60.0) -> list[WallpaperItem]:
    """下载壁纸（视频或图片）到指定目录

    新增特性：
    - URL 级别去重（避免重复下载相同资源）
    - 过滤非本站资源（避免 favicon、外部 gif）
    - 正确识别文件扩展名（视频 vs 图片）

    Args:
        items: 壁纸项列表
        output_dir: 输出目录
        page_url: 来源页面 URL（用于构造 Referer）
        timeout: 超时（秒）

    Returns:
        更新后的 items（填充 filename、local_path、size）
    """
    if not items:
        return []

    os.makedirs(output_dir, exist_ok=True)

    # URL 去重集合
    downloaded_urls: set[str] = set()
    # 文件名去重集合
    used_filenames: set[str] = set()

    # 过滤：只处理本站资源
    host = urlparse(page_url).netloc if page_url else ""

    headers = dict(_DEFAULT_HEADERS)
    headers["Accept"] = "*/*"
    if page_url:
        headers["Referer"] = page_url

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for item in items:
            if not item.media_url:
                continue

            # URL 去重
            if item.media_url in downloaded_urls:
                continue

            # 过滤非本站资源（避免抓取到外部 gif、favicon 等）
            if host and host not in item.media_url:
                continue

            # 过滤 favicon、logo、图标
            lower_url = item.media_url.lower()
            if any(k in lower_url for k in ("favicon", "icon.ico", "logo", "/avatar", "/icons/")):
                continue

            try:
                resp = client.get(item.media_url, headers=headers)
                if resp.status_code != 200 or not resp.content:
                    continue

                # 根据 URL 和 Content-Type 判断文件类型和扩展名
                ext = _guess_media_ext(item.media_url, resp.headers.get("Content-Type", ""))

                # 生成基于标题的文件名（更友好）
                base = _safe_title_filename(item.title) if item.title else _safe_filename(item.media_url)
                filename = f"{base}{ext}"

                # 如果与同批次其他文件名冲突（同标题），才加序号
                counter = 1
                final_filename = filename
                while final_filename in used_filenames:
                    final_filename = f"{base}_{counter}{ext}"
                    counter += 1

                filepath = os.path.join(output_dir, final_filename)

                # 写入文件（同名直接覆盖，避免 _1 后缀堆积）
                with open(filepath, "wb") as f:
                    f.write(resp.content)

                file_size = os.path.getsize(filepath)

                item.filename = final_filename
                item.local_path = filepath
                item.size = _format_size(file_size)
                downloaded_urls.add(item.media_url)
                used_filenames.add(final_filename)

            except (httpx.RequestError, OSError, IOError) as e:
                logger.debug(f"媒体下载失败 {item.media_url}: {e}")

    # 返回下载成功的项
    return [i for i in items if i.local_path]


def _safe_title_filename(title: str, max_len: int = 50) -> str:
    """从壁纸标题生成安全文件名"""
    if not title:
        return "wallpaper"
    # 去掉非法字符 + 特殊符号
    safe = re.sub(r'[\\/:*?"<>|\|\[\]\(\)（）【】「」『』「」\'`~!@#$%^&*+=]+', "_", title)
    safe = re.sub(r"_+", "_", safe).strip("_")
    # 限制长度
    if len(safe) > max_len:
        safe = safe[:max_len]
    return safe or "wallpaper"


def _guess_media_ext(url: str, content_type: str) -> str:
    """根据 URL 和 Content-Type 判断文件扩展名（支持视频）"""
    # 1. 从 URL 判断
    parsed = urlparse(url)
    path_lower = parsed.path.lower().split("?")[0]

    # 视频后缀
    video_exts = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".flv")
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg")

    for ext in video_exts:
        if path_lower.endswith(ext):
            return ext

    for ext in image_exts:
        if path_lower.endswith(ext):
            return ext

    # 2. 从 Content-Type 判断
    ct = content_type.lower()
    if "video" in ct:
        if "mp4" in ct:
            return ".mp4"
        if "webm" in ct:
            return ".webm"
        if "quicktime" in ct:
            return ".mov"
        return ".mp4"

    if "image" in ct:
        if "jpeg" in ct or "jpg" in ct:
            return ".jpg"
        if "png" in ct:
            return ".png"
        if "webp" in ct:
            return ".webp"
        if "gif" in ct:
            return ".gif"
        return ".jpg"

    # 3. 默认：根据 URL 模式猜测
    # haowallpaper: previewFileImg 通常是 mp4 视频, getVideoReduce 也是视频
    if "previewFileImg" in url or "getVideoReduce" in url:
        return ".mp4"
    if "getCroppingImg" in url:
        return ".jpg"

    return ".mp4"


def _safe_filename(url: str, max_len: int = 60) -> str:
    """从 URL 生成安全的文件名（截取关键部分）"""
    parsed = urlparse(url)
    # 取路径最后一段作为基础
    path = parsed.path.strip("/")
    if path:
        name = path.split("/")[-1].rsplit(".", 1)[0]
    else:
        name = parsed.netloc.replace(".", "_")
    # 去掉不合法字符
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = name.strip("._")
    if not name:
        name = "image"
    # 限制长度
    if len(name) > max_len:
        name = name[:max_len]
    return name


def _format_size(size_bytes: int) -> str:
    """把字节数格式化为人类可读"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


# ============================================================
# 智能壁纸爬虫：主入口（首页 → 详情页列表 → 每个详情页提取主壁纸 → 下载）
# ============================================================

def smart_scrape_wallpapers(crawler_instance: Any, home_url: str,
                            output_dir: str, max_wallpapers: int = 15) -> tuple[list[WallpaperItem], str]:
    """智能壁纸爬虫主函数

    流程：
    1. 爬取首页 → 提取 homeViewLook 详情页 URL
    2. 逐个爬取详情页 → 提取主 video 或 主图
    3. 去重 + 过滤 → 下载到 output_dir

    Args:
        crawler_instance: Crawler 实例（用于复用 Playwright）
        home_url: 首页 URL
        output_dir: 输出目录
        max_wallpapers: 最多下载多少个（避免太多）

    Returns:
        (成功下载的壁纸项列表, 状态信息文本)
    """
    results: list[WallpaperItem] = []
    messages: list[str] = []

    # 1. 爬取首页
    home_result = crawler_instance.fetch(home_url)
    if not home_result.success or not home_result.html:
        return [], f"❌ 无法访问首页: {home_url}"

    # 2. 从首页提取详情页 URL
    detail_urls = extract_wallpaper_detail_urls(
        home_result.html, home_url, limit=max_wallpapers * 2
    )
    if not detail_urls:
        # 如果没找到 homeViewLook 详情页，尝试直接从首页的 video 元素提取
        videos = _extract_videos_from_list(home_result.html, home_url)
        if videos:
            results = download_media(videos, output_dir, page_url=home_url)
            messages.append(f"ℹ️ 从首页 video 提取到 {len(videos)} 个壁纸，成功下载 {len(results)} 个")
            return results, "\n".join(messages)
        return [], "❌ 首页没有找到任何壁纸详情页"

    messages.append(f"✅ 从首页找到 {len(detail_urls)} 个详情页")

    # 3. 逐个爬取详情页，提取主壁纸
    items: list[WallpaperItem] = []
    detail_count = min(len(detail_urls), max_wallpapers)
    for i, d_url in enumerate(detail_urls[:detail_count], 1):
        try:
            d_result = crawler_instance.fetch(d_url)
            if d_result.success and d_result.html:
                item = extract_wallpaper_from_detail_page(d_result.html, d_url)
                if item and item.media_url:
                    items.append(item)
        except (httpx.RequestError, Exception) as e:
            logger.debug(f"详情页提取失败 {d_url}: {e}")

    messages.append(f"✅ 从 {detail_count} 个详情页提取到 {len(items)} 个壁纸资源")

    # 4. 下载媒体文件
    downloaded = download_media(items, output_dir, page_url=home_url)
    messages.append(f"✅ 成功下载 {len(downloaded)} 个壁纸文件")

    return downloaded, "\n".join(messages)


def _extract_videos_from_list(html: str, base_url: str) -> list[WallpaperItem]:
    """从列表页（首页）直接提取 video 元素（备用方案）"""
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base_url).netloc
    items: list[WallpaperItem] = []
    seen: set[str] = set()

    for v in soup.find_all("video"):
        src = v.get("src") or ""
        if not src:
            continue
        full = urljoin(base_url, src)
        if host not in full:
            continue
        if full in seen:
            continue
        seen.add(full)

        title = v.get("title", "")
        items.append(WallpaperItem(
            title=_clean_title(title),
            media_url=full,
            media_type="video",
            detail_url=base_url,
            resolution=_extract_resolution(title),
        ))

    return items
