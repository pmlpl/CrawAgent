import os
import re
import requests
import subprocess
import tempfile
import shutil

from .types import VideoItem
from crawagent.config.settings import get_logger
logger = get_logger(__name__)


def extract_show_episodes(page_url: str, page=None) -> list[str]:
    """从优酷剧集/专辑页面提取所有剧集链接
    
    Args:
        page_url: 优酷专辑页面URL（如电视剧详情页）
        page: ChromiumPage 对象（可选，用于使用浏览器会话）
    
    Returns:
        所有剧集的URL列表
    """
    episode_urls = []
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.youku.com/",
        }
        
        if page is not None:
            resp = page.session.get(page_url, headers=headers, timeout=30)
        else:
            resp = requests.get(page_url, headers=headers, timeout=30)
        
        html = resp.text
        
        # 方法1: 从页面源码中提取剧集链接
        # 匹配 v.youku.com/v_show/id_xxx.html 格式
        pattern1 = r'https?://v\.youku\.com/v_show/id_([a-zA-Z0-9=]+)\.html'
        matches1 = re.findall(pattern1, html)
        for vid in matches1:
            url = f"https://v.youku.com/v_show/id_{vid}.html"
            if url not in episode_urls:
                episode_urls.append(url)
        
        # 方法2: 匹配相对路径格式
        pattern2 = r'/v_show/id_([a-zA-Z0-9=]+)\.html'
        matches2 = re.findall(pattern2, html)
        for vid in matches2:
            url = f"https://v.youku.com/v_show/id_{vid}.html"
            if url not in episode_urls:
                episode_urls.append(url)
        
        # 方法3: 从 JSON 数据中提取
        json_pattern = r'"showId"\s*:\s*"([^"]+)".*?"episodeIds"\s*:\s*"([^"]+)"'
        json_matches = re.findall(json_pattern, html, re.DOTALL)
        for show_id, ep_ids in json_matches:
            if ep_ids:
                # 可能有多个剧集ID，用逗号分隔
                ep_id_list = ep_ids.split(",")
                for ep_id in ep_id_list:
                    ep_id = ep_id.strip().strip('"')
                    if ep_id and ep_id != "0":
                        url = f"https://v.youku.com/v_show/id_{ep_id}.html"
                        if url not in episode_urls:
                            episode_urls.append(url)
        
        # 方法4: 从页面上的剧集列表数据提取
        show_data_pattern = r'"episode"\s*:\s*\[(.*?)\]'
        show_data_matches = re.findall(show_data_pattern, html, re.DOTALL)
        for episode_data in show_data_matches:
            ep_urls = re.findall(r'"id"\s*:\s*"([^"]+)"', episode_data)
            for ep_id in ep_urls:
                url = f"https://v.youku.com/v_show/id_{ep_id}.html"
                if url not in episode_urls:
                    episode_urls.append(url)
        
        logger.info(f"从专辑页面提取到 {len(episode_urls)} 个剧集链接")
        
    except Exception as e:
        logger.error(f"提取剧集失败: {e}")
    
    return episode_urls


def parse_youku_title(page_title: str) -> str:
    """从优酷页面标题提取视频名称和集数"""
    if not page_title:
        return ""

    title = page_title.replace(" - 优酷", "").strip()

    episode_match = re.search(r'第(\d+)集', title)
    if episode_match:
        episode = episode_match.group(1)
        title_clean = re.sub(r'第\d+集\s*[-_]\s*', '', title)
        title_clean = re.sub(r'[-_]\s*第\d+集', '', title_clean)
        return f"{title_clean.strip()} 第{episode}集"

    return title


