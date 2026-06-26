"""视频元数据爬虫"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config.settings import get_logger

logger = get_logger(__name__)


@dataclass
class VideoItem:
    """单个视频资源的元数据"""
    title: str = ""                     # 视频标题
    site: str = ""                      # 网站 (youku / bilibili / qq / iqiyi / other)
    page_url: str = ""                  # 来源页 URL
    description: str = ""               # 简介/描述
    directors: list[str] = field(default_factory=list)   # 导演
    actors: list[str] = field(default_factory=list)      # 演员
    tags: list[str] = field(default_factory=list)        # 标签/类型 (如 剧情/动作/2024)
    episodes: list[str] = field(default_factory=list)    # 剧集列表 (多集时)
    episode_count: int = 0              # 总集数
    play_count: str = ""                # 播放量
    rating: str = ""                    # 评分
    stream_urls: list[str] = field(default_factory=list) # 检测到的流地址 (m3u8/mp4/flv)
    poster_urls: list[str] = field(default_factory=list) # 海报/封面图
    meta: dict[str, str] = field(default_factory=dict)   # 其他元数据键值对


def _detect_video_site(url: str) -> str:
    """从 URL 判断视频站点类型"""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError as e:
        logger.debug(f"URL 解析失败: {e}")
        return "other"
    if "youku" in host:
        return "youku"
    if "bilibili" in host:
        return "bilibili"
    if "v.qq" in host or "film.qq" in host:
        return "qq"
    if "iqiyi" in host:
        return "iqiyi"
    if "youtube" in host:
        return "youtube"
    if "douyin" in host:
        return "douyin"
    if "mgtv" in host:
        return "mgtv"
    if "sohu" in host:
        return "sohu"
    return "other"


def extract_video_metadata(html: str, page_url: str) -> VideoItem:
    """从页面 HTML 中提取视频元数据

    策略:
    1. 从 <meta> 标签 (og:title, og:description, og:image 等) 提取通用信息
    2. 从常见结构中提取标题、简介
    3. 从 script/文本中提取 m3u8 等流地址
    4. 提取剧集/分集列表
    """
    item = VideoItem(page_url=page_url)
    item.site = _detect_video_site(page_url)

    if not html:
        return item

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        logger.debug(f"BeautifulSoup 解析失败: {e}")
        return item

    # ---- 1. Meta 标签通用提取 ----
    # og:title / og:description / og:image
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        content = (meta.get("content") or "").strip()
        if not content:
            continue
        if prop in ("og:title", "title"):
            item.title = item.title or content
        elif prop in ("og:description", "description"):
            item.description = item.description or content
        elif prop in ("og:image", "image"):
            item.poster_urls.append(content)
        elif prop == "og:video":
            item.stream_urls.append(content)

    # 回退: <title> 标签
    if not item.title:
        try:
            t_tag = soup.find("title")
            if t_tag:
                item.title = t_tag.get_text(strip=True)
        except Exception as e:
            logger.debug(f"标题提取失败: {e}")

    # ---- 2. 从页面文本中提取: 导演/演员 (通用模式) ----
    # 模式: "导演：xxx" / "主演：xxx" / "类型：xxx"
    text_fragments = []
    for tag_name in ("p", "span", "div", "li"):
        for t in soup.find_all(tag_name):
            txt = t.get_text(" ", strip=True)
            if 3 <= len(txt) <= 80:
                text_fragments.append(txt)

    keywords_map = {
        "导演": "directors",
        "主演": "actors",
        "演员": "actors",
        "类型": "tags",
        "分类": "tags",
        "年份": "tags",
        "年代": "tags",
        "地区": "tags",
        "国家": "tags",
        "简介": "description",
        "剧情": "description",
    }

    for txt in text_fragments:
        matched = False
        for zh_key, field_name in keywords_map.items():
            # 匹配 "导演：xxx" / "导演: xxx" / "导演 xxx"
            for sep in ("：", ":", " ", " - "):
                key = zh_key + sep
                if txt.startswith(key) and len(txt) > len(key) + 1:
                    value = txt[len(key):].strip().strip("，,。.")
                    matched = True
                    # 拆分多个值
                    parts = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
                    if field_name == "description":
                        if not item.description:
                            item.description = value
                    elif field_name == "directors":
                        for p in parts:
                            if p and p not in item.directors:
                                item.directors.append(p)
                    elif field_name == "actors":
                        for p in parts:
                            if p and p not in item.actors:
                                item.actors.append(p)
                    elif field_name == "tags":
                        for p in parts:
                            if p and p not in item.tags:
                                item.tags.append(p)
                    break
            if matched:
                break

    # ---- 3. 提取剧集列表 (ul/ol/列表中的数字项) ----
    # 匹配 "第X集" / "EP X" / 纯数字 1 2 3 ...
    episode_hrefs: list[str] = []
    episode_texts: list[str] = []

    # 寻找包含数字 + 集/episode 的链接
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True)
        href = a["href"]
        if not txt or len(txt) > 30:
            continue
        # 匹配: "第1集", "01", "EP 01", "预告1" 等
        is_episode = (
            ("集" in txt and any(c.isdigit() for c in txt))
            or (txt.isdigit() and len(txt) <= 4)
            or txt.lower().startswith("ep")
            or txt.lower().startswith("第")
        )
        if is_episode:
            if href.startswith(("http://", "https://", "//")):
                episode_hrefs.append(href)
            episode_texts.append(txt)

    # 去重保序
    seen_ep: set[str] = set()
    for t in episode_texts:
        if t not in seen_ep and len(seen_ep) < 200:
            item.episodes.append(t)
            seen_ep.add(t)
    item.episode_count = len(item.episodes)

    # ---- 4. 从 HTML 文本中正则提取流地址 (m3u8/mp4/flv) ----
    html_raw = html
    # 4a. 匹配 .m3u8 URL
    for m in re.finditer(r'"(https?://[^"\s]+?\.m3u8[^"]*?)"', html_raw):
        url = m.group(1)
        if url not in item.stream_urls:
            item.stream_urls.append(url)
    # 匹配单引号包裹
    for m in re.finditer(r"'(https?://[^'\s]+?\.m3u8[^']*?)'", html_raw):
        url = m.group(1)
        if url not in item.stream_urls:
            item.stream_urls.append(url)
    # 4b. 匹配 .mp4 URL (非图片/非图标)
    for m in re.finditer(r'"(https?://[^"\s]+?\.mp4[^"]*?)"', html_raw):
        url = m.group(1)
        if not any(x in url.lower() for x in (".jpg", ".png", "icon", "avatar")):
            if url not in item.stream_urls:
                item.stream_urls.append(url)
    # 4c. 提取内嵌 window.playerArgs / videoData 等 (B站/优酷/腾讯常有)
    for m in re.finditer(r'["\'](https?://[^\s"\']{10,})["\']', html_raw):
        url = m.group(1)
        lower = url.lower()
        if any(ext in lower for ext in (".m3u8", ".mp4", ".flv", ".ts")):
            if url not in item.stream_urls:
                item.stream_urls.append(url)

    # ---- 5. 封面图: 从 img 中提取海报 ----
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        alt = (img.get("alt") or "").lower()
        if src and (not alt or "cover" in alt or "海报" in alt or "封面" in alt):
            full_url = src if src.startswith(("http://", "https://")) else urljoin(page_url, src)
            if full_url not in item.poster_urls:
                item.poster_urls.append(full_url)

    # ---- 6. 播放量/评分: 文本模式匹配 ----
    for txt in text_fragments:
        # 播放量: "播放量: 1.2亿" / "1.2亿播放"
        if "播放" in txt and any(c.isdigit() for c in txt):
            item.play_count = txt
        # 评分: "9.5分" / "评分: 9.5"
        if "分" in txt and any(c.isdigit() for c in txt) and "." in txt:
            item.rating = txt

    return item


def extract_video_streams(html: str, page_url: str) -> list[str]:
    """仅提取流 URL (轻量级, 用于快速扫描)"""
    if not html:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for pattern in (
        r'"(https?://[^"\s]+?\.m3u8[^"]*?)"',
        r"'(https?://[^'\s]+?\.m3u8[^']*?)'",
        r'"(https?://[^"\s]+?\.mp4[^"]*?)"',
        r"'(https?://[^'\s]+?\.mp4[^']*?)'",
        r'"(https?://[^"\s]+?\.flv[^"]*?)"',
    ):
        for m in re.finditer(pattern, html):
            u = m.group(1)
            if u not in seen and not any(x in u.lower() for x in (".jpg", ".png", "icon", "avatar")):
                seen.add(u)
                urls.append(u)
    # 兜底: 在整页 HTML 中扫描可能的流地址
    for m in re.finditer(r'["\'](https?://[^\s"\']{15,})["\']', html):
        u = m.group(1)
        lower = u.lower()
        if any(ext in lower for ext in (".m3u8", ".mp4", ".flv", ".ts")) and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def smart_scrape_videos(crawler_instance: Any, page_url: str) -> tuple[VideoItem, str]:
    """智能视频页爬取 - 针对视频网站优化

    说明:
      主流视频网站 (优酷/爱奇艺/腾讯/B站等) 的视频流通常:
      1. 需要登录会员才能观看高清
      2. 使用加密的 m3u8/DASH 协议
      3. 视频文件本身无法被简单下载

      所以这个函数聚焦于可做到的:
      - 提取标题/简介/演员/剧集等元数据
      - 检测是否能拿到视频流 URL
      - 给用户清晰的提示

    Args:
        crawler_instance: Crawler 实例（用于复用浏览器）
        page_url: 视频页 URL

    Returns:
        (VideoItem, 状态信息字符串)
    """
    messages: list[str] = []

    # 探测站点
    site = _detect_video_site(page_url)
    site_zh = {
        "youku": "优酷",
        "bilibili": "B站",
        "qq": "腾讯视频",
        "iqiyi": "爱奇艺",
        "youtube": "YouTube",
        "douyin": "抖音",
        "mgtv": "芒果TV",
        "sohu": "搜狐视频",
        "other": "其他",
    }.get(site, site)
    messages.append(f"🎬 识别站点: {site_zh} ({site})")

    # 用 Crawler 爬取 (会自动选策略: 基础+浏览器回退)
    result = crawler_instance.fetch(page_url)
    if not result.success:
        return VideoItem(page_url=page_url, site=site), f"❌ 页面爬取失败: {result.error}"

    messages.append(f"✅ 页面获取成功 (HTTP {result.status_code or 200}, 策略: {result.strategy or 'basic'})")

    # 从 HTML 中提取元数据
    video = extract_video_metadata(result.html, page_url)
    video.site = site

    # 补充: 浏览器爬取也没拿到东西时, 尝试从基础爬虫的 text 中提取
    if not video.title and result.text:
        # 取前 3 行非空文本作为标题
        lines = [l.strip() for l in result.text.split("\n") if l.strip()]
        if lines:
            video.title = lines[0][:60]

    messages.append(f"📝 标题: {video.title or '(未提取到)'}")
    if video.description:
        messages.append(f"📄 简介: {video.description[:120]}{'...' if len(video.description) > 120 else ''}")
    if video.directors:
        messages.append(f"🎥 导演: {', '.join(video.directors[:5])}")
    if video.actors:
        messages.append(f"🎭 演员: {', '.join(video.actors[:8])}")
    if video.tags:
        messages.append(f"🏷️  标签: {', '.join(video.tags[:6])}")
    if video.episodes:
        show_ep = ", ".join(video.episodes[:10])
        if video.episode_count > 10:
            show_ep += f" ... (共 {video.episode_count} 集)"
        messages.append(f"📺 剧集: {show_ep}")
    if video.play_count:
        messages.append(f"📈 播放量: {video.play_count}")
    if video.rating:
        messages.append(f"⭐ 评分: {video.rating}")

    if video.stream_urls:
        messages.append(f"🔗 检测到 {len(video.stream_urls)} 个可能的流地址 (m3u8/mp4):")
        for u in video.stream_urls[:5]:
            messages.append(f"   - {u[:100]}{'...' if len(u) > 100 else ''}")
        if len(video.stream_urls) > 5:
            messages.append(f"   ... 还有 {len(video.stream_urls) - 5} 个")
    else:
        messages.append("🔗 流地址: 未检测到明文流地址 (多数视频站使用加密/分段协议, 需登录+专有播放器)")

    if video.poster_urls:
        messages.append(f"🖼️  海报/封面: {len(video.poster_urls)} 张")

    return video, "\n".join(messages)
