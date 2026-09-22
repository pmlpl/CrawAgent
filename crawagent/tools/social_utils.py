"""社媒工具通用层 — UA 常量、HTTP 封装、URL 解析、文件下载（抖音/B站共用）。

从 social_tool.py 拆出（handoff §2.1）；对外无行为变化，仅供平台模块复用。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests

from crawagent.config.settings import get_settings

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _get(url: str, params: dict | None = None, headers: dict | None = None) -> requests.Response:
    settings = get_settings()
    if headers is None:
        headers = {"User-Agent": DESKTOP_UA}
    return requests.get(
        url,
        params=params,
        headers=headers,
        timeout=settings.request_timeout,
        allow_redirects=True,
    )


def _resolve(url: str, headers: dict | None = None) -> str:
    resp = _get(url, headers=headers)
    return resp.url


def _extract_bvid(url: str) -> str:
    match = re.search(r"BV[0-9A-Za-z]{10}", url)
    return match.group(0) if match else ""


def _extract_aweme_id(url: str) -> str:
    patterns = (
        r"/video/(\d+)",
        r"/note/(\d+)",
        r"/slides/(\d+)",
        r"/share/video/(\d+)",
        r"modal_id=(\d+)",
        r"aweme_id=(\d+)",
        r"item_ids=(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def _find_item(obj):
    """Return the first dict that looks like a Douyin item."""
    if isinstance(obj, dict):
        aweme_id = obj.get("aweme_id")
        if isinstance(aweme_id, str) and aweme_id.isdigit() and ("video" in obj or "images" in obj):
            return obj
        for value in obj.values():
            found = _find_item(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_item(value)
            if found:
                return found
    return None


def _parse_router_data(html: str) -> dict:
    match = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def _pick_url(obj) -> str:
    if isinstance(obj, dict):
        for url in obj.get("url_list") or []:
            if url:
                return url
    return ""


def _safe_name(value: str, limit: int = 80) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", value or "item")
    return value[:limit].strip() or "item"


def _suffix(url: str, kind: str) -> str:
    ext = Path(urlparse(url).path).suffix
    if ext and len(ext) <= 5 and ext.isascii():
        return ext
    return {"video": ".mp4", "audio": ".mp3", "cover": ".jpg", "image": ".jpg"}.get(kind, ".bin")


def _download_to_file(url: str, path: Path, headers: dict) -> Path:
    settings = get_settings()
    resp = requests.get(
        url,
        headers=headers,
        timeout=settings.request_timeout,
        allow_redirects=True,
        stream=True,
    )
    resp.raise_for_status()
    with path.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)
    return path


def _find_ffmpeg() -> str:
    settings = get_settings()
    candidates = [
        settings.ffmpeg_path,
        shutil.which("ffmpeg"),
        shutil.which("ffmpeg.exe"),
        # Common Windows installation paths
        str(Path(r"C:\ffmpeg\bin\ffmpeg.exe")),
        str(Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe")),
        str(Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"),
    ]
    for c in candidates:
        if c and Path(str(c)).exists():
            return str(c)
    return ""


def _merge_media(video_path: Path, audio_path: Path, output_path: Path, ffmpeg: str) -> None:
    result = subprocess.run(
        [ffmpeg, "-y", "-i", str(video_path), "-i", str(audio_path), "-c", "copy", str(output_path)],
        capture_output=True,
        timeout=300,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg merge failed: {stderr[:200]}")
