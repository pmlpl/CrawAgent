"""网络抓包爬虫 - 基于 DrissionPage

通过监听浏览器网络请求，从 API 响应中直接提取视频数据。
适用于：抖音、B站、优酷等动态加载的视频平台。

核心流程：
1. ChromiumPage 打开目标页面
2. 监听网络请求（aweme/post, x/web-interface/view, playurl 等）
3. 页面加载后滚动触发数据加载
4. 从 JSON 响应中提取视频 URL 列表
5. 下载视频文件
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from typing import Any

from crawagent.config.settings import get_logger
logger = get_logger(__name__)

try:
    from DrissionPage import ChromiumPage
    _DRISSION_AVAILABLE = True
except ImportError:
    ChromiumPage = None
    _DRISSION_AVAILABLE = False

import requests

from .types import VideoItem, PacketCrawlResult
from .extractors import (
    extract_from_douyin_aweme,
    extract_from_bilibili,
    extract_from_bilibili_playurl,
    extract_from_generic,
)
from .youku import extract_from_youku_m3u8
from .ffmpeg import (
    find_ffmpeg,
    remux_to_mp4,
    encode_from_local_segments,
    _generate_clean_m3u8,
)


def is_available() -> bool:
    """检查 DrissionPage 是否可用"""
    return _DRISSION_AVAILABLE


_VIDEO_EXTRACT_RULES: list[dict[str, Any]] = [
    {
        "name": "douyin_aweme_post",
        "url_pattern": r"/aweme/post/",
        "extractor": extract_from_douyin_aweme,
    },
    {
        "name": "douyin_aweme_detail",
        "url_pattern": r"/aweme/v1/web/aweme/detail/",
        "extractor": extract_from_douyin_aweme,
    },
    {
        "name": "bilibili_web_interface_view",
        "url_pattern": r"/x/web-interface/view",
        "extractor": extract_from_bilibili,
    },
    {
        "name": "bilibili_player_playurl",
        "url_pattern": r"/x/player/(wbi/)?playurl",
        "extractor": extract_from_bilibili_playurl,
    },
]


class PacketCrawler:
    """基于 DrissionPage 的网络抓包爬虫"""

    _LISTEN_TARGETS = [
        "/aweme/post/",
        "/aweme/v1/web/aweme/detail/",
        "/x/web-interface/view",
        "/x/player/",
        "/aweme/v1/",
        "douyinvod.com",
        "bilibili.com",
        "youku.com",
        "playurl",
        "playlist/m3u8",
        ".m4s",
        ".mp4",
        ".flv",
        ".ts",
        "upgcxcode",
        "api.bilibili.com",
        "valipl",
        "cibntv",
        "ott",
        "pstatp",
        "bdwsf",
    ]

    _VIDEO_FILE_MARKERS = (
        ".m4s", ".mp4", ".flv", ".ts", ".webm",
        "douyinvod.com", "upgcxcode", "ott.cibntv.net",
    )

    _API_KEYWORDS = (
        "x/player/", "x/web-interface/", "aweme/v1", "/api/",
        "callback=", "&e=ig8euxZM",
    )

    def __init__(self, headless: bool = False, cookies_file: str = None):
        """初始化抓包爬虫

        Args:
            headless: 是否无头模式运行浏览器
            cookies_file: Cookie 文件路径（支持 Netscape 格式）
        """
        self._page = None
        self._headless = headless
        self._cookies_file = cookies_file

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口 - 确保浏览器关闭"""
        self.close()

    def close(self):
        """关闭浏览器"""
        if self._page is not None:
            try:
                self._page.quit()
            except Exception:
                pass
            self._page = None

    def crawl(self, url: str, wait_seconds: int = 8, scroll_times: int = 3,
              scroll_gap: float = 1.0) -> PacketCrawlResult:
        """抓取目标页面的网络数据包，提取视频信息

        Args:
            url: 目标网页 URL
            wait_seconds: 页面加载等待总时间（秒）
            scroll_times: 滚动次数（触发更多数据加载）
            scroll_gap: 每次滚动间隔（秒）

        Returns:
            PacketCrawlResult: 抓包结果，包含提取到的视频列表
        """
        result = PacketCrawlResult(url=url)

        if not _DRISSION_AVAILABLE:
            result.error = "DrissionPage 模块未安装，请先安装：pip install DrissionPage"
            return result

        # 判断是否是第三方聚合网站（需要特殊处理 iframe 视频）
        third_party_domains = [
            "land8028.com", "nnyy.in", "555dy.vip", "dyxs.la",
            "aqy1.com", "pptv3.com", "mgtv8.com", "iqiyi3.com",
        ]
        is_third_party = any(domain in url.lower() for domain in third_party_domains)

        try:
            if self._headless:
                self._page = ChromiumPage()
                self._page.set.headless(True)
            else:
                self._page = ChromiumPage()
            
            # 加载 Cookie（如果提供了 Cookie 文件）
            if self._cookies_file and os.path.exists(self._cookies_file):
                try:
                    self._page.set.cookies.load(self._cookies_file)
                    logger.info(f"已加载 Cookie: {self._cookies_file}")
                except Exception as e:
                    logger.warning(f"Cookie 加载失败: {e}")

            self._page.listen.start(
                targets=self._LISTEN_TARGETS,
                is_regex=False,
                method=True,
            )

            self._page.get(url)

            # 等待页面加载完成（包含"正在连接请稍后..."加载动画）
            logger.debug(f"等待页面加载，wait_seconds={wait_seconds}")
            time.sleep(max(3, wait_seconds))

            # 对于第三方聚合网站，视频 URL 已在页面源码中，无需点击播放按钮
            # 但仍需等待 iframe 加载完成
            if is_third_party:
                logger.debug("第三方聚合网站，等待 iframe 加载...")
                # 等待 iframe 出现
                try:
                    self._page.wait.ele_displayed("tag:iframe", timeout=10)
                    logger.debug("iframe 已加载")
                except Exception:
                    logger.debug("iframe 加载超时")

            for _ in range(scroll_times):
                try:
                    self._page.scroll.down(500)
                    time.sleep(scroll_gap)
                except Exception:
                    break

            time.sleep(max(1, wait_seconds * 0.3))

            try:
                result.page_title = self._page.title or ""
            except Exception:
                pass

            caught_queue = self._page.listen._caught
            packets: list[Any] = []
            while not caught_queue.empty():
                p = caught_queue.get_nowait()
                if p and hasattr(p, "url") and isinstance(p.url, str) and p.url.startswith("http"):
                    packets.append(p)

            if packets:
                result.success = True
                for packet in packets:
                    try:
                        request_url = packet.url
                        status = getattr(packet.response, "status", 0) if hasattr(packet, "response") else 0

                        result.captured_requests.append({
                            "url": request_url[:200],
                            "status": status,
                        })

                        if "ott.cibntv.net" in request_url:
                            pass
                        elif self._is_video_file_url(request_url):
                            vid = VideoItem()
                            vid.video_url = request_url.split("?")[0]
                            vid.url = request_url
                            vid.title = self._extract_filename(request_url) or "视频流"
                            if "douyinvod" in request_url or "douyin" in request_url:
                                vid.platform = "douyin"
                            elif "bilibili" in request_url or "mountaintoys" in request_url or "upgcxcode" in request_url:
                                vid.platform = "bilibili"
                            else:
                                vid.platform = "video_stream"
                            result.videos.append(vid)
                            continue

                        data = None
                        try:
                            if hasattr(packet, "response") and packet.response is not None:
                                body = packet.response.body
                                if isinstance(body, dict):
                                    data = body
                                elif isinstance(body, str):
                                    try:
                                        data = json.loads(body)
                                    except json.JSONDecodeError:
                                        data = None
                        except Exception:
                            data = None

                        if not data or not isinstance(data, dict):
                            continue

                        for rule in _VIDEO_EXTRACT_RULES:
                            pattern = rule["url_pattern"]
                            if re.search(pattern, request_url, re.I):
                                extracted = rule["extractor"](data, self)
                                if extracted:
                                    result.videos.extend(extracted)
                                    break

                    except Exception:
                        continue

            # 通用视频提取：对所有网站都尝试提取视频（包括 iframe、video 标签、页面源码中的视频 URL）
            if self._page is not None:
                try:
                    # 先提取页面标题（用于视频命名）
                    page_title = self._extract_page_title()
                    logger.info(f"页面标题: {page_title}")

                    # 先尝试从页面中的 video 标签提取
                    video_tags = self._page.eles("tag:video")
                    if video_tags:
                        logger.info(f"找到 {len(video_tags)} 个 video 标签")
                        for video_tag in video_tags:
                            try:
                                video_src = video_tag.attr("src") or ""
                                if video_src and video_src.startswith("http"):
                                    if video_src not in [v.video_url for v in result.videos]:
                                        item = VideoItem()
                                        item.platform = "generic"
                                        item.title = page_title or f"网页视频_{len(result.videos)+1}"
                                        item.video_url = video_src
                                        item.url = url
                                        item.raw_data = {"_source": "video_tag", "_original_url": url}
                                        result.videos.append(item)
                                        logger.debug(f"从 video 标签提取到视频: {video_src[:80]}...")
                            except Exception:
                                continue

                    # 再尝试从 iframe 和页面源码中提取
                    iframe_videos = self._extract_iframe_video(url)
                    if iframe_videos:
                        result.videos.extend(iframe_videos)
                        result.success = True
                        # 如果提取到了 m3u8，过滤掉网络监听中的单独 ts 片段
                        has_m3u8 = any(".m3u8" in (v.video_url or "") for v in result.videos)
                        if has_m3u8:
                            logger.info("已提取到 m3u8，过滤掉单独的 ts 片段")
                            result.videos = [v for v in result.videos
                                            if not (v.video_url or "").endswith(".ts")]
                except Exception as e:
                    logger.error(f"提取失败: {e}")

            # 兜底：如果已经有视频但 success 未设置，设置为 True
            if result.videos and not result.success:
                result.success = True

            is_youku = "youku.com" in url.lower() or "v.youku" in url.lower()
            if is_youku and self._page is not None:
                try:
                    youku_videos = extract_from_youku_m3u8(packets, self._page, url)
                    if youku_videos:
                        result.videos.extend(youku_videos)
                        result.success = True
                except Exception:
                    pass

            if not result.videos and packets:
                for packet in packets:
                    try:
                        if not hasattr(packet, "response") or packet.response is None:
                            continue
                        body = packet.response.body
                        if isinstance(body, dict):
                            extracted = extract_from_generic(body, self)
                            if extracted:
                                result.videos.extend(extracted)
                    except Exception:
                        continue

            if result.videos:
                seen = set()
                unique_videos = []
                for v in result.videos:
                    key = v.video_url or v.title or ""
                    if key.startswith("http"):
                        key = key.split("?")[0]
                    if key and key not in seen:
                        seen.add(key)
                        unique_videos.append(v)
                result.videos = unique_videos

            if not result.videos:
                result.error = "未从网络数据包中找到视频地址（可能页面需要登录，或 API 结构变化）"

        except Exception as e:
            result.error = str(e)

        finally:
            try:
                if self._page and self._page.listen:
                    self._page.listen.stop()
            except Exception:
                pass

        return result

    def crawl_show_preview(self, show_url: str, wait_seconds: int = 10) -> dict:
        """预览专辑页面的剧集列表（不下载视频），保存为 JSON
        
        Args:
            show_url: 专辑页面 URL，如 https://land8028.com/weihu/1381674.html
            wait_seconds: 页面加载等待时间
            
        Returns:
            dict: {
                "success": True/False,
                "show_name": "电视剧名称",
                "show_url": "专辑页面URL",
                "episodes": [{"title": "第1集", "url": "剧集URL"}, ...],
                "json_path": "保存的JSON文件路径"
            }
        """
        result = {
            "success": False,
            "show_name": "",
            "show_url": show_url,
            "episodes": [],
            "json_path": ""
        }
        
        if not _DRISSION_AVAILABLE:
            result["error"] = "DrissionPage 模块未安装"
            return result
        
        # 检测是否是第三方聚合网站（需要非无头模式）
        third_party_domains = [
            "land8028.com", "nnyy.in", "555dy.vip", "dyxs.la",
            "aqy1.com", "pptv3.com", "mgtv8.com", "iqiyi3.com",
        ]
        is_third_party = any(domain in show_url.lower() for domain in third_party_domains)
        use_headless = not is_third_party
        
        try:
            # 初始化浏览器
            if self._page is None:
                if self._headless:
                    self._page = ChromiumPage()
                    self._page.set.headless(use_headless)
                else:
                    self._page = ChromiumPage()
            
            # 访问专辑页面
            logger.info("正在访问专辑页面...")
            logger.info(f"URL: {show_url}")
            self._page.get(show_url)
            time.sleep(max(3, wait_seconds))
            
            # 提取剧集列表
            show_info = self.extract_show_episodes(show_url)
            result["show_name"] = show_info["show_name"]
            result["episodes"] = show_info["episodes"]
            
            if result["episodes"]:
                result["success"] = True
                
                # 保存为 JSON 文件
                import json
                from datetime import datetime
                
                save_dir = "output"
                os.makedirs(save_dir, exist_ok=True)
                
                # 生成文件名
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_name = self._sanitize_filename(result["show_name"]) or "show"
                json_filename = f"{safe_name}_剧集列表_{timestamp}.json"
                json_path = os.path.join(save_dir, json_filename)
                
                # 保存 JSON
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                
                result["json_path"] = json_path
                logger.info(f"剧集列表已保存到: {json_path}")
            else:
                result["error"] = "未提取到剧集列表"
                
        except Exception as e:
            result["error"] = str(e)
            import traceback
            traceback.print_exc()
        
        return result

    @classmethod
    def _is_video_file_url(cls, url: str) -> bool:
        """判断 URL 是否是视频文件 URL"""
        if not url or not isinstance(url, str):
            return False

        url_lower = url.lower()

        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = (parsed.netloc or "").lower()
            path = (parsed.path or "").lower()
            domain_and_path = domain + path
        except Exception:
            domain_and_path = url_lower

        ad_domains = ("adsmind.ugdtimg.com", "ad.youku.com", "youku.ad.", "ads.youku.",
                      "ugdtimg.com", "adserver.", "doubleclick.net", "googleadservices.com")
        if any(ad_domain in domain for ad_domain in ad_domains):
            return False

        ad_path_keywords = ("ads_svp_video", "ad_", "_ad.", "/ad/", "advertisement")
        if any(ad_keyword in domain_and_path for ad_keyword in ad_path_keywords):
            return False

        exclude_path_keywords = ("/log/", "/api/", "/x/player/", "/x/web-interface/",
                                 "playlist/m3u8")
        if any(k in domain_and_path for k in exclude_path_keywords):
            return False

        file_extensions = (".ts", ".mp4", ".flv", ".m4s", ".webm")
        for ext in file_extensions:
            if ext in path:
                return True

        if "douyinvod.com" in domain:
            return True

        if "ott.cibntv.net" in domain:
            has_video_ext = any(ext in path for ext in (".ts", ".mp4", ".flv", ".m4s"))
            if has_video_ext:
                return True

        if "upgcxcode" in path:
            return True

        return False

    @staticmethod
    def _extract_filename(url: str) -> str | None:
        """从 URL 提取文件名"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            fname = os.path.basename(parsed.path)
            if fname:
                return fname.split("?")[0]
        except Exception:
            pass
        return None

    def download_video(self, video: VideoItem, save_dir: str = "output/video",
                       timeout: int = 60, retry: int = 2) -> str | None:
        """下载单个视频到指定目录

        Args:
            video: VideoItem 对象
            save_dir: 保存目录
            timeout: 请求超时时间（秒）
            retry: 重试次数

        Returns:
            保存的文件路径，失败返回 None
        """
        if not video.video_url:
            return None

        os.makedirs(save_dir, exist_ok=True)

        fname = self._sanitize_filename(video.title) or "video"
        basepath = os.path.join(save_dir, fname)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": video.url or self._get_referer(video),
        }

        # 处理第三方聚合网站和通用网页的视频下载
        if video.platform in ("third_party", "generic") and video.video_url:
            final_path = basepath + ".mp4"
            video_url = video.video_url
            page_url = video.url or video.raw_data.get("_original_url", "") if isinstance(video.raw_data, dict) else ""

            # 判断是否是视频流 URL（m3u8 或 baofeng10.com 的视频流）
            is_video_stream = ".m3u8" in video_url.lower() or "baofeng" in video_url.lower() or "video/moli" in video_url.lower()

            # 方案1: 使用 yt-dlp（最可靠）
            if is_video_stream:
                if ".m3u8" in video_url.lower():
                    logger.info("检测到 m3u8，使用 yt-dlp 下载...")
                else:
                    logger.info("检测到视频流，使用 yt-dlp 下载...")
                try:
                    import yt_dlp
                    ydl_opts = {
                        'format': 'bestvideo*+bestaudio/best',
                        'outtmpl': final_path,
                        'merge_output_format': 'mp4',
                        'quiet': False,
                        'no_warnings': False,
                    }
                    # 添加 referer
                    referer = page_url or video_url
                    ydl_opts['http_headers'] = {'Referer': referer}

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([video_url])
                    if os.path.exists(final_path) and os.path.getsize(final_path) > 1024 * 1024:
                        size_mb = os.path.getsize(final_path) / (1024 * 1024)
                        logger.info(f"yt-dlp 下载成功，文件大小: {size_mb:.2f} MB")
                        return final_path
                except ImportError:
                    logger.warning("yt-dlp 未安装，尝试 ffmpeg...")
                except Exception as e:
                    logger.error(f"yt-dlp 失败: {e}")

                # 方案2: 使用 ffmpeg
                logger.info("使用 ffmpeg 下载 m3u8...")
                ffmpeg_path = find_ffmpeg()
                if ffmpeg_path:
                    import subprocess
                    referer = page_url or video_url
                    cmd = [
                        ffmpeg_path, "-y", "-loglevel", "warning",
                        "-headers", f"Referer: {referer}\r\nUser-Agent: {headers['User-Agent']}\r\n",
                        "-reconnect_streamed", "1", "-reconnect_delay_max", "60",
                        "-i", video_url,
                        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                        "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "48",
                        "-sc_threshold", "0",
                        "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart",
                        "-fflags", "+genpts",
                        final_path
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
                    if result.returncode == 0 and os.path.exists(final_path) and os.path.getsize(final_path) > 1024 * 1024:
                        size_mb = os.path.getsize(final_path) / (1024 * 1024)
                        logger.info(f"ffmpeg 下载成功，文件大小: {size_mb:.2f} MB")
                        return final_path
                    else:
                        logger.error(f"ffmpeg 失败，返回码: {result.returncode}")
            else:
                # 直链 mp4，直接下载
                logger.info("直链下载...")
                try:
                    resp = requests.get(video_url, headers=headers, timeout=timeout, stream=True)
                    if resp.status_code == 200:
                        content_length = int(resp.headers.get("Content-Length", 0))
                        with open(final_path, "wb") as f:
                            downloaded = 0
                            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    if content_length > 0:
                                        pct = int(downloaded / content_length * 50)
                                        bar = "=" * pct + ">" + " " * (50 - pct)
                                        print(f"\r    [{bar}] {downloaded * 100 // content_length}%", end="", flush=True)
                        print()
                        if os.path.getsize(final_path) > 1024:
                            logger.info(f"下载完成: {os.path.basename(final_path)}")
                            return final_path
                except Exception as e:
                    logger.error(f"直链下载失败: {e}")

        if video.platform == "youku" and isinstance(video.raw_data, dict):
            track_type = video.raw_data.get("_track_type", "")
            video_segs = video.raw_data.get("_segments_video", [])
            audio_segs = video.raw_data.get("_segments_audio", [])
            video_m3u8 = video.raw_data.get("_m3u8_video", "")
            audio_m3u8 = video.raw_data.get("_m3u8_audio", "")

            if track_type == "fmp4" and video_m3u8:
                logger.info(f"开始处理 fMP4 视频，片段: {len(video_segs)}/{len(audio_segs)}")
                final_path = basepath + ".mp4"
                
                # 方案1: 使用 yt-dlp（最可靠的方案）
                logger.info("方案1: 使用 yt-dlp 下载...")
                try:
                    import yt_dlp
                    ydl_opts = {
                        'format': 'bestvideo*+bestaudio/best',
                        'outtmpl': final_path,
                        'merge_output_format': 'mp4',
                        'quiet': False,
                        'no_warnings': False,
                    }
                    
                    # 方式1: yt-dlp 直接从浏览器读取 Cookie（推荐）
                    ydl_opts['cookiesfrombrowser'] = ('chrome', None, None, None)
                    
                    # 方式2: 使用初始化时传入的 cookie 文件
                    if self._cookies_file and os.path.exists(self._cookies_file):
                        ydl_opts['cookiefile'] = self._cookies_file
                        logger.info(f"使用 Cookie 文件: {self._cookies_file}")
                    
                    # 方式3: 从 DrissionPage 会话导出 cookies
                    else:
                        browser_cookie = self._get_browser_cookie()
                        if browser_cookie:
                            ydl_opts['cookiefile'] = browser_cookie
                            logger.info("从浏览器会话导出 Cookie")
                    
                    original_url = video.raw_data.get("_original_url", video.url)
                    if not original_url or "m3u8" in original_url.lower():
                        original_url = video.url
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([original_url])
                    if os.path.exists(final_path) and os.path.getsize(final_path) > 1024 * 1024:
                        size_mb = os.path.getsize(final_path) / (1024 * 1024)
                        logger.info(f"yt-dlp 下载成功，文件大小: {size_mb:.2f} MB")
                        return final_path
                except ImportError:
                    logger.warning("yt-dlp 未安装，跳过...")
                except Exception as e:
                    logger.error(f"yt-dlp 失败: {e}")
                
                # 方案2: ffmpeg 直接下载远程 m3u8
                logger.info("方案2: ffmpeg 直接下载远程 m3u8...")
                ffmpeg_path = find_ffmpeg()
                if ffmpeg_path:
                    import subprocess
                    referer_header = f"Referer: https://v.youku.com/\r\nOrigin: https://v.youku.com\r\n"
                    cmd = [ffmpeg_path, "-y", "-loglevel", "info",
                           "-headers", referer_header,
                           "-reconnect_streamed", "1", "-reconnect_delay_max", "60",
                           "-i", video_m3u8]
                    if audio_m3u8:
                        cmd += ["-headers", referer_header, "-i", audio_m3u8]
                    cmd += [
                        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                        "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "48",
                        "-sc_threshold", "0",
                        "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart",
                        "-fflags", "+genpts",
                        final_path
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
                    if result.returncode == 0 and os.path.exists(final_path) and os.path.getsize(final_path) > 1024:
                        size_mb = os.path.getsize(final_path) / (1024 * 1024)
                        logger.info(f"方案1成功，文件大小: {size_mb:.2f} MB")
                        return final_path
                    else:
                        logger.error(f"方案1失败，返回码: {result.returncode}")
                        if result.stderr:
                            # 提取关键错误信息
                            errors = [l for l in result.stderr.split('\n') 
                                     if 'error' in l.lower() or 'fail' in l.lower() or 'invalid' in l.lower()]
                            if errors:
                                logger.error(f"错误: {errors[:2]}")
            
            # 方案2: 跳过，使用远程 m3u8 直接 copy（不做重编码）
            if track_type == "fmp4" and video_m3u8:
                logger.info("方案2: ffmpeg 直接复制远程流...")
                ffmpeg_path = find_ffmpeg()
                if ffmpeg_path:
                    import subprocess
                    cmd = [ffmpeg_path, "-y", "-loglevel", "warning",
                           "-headers", f"Referer: https://v.youku.com/\r\n",
                           "-i", video_m3u8,
                           "-c", "copy", "-movflags", "+faststart", final_path]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
                    if result.returncode == 0 and os.path.exists(final_path) and os.path.getsize(final_path) > 1024:
                        size_mb = os.path.getsize(final_path) / (1024 * 1024)
                        logger.info(f"方案2成功，文件大小: {size_mb:.2f} MB")
                        return final_path

        try:
            for attempt in range(retry + 1):
                try:
                    resp = requests.get(video.video_url, headers=headers, timeout=timeout, stream=True)
                    if resp.status_code == 200:
                        content_length = int(resp.headers.get("Content-Length", 0))
                        final_path = basepath + ".mp4"
                        with open(final_path, "wb") as f:
                            downloaded = 0
                            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    if content_length > 0:
                                        pct = int(downloaded / content_length * 50)
                                        bar = "=" * pct + ">" + " " * (50 - pct)
                                        print(f"\r    [{bar}] {downloaded * 100 // content_length}%", end="", flush=True)
                        print()
                        if os.path.getsize(final_path) > 1024:
                            logger.info(f"下载完成: {os.path.basename(final_path)}")
                            return final_path
                except Exception:
                    if attempt < retry:
                        time.sleep(1)

            headers_no_range = headers.copy()
            headers_no_range.pop("Range", None)
            resp = requests.get(video.video_url, headers=headers_no_range, timeout=timeout)
            if resp.status_code == 200:
                final_path = basepath + ".mp4"
                with open(final_path, "wb") as f:
                    f.write(resp.content)
                if os.path.getsize(final_path) > 1024:
                    logger.info(f"下载完成: {os.path.basename(final_path)}")
                    return final_path

        except Exception:
            pass

        return None

    def _download_and_concat(self, segments: list[str], out_path: str, headers: dict) -> bool:
        """下载多个片段并拼接成一个文件"""
        total = len(segments)
        if total == 0:
            return False

        with open(out_path, "wb") as f:
            success_count = 0
            for idx, seg_url in enumerate(segments):
                content = None
                try:
                    if self._page is not None:
                        resp = self._page.session.get(seg_url, headers=headers, timeout=30)
                        if resp.status_code in (200, 206):
                            content = resp.content
                    if content is None:
                        resp = requests.get(seg_url, headers=headers, timeout=30)
                        if resp.status_code in (200, 206):
                            content = resp.content
                except Exception:
                    pass

                if content:
                    f.write(content)
                    success_count += 1
                pct = int((idx + 1) / total * 50)
                bar = "=" * pct + ">" + " " * (50 - pct)
                fail_count = (idx + 1) - success_count
                status = f"✓{success_count}" if fail_count == 0 else f"✓{success_count} ✗{fail_count}"
                print(f"\r    [{bar}] {(idx+1)*100//total}%  {status}", end="", flush=True)

        print()
        return success_count > max(5, total // 2)

    def _extract_page_title(self) -> str | None:
        """从页面中提取标题信息
        
        支持多种标题提取方式：
        1. HTML title 标签
        2. h1 标签
        3. 常见的播放标题元素（如 .play-ji h1, .video-title 等）
        """
        if self._page is None:
            return None
        
        title = None
        
        # 方法1: 获取 HTML title
        try:
            title_tag = self._page.ele("tag:title")
            if title_tag:
                title = title_tag.text.strip()
        except Exception:
            pass
        
        # 方法2: 获取 h1 标签
        if not title:
            try:
                h1_tag = self._page.ele("tag:h1")
                if h1_tag:
                    title = h1_tag.text.strip()
            except Exception:
                pass
        
        # 方法3: 获取常见的播放标题元素
        if not title:
            title_selectors = [
                ".play-ji h1", ".play-title", ".video-title", ".tv-title",
                ".stui-content__head h1", ".vod-detail-title", ".video-name",
                ".movie-title", ".film-title", ".episode-title",
            ]
            for selector in title_selectors:
                try:
                    ele = self._page.ele(selector, timeout=2)
                    if ele:
                        title = ele.text.strip()
                        break
                except Exception:
                    continue
        
        # 方法4: 获取页面中的标题文本（如"第x集"）
        if not title:
            try:
                html = self._page.html
                # 匹配"第x集"格式
                episode_match = re.search(r'(第[\d一二三四五六七八九十]+集)', html)
                if episode_match:
                    title = episode_match.group(1)
            except Exception:
                pass
        
        # 清理标题（去除多余字符）
        if title:
            # 去除常见的网站后缀
            title = title.replace("在线观看", "").replace("免费观看", "")
            title = title.replace("-全集", "").replace("全集", "")
            title = title.replace(" - ", " ").replace("|", " ")
            title = re.sub(r'\s+', ' ', title).strip()
        
        return title or None

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """清理文件名中的非法字符"""
        if not filename:
            return ""
        invalid_chars = r'[\\/:*?"<>|]'
        return re.sub(invalid_chars, "_", filename)

    def _get_referer(self, video: VideoItem) -> str:
        """根据平台构造合适的 Referer"""
        if video.platform == "douyin":
            return "https://www.douyin.com/"
        elif video.platform == "bilibili":
            return "https://www.bilibili.com/"
        elif video.platform == "youku":
            return "https://v.youku.com/"
        return "https://www.douyin.com/"

    def _extract_iframe_video(self, page_url: str) -> list[VideoItem]:
        """从网页中提取视频（通用方法）

        支持多种视频提取方式：
        1. 页面源码中的 m3u8/mp4/flv 等视频 URL
        2. iframe 的 src 属性中（如 dplayer.html?url=xxx）
        3. iframe 内部的 video 标签
        4. 页面中的 video 标签
        5. baofeng10.com 等视频流地址推断
        """
        videos: list[VideoItem] = []
        if self._page is None:
            return videos

        logger.debug("自动检测网页中的视频...")
        
        # 提取页面标题（用于视频命名）
        page_title = self._extract_page_title()

        # 方法1: 从页面源码中直接提取 m3u8 URL
        try:
            html = self._page.html
            if html:
                # 匹配常见的 m3u8 URL 格式
                m3u8_patterns = [
                    r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
                    r'url[=:]\s*["\']?(https?://[^\s"\'<>]+)["\']?',
                    r'["\'](https?://[^\s"\'<>]+(?:\.mp4|\.m3u8))["\']',
                    r'src[=:]\s*["\']?(https?://[^\s"\'<>]+)["\']?',
                ]
                found_urls = set()
                for pattern in m3u8_patterns:
                    matches = re.findall(pattern, html, re.IGNORECASE)
                    for match in matches:
                        if isinstance(match, tuple):
                            match = match[0] if match else ""
                        match = match.strip().strip('"').strip("'")
                        if match and match.startswith("http") and match not in found_urls:
                            found_urls.add(match)
                            # 过滤掉广告和非视频 URL
                            if self._is_video_file_url(match) or ".m3u8" in match.lower():
                                item = VideoItem()
                                item.platform = "third_party"
                                item.title = page_title or f"第三方视频_{len(videos)+1}"
                                item.video_url = match
                                item.url = page_url
                                item.raw_data = {"_source": "iframe_page_html", "_original_url": page_url}
                                videos.append(item)
                                logger.debug(f"从页面源码提取到视频 URL: {match[:80]}...")
        except Exception as e:
            logger.error(f"页面源码提取失败: {e}")

        # 方法2: 查找 iframe 元素并提取 src
        try:
            iframes = self._page.eles("tag:iframe")
            logger.debug(f"找到 {len(iframes)} 个 iframe")
            for iframe in iframes:
                try:
                    src = iframe.attr("src")
                    if not src:
                        continue
                    logger.debug(f"iframe src: {src[:100]}...")

                    # 处理相对路径
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        from urllib.parse import urlparse
                        parsed = urlparse(page_url)
                        src = f"{parsed.scheme}://{parsed.netloc}{src}"

                    # 如果 iframe src 包含视频 URL 参数（如 dplayer.html?url=xxx）
                    if ".m3u8" in src or "url=" in src or "video" in src.lower() or "baofeng" in src.lower():
                        # 尝试从 URL 参数中提取 m3u8
                        url_match = re.search(r'[?&]url=([^&]+)', src)
                        if url_match:
                            import urllib.parse
                            video_url = urllib.parse.unquote(url_match.group(1))
                            if video_url.startswith("http"):
                                item = VideoItem()
                                item.platform = "third_party"
                                item.title = page_title or f"第三方视频_{len(videos)+1}"
                                item.video_url = video_url
                                item.url = page_url
                                item.raw_data = {"_source": "iframe_src", "_iframe_src": src, "_original_url": page_url}
                                videos.append(item)
                                logger.debug(f"从 iframe src 提取到视频 URL: {video_url[:80]}...")
                                continue

                        # 如果 iframe src 本身就是 m3u8
                        if src.startswith("http") and ".m3u8" in src:
                            item = VideoItem()
                            item.platform = "third_party"
                            item.title = page_title or f"第三方视频_{len(videos)+1}"
                            item.video_url = src
                            item.url = page_url
                            item.raw_data = {"_source": "iframe_src_direct", "_original_url": page_url}
                            videos.append(item)
                            logger.debug(f"iframe 直接指向 m3u8: {src[:80]}...")
                            continue

                        # 如果 iframe src 是 baofeng10.com 的视频流地址
                        if src.startswith("http") and ("baofeng" in src.lower() or "video/moli" in src.lower()):
                            # 尝试推断 m3u8 地址（baofeng10.com 的视频流地址通常是 xxx/index.m3u8）
                            if not src.endswith(".m3u8"):
                                m3u8_url = src.rstrip("/") + "/index.m3u8"
                                # 验证 m3u8 是否存在
                                try:
                                    resp = self._page.session.head(m3u8_url, timeout=5)
                                    if resp.status_code == 200:
                                        item = VideoItem()
                                        item.platform = "third_party"
                                        item.title = page_title or f"第三方视频_{len(videos)+1}"
                                        item.video_url = m3u8_url
                                        item.url = page_url
                                        item.raw_data = {"_source": "iframe_src_baofeng_inferred", "_iframe_src": src, "_original_url": page_url}
                                        videos.append(item)
                                        logger.debug(f"推断 baofeng m3u8 地址: {m3u8_url[:80]}...")
                                        continue
                                except Exception:
                                    pass
                            
                            # 如果推断失败，使用原始 iframe src
                            item = VideoItem()
                            item.platform = "third_party"
                            item.title = page_title or f"第三方视频_{len(videos)+1}"
                            item.video_url = src
                            item.url = page_url
                            item.raw_data = {"_source": "iframe_src_baofeng", "_iframe_src": src, "_original_url": page_url}
                            videos.append(item)
                            logger.debug(f"iframe 指向 baofeng 视频流: {src[:80]}...")
                            continue

                    # 方法3: 尝试进入 iframe 内部获取视频 URL
                    if src.startswith("http") and not src.startswith("data:"):
                        try:
                            iframe_html = self._page.session.get(src, timeout=10).text
                            if iframe_html:
                                # 匹配 m3u8
                                m3u8_matches = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', iframe_html)
                                for m3u8_url in m3u8_matches:
                                    if m3u8_url not in [v.video_url for v in videos]:
                                        item = VideoItem()
                                        item.platform = "third_party"
                                        item.title = page_title or f"第三方视频_{len(videos)+1}"
                                        item.video_url = m3u8_url
                                        item.url = page_url
                                        item.raw_data = {"_source": "iframe_inner_html", "_iframe_src": src, "_original_url": page_url}
                                        videos.append(item)
                                        logger.debug(f"从 iframe 内部提取到 m3u8: {m3u8_url[:80]}...")
                                
                                # 匹配 baofeng10.com 的视频流地址
                                baofeng_matches = re.findall(r'https?://[^\s"\'<>]*baofeng[^\s"\'<>]*', iframe_html, re.IGNORECASE)
                                for bf_url in baofeng_matches:
                                    if bf_url not in [v.video_url for v in videos]:
                                        item = VideoItem()
                                        item.platform = "third_party"
                                        item.title = page_title or f"第三方视频_{len(videos)+1}"
                                        item.video_url = bf_url
                                        item.url = page_url
                                        item.raw_data = {"_source": "iframe_inner_baofeng", "_iframe_src": src, "_original_url": page_url}
                                        videos.append(item)
                                        logger.debug(f"从 iframe 内部提取到 baofeng 视频流: {bf_url[:80]}...")
                        except Exception as e:
                            logger.error(f"进入 iframe 失败: {e}")

                    # 方法4: 通过 JavaScript 在 iframe 内获取 video 元素的 src
                    try:
                        video_src = iframe.run_js("""
                            var video = document.querySelector('video');
                            if (video) {
                                return video.src || video.currentSrc || '';
                            }
                            return '';
                        """)
                        if video_src and video_src.startswith("http") and video_src not in [v.video_url for v in videos]:
                            item = VideoItem()
                            item.platform = "third_party"
                            item.title = page_title or f"第三方视频_{len(videos)+1}"
                            item.video_url = video_src
                            item.url = page_url
                            item.raw_data = {"_source": "iframe_js_video", "_iframe_src": src, "_original_url": page_url}
                            videos.append(item)
                            logger.debug(f"通过 JS 获取到 video src: {video_src[:80]}...")
                    except Exception as e:
                        logger.error(f"JS 获取 video src 失败: {e}")

                except Exception:
                    continue
        except Exception as e:
            logger.error(f"iframe 提取失败: {e}")

        # 去重
        seen = set()
        unique = []
        for v in videos:
            key = v.video_url.split("?")[0] if v.video_url else ""
            if key and key not in seen:
                seen.add(key)
                unique.append(v)

        if unique:
            logger.info(f"共提取到 {len(unique)} 个唯一视频 URL")
        else:
            logger.debug("未提取到视频 URL")

        return unique

    def extract_show_episodes(self, show_url: str) -> dict:
        """从专辑页面提取所有剧集信息
        
        Args:
            show_url: 专辑页面 URL，如 https://land8028.com/weihu/1381674.html
            
        Returns:
            dict: {
                "show_name": "电视剧名称",
                "show_url": "专辑页面URL",
                "episodes": [{"title": "第1集", "url": "剧集URL"}, ...]
            }
        """
        from .types import ShowInfo, EpisodeInfo
        
        show_info = {
            "show_name": "",
            "show_url": show_url,
            "episodes": []
        }
        
        if self._page is None:
            logger.warning("页面未加载")
            return show_info
        
        try:
            logger.debug("正在提取剧集列表...")
            
            # 方法1: 从 id="con_playlist_1" 提取剧集列表
            try:
                playlist = self._page.ele("#con_playlist_1")
                if playlist:
                    links = playlist.eles("tag:a")
                    for link in links:
                        href = link.attr("href")
                        title = link.text.strip()
                        if href and title:
                            # 构建完整 URL
                            if not href.startswith("http"):
                                from urllib.parse import urljoin
                                href = urljoin(show_url, href)
                            show_info["episodes"].append({
                                "title": title,
                                "url": href
                            })
                    if show_info["episodes"]:
                        logger.info(f"从 con_playlist_1 提取到 {len(show_info['episodes'])} 个剧集")
            except Exception as e:
                logger.error(f"从 con_playlist_1 提取失败: {e}")
            
            # 方法2: 提取电视剧名称（从 class="video-info"）
            try:
                video_info = self._page.ele(".video-info")
                if video_info:
                    # 尝试获取标题
                    title_ele = video_info.ele("tag:h1")
                    if not title_ele:
                        title_ele = video_info.ele("tag:h2")
                    if not title_ele:
                        title_ele = video_info.ele("tag:h3")
                    if title_ele:
                        show_info["show_name"] = title_ele.text.strip()
                
                if not show_info["show_name"]:
                    # 从页面标题提取
                    page_title = self._extract_page_title()
                    if page_title:
                        show_info["show_name"] = page_title
            except Exception as e:
                logger.error(f"提取剧名失败: {e}")
            
            # 方法3: 如果方法1失败，尝试其他常见的选择器
            if not show_info["episodes"]:
                selectors = [
                    ".playlist a", ".episode-list a", ".play-list a",
                    ".stui-content__playlist a", ".playurl a"
                ]
                for selector in selectors:
                    try:
                        links = self._page.eles(selector)
                        for link in links:
                            href = link.attr("href")
                            title = link.text.strip()
                            if href and title and ("第" in title or "集" in title):
                                if not href.startswith("http"):
                                    from urllib.parse import urljoin
                                    href = urljoin(show_url, href)
                                show_info["episodes"].append({
                                    "title": title,
                                    "url": href
                                })
                        if show_info["episodes"]:
                            logger.info(f"从 {selector} 提取到 {len(show_info['episodes'])} 个剧集")
                            break
                    except Exception:
                        continue
            
            logger.info(f"剧名: {show_info['show_name']}")
            logger.info(f"共 {len(show_info['episodes'])} 集")
            
        except Exception as e:
            logger.error(f"提取失败: {e}")
        
        return show_info

    def _get_browser_cookie(self) -> str | None:
        """从浏览器获取 Cookie 用于下载
        
        优先级：
        1. 使用 PacketCrawler 初始化时传入的 cookies_file
        2. 使用 DrissionPage 当前会话的 cookies
        """
        # 方式1: 使用初始化时传入的 cookie 文件
        if self._cookies_file and os.path.exists(self._cookies_file):
            return self._cookies_file
        
        # 方式2: 从 DrissionPage 会话导出 cookies
        if self._page is not None:
            try:
                # 尝试从浏览器获取 cookies 并保存到临时文件
                cookies = self._page.cookies()
                if cookies:
                    import tempfile
                    temp_file = tempfile.NamedTemporaryFile(
                        mode='w', 
                        suffix='.txt', 
                        delete=False,
                        encoding='utf-8'
                    )
                    # 写入 Netscape 格式
                    temp_file.write("# Netscape HTTP Cookie File\n")
                    temp_file.write("# This file contains cookies for youku.com\n")
                    for cookie in cookies:
                        domain = cookie.get('domain', '.youku.com')
                        if 'youku' not in domain.lower():
                            continue
                        name = cookie.get('name', '')
                        value = cookie.get('value', '')
                        path = cookie.get('path', '/')
                        secure = 'TRUE' if cookie.get('secure', False) else 'FALSE'
                        expiry = int(cookie.get('expiry', 9999999999))
                        temp_file.write(f"{domain}\tTRUE\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n")
                    temp_file.close()
                    return temp_file.name
            except Exception:
                pass
        
        return None


def batch_download_yk_show(url: str, output_dir: str = "output/video", headless: bool = False,
                            wait_seconds: int = 8, max_episodes: int = 999) -> tuple[list[str], str]:
    """批量下载优酷剧集的所有剧集
    
    自动识别专辑页面，提取所有剧集链接并逐个下载。
    
    Args:
        url: 优酷剧集页面URL（可以是专辑页或单集页）
        output_dir: 保存目录
        headless: 是否无头模式
        wait_seconds: 页面加载等待时间
        max_episodes: 最大下载集数（默认下载全部）
    
    Returns:
        (已下载文件路径列表, 状态消息)
    """
    from .youku import extract_show_episodes
    
    downloaded_paths = []
    total_count = 0
    success_count = 0
    
    try:
        # 步骤1: 提取所有剧集链接
        logger.info("正在分析页面，提取剧集列表...")
        episode_urls = extract_show_episodes(url, page=None)
        
        if not episode_urls:
            return [], "未能提取到剧集链接，请手动提供剧集URL"
        
        # 限制下载数量
        if max_episodes < len(episode_urls):
            episode_urls = episode_urls[:max_episodes]
        
        total_count = len(episode_urls)
        logger.info(f"开始批量下载，共 {total_count} 集")
        
        # 步骤2: 逐个下载每集
        for i, ep_url in enumerate(episode_urls, 1):
            logger.info(f"[{i}/{total_count}] 正在下载...")
            
            try:
                videos, paths, msg = scrape_and_download(
                    ep_url, 
                    output_dir=output_dir, 
                    headless=headless, 
                    wait_seconds=wait_seconds,
                    max_videos=1
                )
                
                if paths:
                    downloaded_paths.extend(paths)
                    success_count += 1
                    logger.info(f"第 {i} 集下载成功")
                else:
                    logger.error(f"第 {i} 集下载失败: {msg}")
                
            except Exception as e:
                logger.error(f"第 {i} 集异常: {e}")
                continue
        
        # 总结
        logger.info(f"批量下载完成！成功: {success_count}/{total_count} 集，保存位置: {output_dir}")
        
        return downloaded_paths, f"成功下载 {success_count}/{total_count} 集"
        
    except Exception as e:
        return downloaded_paths, f"批量下载失败: {e}"


def scrape_and_download(url: str, output_dir: str = "output/video", headless: bool = False,
                        wait_seconds: int = 8, max_videos: int = 20,
                        cookies_file: str = None) -> tuple[list[VideoItem], list[str], str]:
    """一站式爬取并下载视频

    Args:
        url: 目标网页 URL
        output_dir: 保存目录
        headless: 是否无头模式
        wait_seconds: 页面加载等待时间
        max_videos: 最大下载视频数量
        cookies_file: Cookie 文件路径（支持 Netscape 格式，用于登录验证）

    Returns:
        (视频列表, 已下载文件路径列表, 状态消息)
    """
    videos: list[VideoItem] = []
    downloaded_paths: list[str] = []

    try:
        with PacketCrawler(headless=headless, cookies_file=cookies_file) as crawler:
            result = crawler.crawl(url, wait_seconds=wait_seconds)

            if not result.success or not result.videos:
                return [], [], f"抓取失败: {result.error or '未找到视频'}"

            videos = result.videos[:max_videos]
            logger.info(f"提取到 {len(videos)} 个视频")

            for i, video in enumerate(videos, 1):
                logger.info(f"[{i}/{len(videos)}] 下载: {video.title}")
                logger.debug(f"URL: {video.video_url[:100]}...")

                path = crawler.download_video(video, save_dir=output_dir)
                if path:
                    downloaded_paths.append(path)
                    logger.info(f"已保存: {path}")
                else:
                    logger.error("下载失败")

    except Exception as e:
        return videos, downloaded_paths, f"错误: {str(e)}"

    msg = f"网络抓包爬虫: 提取到 {len(videos)} 个视频，已下载 {len(downloaded_paths)} 个\n"
    msg += f"保存位置: {output_dir}/"
    return videos, downloaded_paths, msg
