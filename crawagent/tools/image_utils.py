"""图片提取与下载工具"""
from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config.settings import get_logger
from .utils import random_user_agent, _UA_POOL as _UA_POOL_IMPORTED

logger = get_logger(__name__)


# 常见图片后缀
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg", ".avif")

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


def _build_headers(url: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """构建更真实的请求头，每次请求稍微随机化"""
    import random
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


def extract_image_urls(html: str, base_url: str = "", limit: int = 50) -> list[str]:
    """从 HTML 中提取图片 URL（去重、转绝对地址）

    Args:
        html: 页面 HTML 源码
        base_url: 页面 URL，用于把相对地址转换为绝对地址
        limit: 最多提取多少张图片

    Returns:
        图片 URL 列表
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()

    # 1) 从 <img> 标签提取
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or img.get("data-lazy-src")
        if src:
            urls.append(src)

    # 2) 从 <video> 的 poster 提取（海报图）
    for video in soup.find_all("video"):
        poster = video.get("poster")
        if poster:
            urls.append(poster)

    # 3) 从 <source> 提取
    for source in soup.find_all("source"):
        srcset = source.get("srcset") or source.get("src")
        if srcset:
            # srcset 可能是 "a.jpg 1x, b.jpg 2x"，取第一个
            first = srcset.split(",")[0].strip().split()[0]
            urls.append(first)

    # 4) 从 style="background-image: url(...)" 背景图提取
    for tag in soup.find_all(attrs={"style": True}):
        style = tag.get("style", "")
        m = re.search(r'url\(\s*["\']?([^"\')]+)["\']?\s*\)', style)
        if m:
            urls.append(m.group(1))

    # 5) 从 <a href="xxx.jpg"> 链接提取（点击下载链接）
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(ext in href.lower() for ext in _IMAGE_EXTS):
            urls.append(href)

    # 整理：去重 + 转绝对地址 + 过滤
    results: list[str] = []
    for u in urls:
        if not u or not u.strip():
            continue
        u = u.strip()
        # 补全相对地址
        if not u.startswith(("http://", "https://")):
            if base_url:
                try:
                    u = urljoin(base_url, u)
                except Exception as e:
                    logger.debug(f"urljoin 失败: {e}")
                    continue
            else:
                continue

        # 过滤：必须是图片（通过扩展名判断，或 img/video 来源直接信任）
        if not any(ext in u.lower().split("?")[0] for ext in _IMAGE_EXTS):
            # 没有扩展名的 URL，但来源是 img 标签，仍然保留
            pass

        # 过滤明显的占位图/广告/小图标（更严格）
        lower = u.lower()
        skip_keywords = ("icon", "logo", "favicon", "avatar", "placeholder",
                         "loading", "spinner", "1x1", "blank", "data:image")
        if any(k in lower for k in skip_keywords):
            continue

        if u in seen:
            continue
        seen.add(u)
        results.append(u)

        if len(results) >= limit:
            break

    return results


def download_images(image_urls: list[str], output_dir: str, *, page_url: str = "", timeout: float = 30.0, max_workers: int = 4) -> list[dict[str, str]]:
    """下载图片到指定目录

    Args:
        image_urls: 图片 URL 列表
        output_dir: 输出目录（如 "output/img"）
        page_url: 来源页面 URL，用于构造 Referer
        timeout: 单张图片超时（秒）
        max_workers: 并发数

    Returns:
        list: 每张图的下载结果 [{ "url": ..., "path": ..., "size": ... }]
    """
    if not image_urls:
        return []

    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)

    results: list[dict[str, str]] = []
    headers = _build_headers(page_url or image_urls[0])
    headers["Accept"] = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for i, img_url in enumerate(image_urls):
            try:
                resp = client.get(img_url, headers=headers)
                if resp.status_code != 200:
                    results.append({"url": img_url, "path": "", "size": "", "error": f"HTTP {resp.status_code}"})
                    continue

                # 从 URL 或 Content-Type 推断扩展名
                ext = _guess_ext(img_url, resp.headers.get("Content-Type", ""))

                # 生成文件名
                safe_name = _safe_filename(img_url)
                filename = f"{safe_name}{ext}"
                filepath = os.path.join(output_dir, filename)

                # 如果同名文件已存在，加个序号
                counter = 1
                while os.path.exists(filepath):
                    filename = f"{safe_name}_{counter}{ext}"
                    filepath = os.path.join(output_dir, filename)
                    counter += 1

                # 写入文件
                with open(filepath, "wb") as f:
                    f.write(resp.content)

                file_size = os.path.getsize(filepath)
                size_str = _format_size(file_size)

                results.append({"url": img_url, "path": filepath, "size": size_str, "error": ""})

            except (httpx.RequestError, OSError, IOError) as e:
                logger.debug(f"图片下载失败 {img_url}: {e}")
                results.append({"url": img_url, "path": "", "size": "", "error": str(e)})

    return results


def _guess_ext(url: str, content_type: str) -> str:
    """根据 URL 或 Content-Type 推断图片扩展名"""
    # 1) 从 URL 判断
    parsed = urlparse(url)
    path_lower = parsed.path.lower().split("?")[0]
    for ext in _IMAGE_EXTS:
        if path_lower.endswith(ext):
            return ext
    # 2) 从 Content-Type 判断
    ct = content_type.lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    if "bmp" in ct:
        return ".bmp"
    if "svg" in ct:
        return ".svg"
    if "avif" in ct:
        return ".avif"
    # 默认 jpg
    return ".jpg"


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