def parse_fmp4_m3u8(body: str, base_url: str | None = None) -> tuple[list[str], str, list[int]]:
    """解析 fMP4 格式的子 m3u8，返回 (正片segment URL列表, 原始m3u8内容, DISCONTINUITY位置列表)
    
    关键处理：
    1. fMP4 m3u8 中有多个 #EXT-X-DISCONTINUITY 区域（正片和广告交替）
    2. 每个区域都有自己的 #EXT-X-MAP（init segment）
    3. 广告片段的 init segment 路径包含 /ad/，正片不包含
    4. 必须只返回正片片段，过滤掉所有广告
    5. 记录 DISCONTINUITY 位置（在 init segment 之前）
    """
    segs: list[str] = []
    discontinuity_positions: list[int] = []
    lines = [l.strip() for l in body.split("\n")]

    def _resolve_url(url: str) -> str:
        if url.startswith("http"):
            return url
        if base_url:
            from urllib.parse import urljoin
            return urljoin(base_url, url)
        return url

    def _is_ad_url(url: str) -> bool:
        url_lower = url.lower()
        if "/ad/" in url_lower:
            return True
        ad_domains = ("adsmind.ugdtimg.com", "ugdtimg.com", "adserver.",
                      "doubleclick.net", "googleadservices.com")
        ad_keywords = ("ads_svp_video", "ad_", "_ad.", "advertisement")
        for ad_domain in ad_domains:
            if ad_domain in url_lower:
                return True
        for ad_keyword in ad_keywords:
            if ad_keyword in url_lower:
                return True
        return False

    in_ad_section = False
    current_init_segment = None
    seg_count = 0  # 记录添加到 segs 的条目数

    i = 0
    while i < len(lines):
        line = lines[i]
        
        if line.startswith("#EXT-X-DISCONTINUITY"):
            in_ad_section = False
            current_init_segment = None
            i += 1
            continue
        
        if line.startswith("#EXT-X-MAP"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                init_url = _resolve_url(m.group(1))
                if _is_ad_url(init_url):
                    in_ad_section = True
                else:
                    in_ad_section = False
                    current_init_segment = init_url
            i += 1
            continue
        
        if line.startswith("#EXTINF"):
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                j += 1
            if j < len(lines) and lines[j]:
                seg_url = _resolve_url(lines[j])
                if not in_ad_section and not _is_ad_url(seg_url):
                    if current_init_segment and current_init_segment not in segs:
                        segs.append(current_init_segment)
                    segs.append(seg_url)
            i = j + 1
            continue
        
        i += 1

    return segs, body, discontinuity_positions


def extract_from_youku_m3u8(packets: list, page=None, page_url: str = "") -> list[VideoItem]:
    """从捕获的数据包中提取优酷 m3u8 播放列表"""
    videos: list[VideoItem] = []
    if not packets or page is None:
        return videos

    sub_m3u8_urls: list[str] = []

    for p in packets:
        try:
            purl = p.url
            if not isinstance(purl, str):
                continue
            if ".m3u8" not in purl:
                continue
            if "/ad/" in purl:
                continue
            if "valipl" in purl or "cibntv" in purl or "ott" in purl or "pstatp" in purl or "bdwsf" in purl:
                if purl not in sub_m3u8_urls:
                    sub_m3u8_urls.append(purl)
        except Exception:
            continue

    if not sub_m3u8_urls:
        return videos

    video_seg_urls: list[str] = []
    audio_seg_urls: list[str] = []
    video_discontinuity: list[int] = []
    audio_discontinuity: list[int] = []
    video_m3u8_url = ""
    audio_m3u8_url = ""

    video_m3u8_body = ""
    audio_m3u8_body = ""
    
    for sub_url in sub_m3u8_urls:
        try:
            body = _download_with_session(sub_url, page)
            if not body:
                continue
            segs, raw_body, discontinuity = parse_fmp4_m3u8(body, base_url=sub_url)
            if not segs:
                continue
            fname = sub_url.split("/")[-1].split("?")[0]
            if "0500CB" in fname or "0500000000" in fname:
                if not video_seg_urls:
                    video_seg_urls = segs
                    video_discontinuity = discontinuity
                    video_m3u8_url = sub_url
                    video_m3u8_body = raw_body
            elif "0700000000" in fname or "07000000" in fname:
                if not audio_seg_urls:
                    audio_seg_urls = segs
                    audio_discontinuity = discontinuity
                    audio_m3u8_url = sub_url
                    audio_m3u8_body = raw_body
            elif len(segs) > len(video_seg_urls):
                video_seg_urls = segs
                video_discontinuity = discontinuity
                video_m3u8_url = sub_url
                video_m3u8_body = raw_body
        except Exception:
            continue

    if video_m3u8_body:
        cleaned_body = _clean_m3u8_remove_ads(video_m3u8_body, base_url=video_m3u8_url)
        if cleaned_body:
            video_m3u8_body = cleaned_body
    if audio_m3u8_body:
        cleaned_body = _clean_m3u8_remove_ads(audio_m3u8_body, base_url=audio_m3u8_url)
        if cleaned_body:
            audio_m3u8_body = cleaned_body

    if not video_seg_urls:
        return videos

    page_title = ""
    try:
        page_title = page.title or ""
    except Exception:
        pass

    item = VideoItem()
    item.platform = "youku"
    item.title = parse_youku_title(page_title)
    if not item.title:
        item.title = "优酷视频"

    item.video_url = video_m3u8_url
    item.raw_data = {
        "_segments_video": video_seg_urls,
        "_segments_audio": audio_seg_urls,
        "_discontinuity_video": video_discontinuity,
        "_discontinuity_audio": audio_discontinuity,
        "_m3u8_video": video_m3u8_url,
        "_m3u8_audio": audio_m3u8_url,
        "_m3u8_body_video": video_m3u8_body,
        "_m3u8_body_audio": audio_m3u8_body,
        "_track_type": "fmp4",
        "_original_url": page_url,
    }

    videos.append(item)
    return videos


def _download_with_session(url: str, page) -> str | None:
    """使用 ChromiumPage 的 session 下载 URL"""
    if page is None:
        return None
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://v.youku.com/",
            "Accept": "*/*",
        }
        resp = page.session.get(url, timeout=30, headers=headers)
        if resp.status_code in (200, 206):
            text = resp.text
            if isinstance(text, str) and "#EXTM3U" in text:
                return text
            return str(text) if isinstance(text, str) else None
    except Exception:
        pass
    return None


