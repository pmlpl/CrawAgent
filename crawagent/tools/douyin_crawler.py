"""抖音搜索页面视频爬取与下载"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

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
class DouyinVideoItem:
    """抖音视频项"""
    title: str = ""
    url: str = ""
    video_url: str = ""
    cover_url: str = ""
    author: str = ""
    play_count: str = ""
    duration: str = ""


def extract_douyin_videos_from_html(html: str, page_url: str) -> list[DouyinVideoItem]:
    """从抖音搜索页面 HTML 中提取视频列表"""
    videos: list[DouyinVideoItem] = []
    if not html:
        return videos

    try:
        soup = BeautifulSoup(html, "html.parser")

        for item in soup.find_all("div", class_=re.compile(r"(video-card|aweme-item|search-video-item)", re.I)):
            video = DouyinVideoItem()

            link_tag = item.find("a", href=True)
            if link_tag:
                href = link_tag["href"]
                if href.startswith("/"):
                    href = f"https://www.douyin.com{href}"
                video.url = href

            title_tag = item.find(("h3", "span", "p"), class_=re.compile(r"(title|desc|text)", re.I))
            if title_tag:
                video.title = title_tag.get_text(strip=True)[:50]

            cover_tag = item.find("img", class_=re.compile(r"(cover|poster)", re.I))
            if cover_tag:
                video.cover_url = cover_tag.get("src", "") or cover_tag.get("data-src", "")

        for script in soup.find_all("script"):
            script_text = script.string or ""
            if "aweme" in script_text.lower() or "video" in script_text.lower():
                for match in re.finditer(
                    r'"play_addr"\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                    script_text
                ):
                    video_url = match.group(1)
                    if video_url not in [v.video_url for v in videos]:
                        video = DouyinVideoItem(video_url=video_url)
                        videos.append(video)

                for match in re.finditer(
                    r'"video_url"\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                    script_text
                ):
                    video_url = match.group(1)
                    if video_url not in [v.video_url for v in videos]:
                        video = DouyinVideoItem(video_url=video_url)
                        videos.append(video)

    except Exception as e:
        logger.debug(f"从 HTML 提取抖音视频失败: {e}")

    return videos


def extract_douyin_videos_from_xhr(xhr_data: list[dict[str, Any]]) -> list[DouyinVideoItem]:
    """从捕获的 XHR 数据中提取视频列表"""
    videos: list[DouyinVideoItem] = []

    for xhr in xhr_data:
        try:
            text = xhr.get("text", "")
            if not text:
                continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue

            def extract_from_json(obj, path=""):
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        new_path = f"{path}.{key}" if path else key
                        if key.lower() == "play_addr" and isinstance(value, str):
                            if value.endswith(".mp4") and value not in [v.video_url for v in videos]:
                                videos.append(DouyinVideoItem(video_url=value))
                        elif key.lower() == "video_url" and isinstance(value, str):
                            if value.endswith(".mp4") and value not in [v.video_url for v in videos]:
                                videos.append(DouyinVideoItem(video_url=value))
                        elif key.lower() == "url_list" and isinstance(value, list):
                            for url_item in value:
                                if isinstance(url_item, str) and url_item.endswith(".mp4"):
                                    if url_item not in [v.video_url for v in videos]:
                                        videos.append(DouyinVideoItem(video_url=url_item))
                                elif isinstance(url_item, dict):
                                    extract_from_json(url_item, new_path)
                        elif isinstance(value, (dict, list)):
                            extract_from_json(value, new_path)
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        new_path = f"{path}[{i}]"
                        extract_from_json(item, new_path)

            extract_from_json(data)

            for match in re.finditer(
                r'"play_addr"\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                text
            ):
                video_url = match.group(1)
                if video_url not in [v.video_url for v in videos]:
                    videos.append(DouyinVideoItem(video_url=video_url))

            for match in re.finditer(
                r'"video_url"\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                text
            ):
                video_url = match.group(1)
                if video_url not in [v.video_url for v in videos]:
                    videos.append(DouyinVideoItem(video_url=video_url))

        except Exception as e:
            logger.debug(f"从 XHR 提取视频失败: {e}")

    return videos


def download_douyin_videos(videos: list[DouyinVideoItem], output_dir: str = "output/video",
                           max_downloads: int = 10) -> list[tuple[str, bool, str]]:
    """下载抖音视频到指定目录

    Args:
        videos: 视频列表
        output_dir: 输出目录
        max_downloads: 最大下载数量

    Returns:
        下载结果列表: [(文件名, 是否成功, 消息)]
    """
    results: list[tuple[str, bool, str]] = []

    os.makedirs(output_dir, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    downloaded_count = 0
    for video in videos:
        if downloaded_count >= max_downloads:
            break
        if not video.video_url:
            continue

        try:
            video_url = video.video_url
            if video_url.startswith("//"):
                video_url = "https:" + video_url

            filename = video.title[:30] if video.title else f"video_{downloaded_count + 1}"
            filename = re.sub(r'[\\/:*?"<>|]', "_", filename)
            filename = f"{filename}.mp4"
            filepath = os.path.join(output_dir, filename)

            if os.path.exists(filepath):
                results.append((filename, True, "已存在"))
                downloaded_count += 1
                continue

            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                response = client.get(video_url, headers=headers)
                if response.status_code == 200:
                    with open(filepath, "wb") as f:
                        f.write(response.content)
                    results.append((filename, True, "下载成功"))
                    downloaded_count += 1
                else:
                    results.append((filename, False, f"HTTP {response.status_code}"))

        except (httpx.RequestError, OSError, IOError) as e:
            logger.debug(f"抖音视频下载失败 {video.video_url}: {e}")
            filename = video.title[:30] if video.title else f"video_{downloaded_count + 1}"
            filename = f"{filename}.mp4"
            results.append((filename, False, str(e)[:50]))

    return results


def scrape_douyin_search(crawler_instance: Any, page_url: str,
                         output_dir: str = "output/video",
                         max_downloads: int = 10) -> tuple[list[DouyinVideoItem], list[tuple[str, bool, str]], str]:
    """爬取抖音搜索页面并下载视频

    Args:
        crawler_instance: Crawler 实例
        page_url: 抖音搜索页面 URL
        output_dir: 视频输出目录
        max_downloads: 最大下载数量

    Returns:
        (视频列表, 下载结果, 状态消息)
    """
    messages: list[str] = []
    messages.append("🎬 抖音搜索页面爬取开始...")

    result = crawler_instance.fetch(page_url)
    if not result.success:
        return [], [], f"❌ 页面爬取失败: {result.error}"

    messages.append(f"✅ 页面获取成功 (策略: {result.strategy})")

    videos_html = extract_douyin_videos_from_html(result.html, page_url)
    messages.append(f"📊 从 HTML 提取到 {len(videos_html)} 个视频")

    videos_xhr = []
    if result.xhr_data:
        videos_xhr = extract_douyin_videos_from_xhr(result.xhr_data)
        messages.append(f"📊 从 XHR 数据提取到 {len(videos_xhr)} 个视频")

    all_videos = videos_html + videos_xhr

    seen_urls = set()
    unique_videos = []
    for v in all_videos:
        if v.video_url and v.video_url not in seen_urls:
            seen_urls.add(v.video_url)
            unique_videos.append(v)

    messages.append(f"📋 去重后共 {len(unique_videos)} 个视频")

    download_results = []
    if unique_videos:
        download_results = download_douyin_videos(unique_videos, output_dir, max_downloads)
        success_count = sum(1 for _, success, _ in download_results if success)
        messages.append(f"📥 下载完成: {success_count}/{len(download_results)} 成功")
    else:
        messages.append("⚠️  未找到可下载的视频")
        messages.append("💡 说明: 抖音 PC 端搜索页面的视频数据需要登录账号后才能获取。")
        messages.append("       如果需要爬取抖音视频，建议：")
        messages.append("       1. 在浏览器中登录抖音账号")
        messages.append("       2. 将登录后的 cookies 导入爬虫")
        messages.append("       3. 或者使用抖音移动端 API（需要签名算法）")
        messages.append("       当前 CrawAgent 已尽力尝试，但受限于抖音的反爬机制，无法获取视频数据。")

    return unique_videos, download_results, "\n".join(messages)


# ============================================================
# 抖音视频详情页爬取 - 支持无水印下载
# ============================================================

def extract_video_id_from_url(url: str) -> str | None:
    """从抖音 URL 中提取视频 ID"""
    patterns = [
        r'/video/(\d+)',
        r'/note/(\d+)',
        r'modal_id=(\d+)',
        r'video/(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def scrape_douyin_video(crawler_instance: Any, page_url: str,
                        output_dir: str = "output/video",
                        use_third_party: bool = True) -> tuple[str | None, str]:
    """爬取抖音视频详情页并下载

    Args:
        crawler_instance: Crawler 实例
        page_url: 抖音视频页面 URL (如 https://www.douyin.com/video/7648521819389835941)
        output_dir: 视频输出目录
        use_third_party: 是否使用第三方解析接口

    Returns:
        (视频文件路径, 状态消息)
    """
    messages: list[str] = []
    messages.append("🎬 抖音视频详情页爬取开始...")

    video_id = extract_video_id_from_url(page_url)
    if not video_id:
        return None, "❌ 无法从 URL 中提取视频 ID"

    messages.append(f"📋 视频 ID: {video_id}")

    # 方法1: 尝试从页面提取视频
    result = crawler_instance.fetch(page_url)
    if not result.success:
        return None, f"❌ 页面爬取失败: {result.error}"

    messages.append(f"✅ 页面获取成功 (策略: {result.strategy})")

    # 尝试从页面 HTML/XHR 中提取视频 URL
    video_url = _extract_douyin_video_url_from_page(result.html, result.xhr_data)

    if not video_url and use_third_party:
        # 方法2: 使用第三方解析接口
        video_url = _fetch_douyin_via_third_party(page_url)
        if video_url:
            messages.append("📡 通过第三方接口获取到视频 URL")

    if not video_url:
        # 方法3: 尝试从 RENDER_DATA 中提取
        video_url = _extract_from_render_data(result.html)
        if video_url:
            messages.append("🔍 从页面 RENDER_DATA 中提取到视频 URL")

    if not video_url:
        messages.append("⚠️  无法获取视频 URL")
        messages.append("💡 抖音视频详情页可能需要登录才能获取完整视频。")
        messages.append("   尝试使用浏览器登录后复制 cookies 到爬虫中。")
        return None, "\n".join(messages)

    messages.append(f"🔗 视频 URL: {video_url[:80]}...")

    # 下载视频
    os.makedirs(output_dir, exist_ok=True)
    filename = f"douyin_{video_id}.mp4"
    filepath = os.path.join(output_dir, filename)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            response = client.get(video_url, headers=headers)
            if response.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(response.content)
                messages.append(f"✅ 视频下载成功: {filepath}")
                return filepath, "\n".join(messages)
            else:
                messages.append(f"❌ 下载失败: HTTP {response.status_code}")
                return None, "\n".join(messages)
    except (httpx.RequestError, OSError, IOError) as e:
        logger.debug(f"抖音视频下载失败: {e}")
        messages.append(f"❌ 下载异常: {e}")
        return None, "\n".join(messages)


def _extract_douyin_video_url_from_page(html: str, xhr_data: list[dict]) -> str | None:
    """从页面 HTML 或 XHR 数据中提取视频 URL"""
    # 尝试从 XHR 数据中提取
    for xhr in xhr_data or []:
        text = xhr.get("text", "")
        if not text:
            continue
        # 查找视频 URL
        for pattern in [
            r'"play_addr"\s*:\s*["\']([^"\']+)["\']',
            r'"video_url"\s*:\s*["\']([^"\']+)["\']',
            r'"download_addr"\s*:\s*["\']([^"\']+)["\']',
            r'(https?://[^"\']+\.mp4[^"\']*)',
        ]:
            match = re.search(pattern, text)
            if match:
                url = match.group(1)
                if url.startswith("//"):
                    url = "https:" + url
                if ".mp4" in url.lower() or "playaddr" in url.lower():
                    return url

    # 尝试从 HTML 中提取
    if html:
        for pattern in [
            r'<video[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']',
            r'"download_url"\s*:\s*["\']([^"\']+)["\']',
            r'"play_addr"\s*:\s*["\']([^"\']+)["\']',
        ]:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                url = match.group(1)
                if url.startswith("//"):
                    url = "https:" + url
                return url

    return None


def _extract_from_render_data(html: str) -> str | None:
    """从页面 RENDER_DATA 中提取视频 URL"""
    if not html:
        return None

    # 查找 RENDER_DATA
    patterns = [
        r'window\.__RENDER_DATA__\s*=\s*([^<]+)',
        r'id="RENDER_DATA">([^<]+)',
        r'"video_url"\s*:\s*"([^"]+)"',
        r'"play_addr"\s*:\s*"([^"]+)"',
    ]

    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            data = match.group(1)
            try:
                import urllib.parse
                decoded = urllib.parse.unquote(data)
                # 查找视频 URL
                url_match = re.search(r'(https?://[^"\']+\.mp4[^"\']*)', decoded)
                if url_match:
                    return url_match.group(1)
            except Exception as e:
                logger.debug(f"RENDER_DATA 解析失败: {e}")

    return None


def _fetch_douyin_via_third_party(douyin_url: str) -> str | None:
    """通过第三方解析接口获取抖音视频 URL"""
    # 常用的抖音视频解析接口（这些是公开的解析服务）
    api_endpoints = [
        "https://api.douyin.wtf/api",
        "https://www.douyin.wtf/api",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
    }

    for api in api_endpoints:
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                # 有些接口需要 POST
                try:
                    response = client.post(api, json={"url": douyin_url}, headers=headers)
                except httpx.RequestError:
                    # 有些接口需要 GET
                    response = client.get(f"{api}?url={douyin_url}", headers=headers)

                if response.status_code == 200:
                    data = response.json()
                    # 尝试从响应中提取视频 URL
                    if "data" in data:
                        video_url = data["data"].get("url") or data["data"].get("play") or data["data"].get("video_url")
                        if video_url:
                            return video_url
                    elif "url" in data:
                        return data["url"]
                    elif "play" in data:
                        return data["play"]
        except (httpx.RequestError, json.JSONDecodeError) as e:
            logger.debug(f"第三方接口 {api} 请求失败: {e}")

    return None


def batch_scrape_douyin_videos(crawler_instance: Any, urls: list[str],
                                output_dir: str = "output/video",
                                max_downloads: int = 10) -> tuple[list[tuple[str, str | None]], str]:
    """批量爬取抖音视频

    Args:
        crawler_instance: Crawler 实例
        urls: 抖音视频 URL 列表
        output_dir: 视频输出目录
        max_downloads: 最大下载数量

    Returns:
        (下载结果列表 [(url, filepath)], 状态消息)
    """
    messages: list[str] = []
    results: list[tuple[str, str | None]] = []

    messages.append(f"🎬 开始批量爬取 {len(urls)} 个抖音视频...")
    os.makedirs(output_dir, exist_ok=True)

    for i, url in enumerate(urls[:max_downloads], 1):
        messages.append(f"\n[{i}/{min(len(urls), max_downloads)}] 处理: {url}")

        filepath, status = scrape_douyin_video(crawler_instance, url, output_dir, use_third_party=True)
        if filepath:
            results.append((url, filepath))
            messages.append(f"✅ 成功: {filepath}")
        else:
            results.append((url, None))
            messages.append(f"❌ 失败")

    success_count = sum(1 for _, fp in results if fp)
    messages.append(f"\n📊 批量下载完成: {success_count}/{len(results)} 成功")

    return results, "\n".join(messages)
