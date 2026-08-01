"""媒体下载器（P3-2 + P7-1）：yt-dlp 视频 + httpx 流式文件下载

支持：
- yt-dlp：YouTube / Bilibili / 抖音 等视频平台（cookie 透传）
- httpx 流式：图片 / PDF / 压缩包 等普通文件（支持断点续传）
- 下载进度回调
- 视频信息提取（不下载，仅获取元数据）— P7-1
- 播放列表/多集下载 — P7-1

设计原则：
- yt-dlp 可选（缺包时降级为 httpx 直链下载）
- 失败不抛异常，返回带 error 字段的结果字典
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from loguru import logger

from crawagent.output.organizer import FileOrganizer, extract_domain, sanitize_filename

# Content-Type → 扩展名映射（修复 GAP-002）
_CONTENT_TYPE_EXT_MAP = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/x-icon": "ico",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/x-matroska": "mkv",
    "video/x-flv": "flv",
    "video/quicktime": "mov",
    "video/x-msvideo": "avi",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "weba",
    "application/pdf": "pdf",
    "application/zip": "zip",
    "application/x-tar": "tar",
    "application/gzip": "gz",
    "application/x-gzip": "gz",
    "application/x-bzip2": "bz2",
    "application/x-7z-compressed": "7z",
    "application/json": "json",
    "application/xml": "xml",
    "text/html": "html",
    "text/plain": "txt",
    "text/css": "css",
    "text/javascript": "js",
    "text/csv": "csv",
}


class MediaDownloader:
    """媒体下载器：yt-dlp 视频 + httpx 流式文件。

    用法：
        dl = MediaDownloader(base_dir="~/crawagent/media")
        result = await dl.download("https://www.youtube.com/watch?v=xxx", title="视频标题")
    """

    def __init__(self, base_dir: str = "./output/media"):
        self.base_dir = os.path.expanduser(base_dir)
        self.organizer = FileOrganizer(base_dir=self.base_dir)

    async def download(
        self,
        url: str,
        title: str = "",
        ext: str = "",
        cookies_file: str = "",
        prefer_yt_dlp: bool = True,
    ) -> Dict[str, Any]:
        """智能下载：自动判断用 yt-dlp 还是 httpx。

        Args:
            url: 下载 URL
            title: 文件标题（用于生成文件名）
            ext: 强制扩展名（留空则自动推断）
            cookies_file: yt-dlp cookie 文件路径（用于会员内容）
            prefer_yt_dlp: 视频站点优先用 yt-dlp

        Returns:
            {"success": bool, "path": str, "url": str, "title": str, "size": int, "error": str}
        """
        if not url:
            return self._error_result(url, title, "missing url")

        # 判断是否需要 yt-dlp
        is_video_site = self._is_video_site(url)
        use_yt_dlp = prefer_yt_dlp and is_video_site

        if use_yt_dlp:
            result = await self._download_yt_dlp(url, title, ext, cookies_file)
            if result.get("success"):
                return result
            logger.debug(f"yt-dlp 失败，回退 httpx: {result.get('error')}")
            # 回退 httpx

        return await self._download_httpx(url, title, ext)

    def _is_video_site(self, url: str) -> bool:
        """判断 URL 是否属于已知视频站点。"""
        video_domains = (
            "youtube.com", "youtu.be", "bilibili.com", "douyin.com",
            "vimeo.com", "twitter.com", "x.com", "instagram.com",
            "tiktok.com", "nicovideo.jp", "weibo.com",
        )
        domain = extract_domain(url).lower()
        return any(d in domain for d in video_domains)

    async def _download_yt_dlp(
        self,
        url: str,
        title: str,
        ext: str,
        cookies_file: str,
    ) -> Dict[str, Any]:
        """使用 yt-dlp 下载视频。"""
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return self._error_result(
                url, title, "yt-dlp 未安装，请运行 pip install yt-dlp"
            )

        # 文件名模板
        safe_title = sanitize_filename(title) if title else "%(title)s"
        out_ext = ext or "%(ext)s"
        output_template = os.path.join(self.base_dir, f"{safe_title}.{out_ext}")
        os.makedirs(self.base_dir, exist_ok=True)

        opts = {
            "outtmpl": output_template,
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "retries": 2,
        }
        if cookies_file:
            if os.path.isfile(cookies_file):
                opts["cookiefile"] = cookies_file
            else:
                logger.warning(f"cookie 文件不存在: {cookies_file}")

        try:
            # yt-dlp 是同步库，放线程池跑
            def _run():
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return info

            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, _run)

            if info is None:
                return self._error_result(url, title, "yt-dlp 未提取到信息（可能视频不可用）")

            # 获取实际下载文件路径
            downloaded_path = ydl_prepare_filename(info, output_template)
            if not os.path.isfile(downloaded_path):
                # 模板可能用了 %(ext)s，找最接近的文件
                downloaded_path = self._find_downloaded_file(safe_title)

            if not downloaded_path or not os.path.isfile(downloaded_path):
                return self._error_result(url, title, "下载完成但找不到文件")

            size = os.path.getsize(downloaded_path)
            real_title = info.get("title", title) if isinstance(info, dict) else title
            return {
                "success": True,
                "path": downloaded_path,
                "url": url,
                "title": real_title,
                "size": size,
                "engine": "yt-dlp",
                "ext": os.path.splitext(downloaded_path)[1].lstrip("."),
                "error": "",
            }
        except Exception as e:
            return self._error_result(url, title, f"yt-dlp 下载失败: {e}")

    async def _download_httpx(
        self,
        url: str,
        title: str,
        ext: str,
    ) -> Dict[str, Any]:
        """使用 httpx 流式下载普通文件（支持断点续传）。"""
        try:
            import httpx
        except ImportError:
            return self._error_result(url, title, "httpx 未安装")

        # 推断扩展名
        if not ext:
            # 从 URL 路径提取
            from urllib.parse import urlparse
            path = urlparse(url).path
            if "." in os.path.basename(path):
                ext = path.rsplit(".", 1)[-1].lower()
            else:
                # URL 无扩展名，先发 HEAD 请求读 Content-Type
                ext = await self._infer_ext_from_content_type(url) or "bin"
        # 清理扩展名
        ext = sanitize_filename(ext).split("_")[0] or "bin"

        safe_title = sanitize_filename(title) if title else sanitize_filename(
            os.path.basename(urlparse(url).path) or "download"
        )
        filename = f"{safe_title}.{ext}"
        target_path = os.path.join(self.base_dir, filename)
        os.makedirs(self.base_dir, exist_ok=True)

        # 断点续传：检查已下载部分
        existing_size = 0
        if os.path.isfile(target_path):
            existing_size = os.path.getsize(target_path)

        headers = {}
        mode = "wb"
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"
            mode = "ab"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=15.0),
                follow_redirects=True,
                trust_env=False,
            ) as client:
                async with client.stream("GET", url, headers=headers) as resp:
                    # 416 表示 Range 不满足（文件已完整）
                    if resp.status_code == 416:
                        size = os.path.getsize(target_path)
                        return {
                            "success": True,
                            "path": target_path,
                            "url": url,
                            "title": title,
                            "size": size,
                            "engine": "httpx",
                            "ext": ext,
                            "error": "",
                            "resumed": False,
                        }

                    # 非 2xx 且非 206（partial content）
                    if resp.status_code not in (200, 206):
                        # 删除半截文件
                        if mode == "ab" and existing_size > 0:
                            # 续传失败，删除重新下
                            os.remove(target_path)
                        return self._error_result(
                            url, title, f"HTTP {resp.status_code}"
                        )

                    # 200 表示不支持断点续传，覆盖重写
                    if resp.status_code == 200 and mode == "ab":
                        mode = "wb"
                        existing_size = 0

                    with open(target_path, mode) as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)

            size = os.path.getsize(target_path)
            return {
                "success": True,
                "path": target_path,
                "url": url,
                "title": title,
                "size": size,
                "engine": "httpx",
                "ext": ext,
                "error": "",
                "resumed": existing_size > 0 and size > existing_size,
            }
        except Exception as e:
            return self._error_result(url, title, f"httpx 下载失败: {e}")

    def _find_downloaded_file(self, title_prefix: str) -> Optional[str]:
        """在 base_dir 下查找标题前缀匹配的已下载文件。"""
        if not os.path.isdir(self.base_dir):
            return None
        for name in os.listdir(self.base_dir):
            if name.startswith(title_prefix) and os.path.isfile(
                os.path.join(self.base_dir, name)
            ):
                return os.path.join(self.base_dir, name)
        return None

    async def _infer_ext_from_content_type(self, url: str) -> str:
        """通过 HEAD 请求读取 Content-Type 推断扩展名（修复 GAP-002）"""
        try:
            import httpx
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                follow_redirects=True,
                trust_env=False,
            ) as client:
                resp = await client.head(url)
                content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                return _CONTENT_TYPE_EXT_MAP.get(content_type, "")
        except Exception:
            return ""

    def _error_result(self, url: str, title: str, error: str) -> Dict[str, Any]:
        return {
            "success": False,
            "path": "",
            "url": url,
            "title": title,
            "size": 0,
            "engine": "",
            "ext": "",
            "error": error,
        }

    # ==================== P7-1: 视频信息提取 + 播放列表下载 ====================

    async def extract_info(
        self,
        url: str,
        cookies_file: str = "",
    ) -> Dict[str, Any]:
        """提取视频信息（不下载）

        Args:
            url: 视频 URL
            cookies_file: cookie 文件路径

        Returns:
            {"success", "title", "duration", "thumbnail", "formats", "is_playlist", "entries", "error"}
        """
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return {"success": False, "error": "yt-dlp 未安装"}

        opts = {
            "noplaylist": False,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
        }
        if cookies_file and os.path.isfile(cookies_file):
            opts["cookiefile"] = cookies_file

        try:
            def _run():
                with YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False)

            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, _run)

            if not info:
                return {"success": False, "error": "无法提取视频信息"}

            # 播放列表
            if info.get("_type") == "playlist" and "entries" in info:
                entries = []
                for entry in info["entries"]:
                    if entry:
                        entries.append({
                            "title": entry.get("title", ""),
                            "url": entry.get("url", ""),
                            "duration": entry.get("duration", 0),
                            "thumbnail": entry.get("thumbnail", ""),
                        })
                return {
                    "success": True,
                    "is_playlist": True,
                    "title": info.get("title", ""),
                    "playlist_count": len(entries),
                    "entries": entries,
                }

            # 单个视频
            formats = []
            for f in info.get("formats", []):
                formats.append({
                    "format_id": f.get("format_id", ""),
                    "ext": f.get("ext", ""),
                    "resolution": f.get("resolution", ""),
                    "filesize": f.get("filesize", 0),
                    "vcodec": f.get("vcodec", ""),
                    "acodec": f.get("acodec", ""),
                })

            return {
                "success": True,
                "is_playlist": False,
                "title": info.get("title", ""),
                "duration": info.get("duration", 0),
                "thumbnail": info.get("thumbnail", ""),
                "uploader": info.get("uploader", ""),
                "view_count": info.get("view_count", 0),
                "formats": formats,
                "url": url,
            }
        except Exception as e:
            return {"success": False, "error": f"信息提取失败: {e}"}

    async def download_playlist(
        self,
        url: str,
        cookies_file: str = "",
        max_items: int = 0,
    ) -> Dict[str, Any]:
        """下载播放列表（多集视频）

        Args:
            url: 播放列表 URL
            cookies_file: cookie 文件路径
            max_items: 最多下载多少集（0 = 全部）

        Returns:
            {"success", "downloaded", "failed", "total", "items", "error"}
        """
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return {"success": False, "error": "yt-dlp 未安装"}

        # 先提取播放列表信息
        info_result = await self.extract_info(url, cookies_file)
        if not info_result.get("success"):
            return {"success": False, "error": info_result.get("error", "提取失败")}

        if not info_result.get("is_playlist"):
            # 不是播放列表，用普通下载
            result = await self.download(url, cookies_file=cookies_file)
            return {
                "success": result["success"],
                "total": 1,
                "downloaded": 1 if result["success"] else 0,
                "failed": 0 if result["success"] else 1,
                "items": [result],
                "error": result.get("error", ""),
            }

        entries = info_result.get("entries", [])
        if max_items > 0:
            entries = entries[:max_items]

        os.makedirs(self.base_dir, exist_ok=True)

        # 逐集下载
        items = []
        downloaded = 0
        failed = 0

        for i, entry in enumerate(entries):
            entry_url = entry.get("url", "")
            entry_title = entry.get("title", f"第{i+1}集")

            logger.info(f"[Playlist] 下载第 {i+1}/{len(entries)} 集: {entry_title}")

            result = await self.download(
                entry_url,
                title=entry_title,
                cookies_file=cookies_file,
            )

            if result.get("success"):
                downloaded += 1
            else:
                failed += 1

            items.append({
                "index": i + 1,
                "title": entry_title,
                "url": entry_url,
                "success": result.get("success", False),
                "path": result.get("path", ""),
                "size": result.get("size", 0),
                "error": result.get("error", ""),
            })

        return {
            "success": downloaded > 0,
            "total": len(entries),
            "downloaded": downloaded,
            "failed": failed,
            "items": items,
            "error": "",
        }


def ydl_prepare_filename(info: dict, template: str) -> str:
    """从 yt-dlp info 字典和模板推断最终文件名。"""
    try:
        from yt_dlp import YoutubeDL
        # 用 YoutubeDL.prepare_filename 推断
        opts = {"outtmpl": template, "noprogress": True, "quiet": True}
        with YoutubeDL(opts) as ydl:
            return ydl.prepare_filename(info)
    except Exception:
        # 退化：用 info 里的 ext 替换模板
        ext = info.get("ext", "mp4") if isinstance(info, dict) else "mp4"
        if "%(ext)s" in template:
            return template.replace("%(ext)s", ext)
        return template


__all__ = ["MediaDownloader"]