def _clean_m3u8_remove_ads(m3u8_body: str, base_url: str) -> str:
    """从 m3u8 内容中移除所有广告区域（保留正片的多 DISCONTINUITY 结构）
    
    核心思路：
    1. 保留 m3u8 的完整结构（包括 #EXT-X-DISCONTINUITY 标签）
    2. 但跳过所有"广告区域"内的片段
    3. 广告区域判定：EXT-X-MAP 的 URI 包含 /ad/
    
    重要：必须保留 m3u8 中所有的元数据标签（#EXT-X-PLAYLIST-TYPE、#EXT-X-VERSION 等）
    """
    if not m3u8_body:
        return ""
    
    lines = m3u8_body.split("\n")
    
    def _is_ad_url(url: str) -> bool:
        if not url:
            return False
        url_lower = url.lower()
        if "/ad/" in url_lower:
            return True
        ad_domains = ("adsmind.ugdtimg.com", "ugdtimg.com", "adserver.",
                      "doubleclick.net", "googleadservices.com")
        ad_keywords = ("ads_svp_video", "ad_", "_ad.", "advertisement")
        for ad_domain in ad_domains:
            if ad_domain in url_lower:
                return True
        for ad_keyword in ad_keywords:
            if ad_keyword in url_lower:
                return True
        return False
    
    new_lines = []
    in_ad_section = False
    has_video = False
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        if stripped.startswith("#EXT-X-MAP"):
            m = re.search(r'URI="([^"]+)"', stripped)
            if m:
                init_url = m.group(1)
                if _is_ad_url(init_url):
                    in_ad_section = True
                else:
                    in_ad_section = False
                    new_lines.append(line)
            continue
        
        if stripped.startswith("#EXT-X-DISCONTINUITY"):
            if not in_ad_section:
                new_lines.append(line)
            continue
        
        if in_ad_section:
            continue
        
        if stripped.startswith("#EXTINF"):
            new_lines.append(line)
            continue
        
        if stripped.startswith("#EXT-X-KEY"):
            new_lines.append(line)
            continue
        
        if stripped and not stripped.startswith("#"):
            if not _is_ad_url(stripped):
                new_lines.append(line)
                has_video = True
            continue
        
        if stripped.startswith("#EXTM3U") or stripped.startswith("#EXT-X-VERSION") or \
           stripped.startswith("#EXT-X-TARGETDURATION") or stripped.startswith("#EXT-X-MEDIA-SEQUENCE") or \
           stripped.startswith("#EXT-X-PLAYLIST-TYPE") or stripped.startswith("#EXT-X-ENDLIST") or \
           stripped.startswith("#EXT-X-DISCONTINUITY-SEQUENCE") or stripped.startswith("#EXT-X-DATERANGE") or \
           stripped.startswith("#EXT-X-PROGRAM-DATE-TIME") or stripped.startswith("#EXT-X-KEY") or \
           stripped.startswith("#EXT-X-MAP") or stripped.startswith("#EXT-X-MEDIA") or \
           stripped.startswith("#EXT-X-STREAM-INF") or stripped.startswith("#EXT-X-I-FRAME-STREAM-INF") or \
           stripped.startswith("#EXT-X-I-FRAMES-ONLY") or stripped.startswith("#EXT-X-BYTERANGE") or \
           stripped.startswith("#EXT-X-PART") or stripped.startswith("#EXT-X-PART-INF") or \
           stripped.startswith("#EXT-X-SERVER-CONTROL") or stripped.startswith("#EXT-X-PRELOAD-HINT"):
            if not in_ad_section:
                new_lines.append(line)
            continue
        
        if not in_ad_section and stripped:
            new_lines.append(line)
    
    if not has_video:
        return m3u8_body
    
    return "\n".join(new_lines)
