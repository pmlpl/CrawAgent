"""图片/视频批量下载工具

硬约束 save_executor：数据含图片 URL 字段（images/image_urls/thumbnails/image/cover/...）时，
自动下载图片。本工具给 Agent 显式调用入口，也可被 save_executor 间接使用。

特点：
- 支持单个 URL 或逗号/换行分隔的 URL 列表
- 自动识别扩展名（根据 content-type 或 URL 后缀）
- 同名文件自动加序号，不覆盖
- 失败的 URL 汇总返回，不影响其余
- Referer 可传，用于防盗链网站（如 haowallpaper.com）
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from langchain_core.tools import tool
from crawagent.config.settings import get_settings

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 根据 content-type 推断扩展名
_CT_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
    "image/tiff": ".tiff",
    "image/x-icon": ".ico",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}

# 根据 URL 路径后缀推断扩展名（优先级低于 content-type）
_URL_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|bmp|svg|tiff?|ico|mp4|webm|mov)(\?|$)", re.IGNORECASE)


def _split_urls(urls_arg: str) -> list[str]:
    """拆分 URL 字符串：支持逗号、换行、分号、空格分隔，同时支持 JSON 数组字符串"""
    s = urls_arg.strip()
    if not s:
        return []
    # 支持 JSON 数组
    if s.startswith("[") and s.endswith("]"):
        import json
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
    # 普通分隔
    parts = re.split(r"[\s,;，；]+", s)
    return [p.strip() for p in parts if p.strip()]


def _detect_ext(url: str, ct: str) -> str:
    ct = ct.split(";", 1)[0].strip().lower()
    if ct in _CT_EXT:
        return _CT_EXT[ct]
    m = _URL_EXT_RE.search(url)
    if m:
        return "." + m.group(1).lower()
    # 兜底按 content-type 粗分
    if ct.startswith("image/"):
        return ".img"
    if ct.startswith("video/"):
        return ".vid"
    return ".bin"


def _safe_stem(url: str, max_len=60) -> str:
    """从 URL 生成一个安全的文件 stem（不含扩展名）"""
    parsed = urlparse(url)
    # 优先用 path 的最后一段（去掉参数）
    last = parsed.path.rstrip("/").split("/")[-1]
    if last and len(last) >= 6:
        stem = last[:max_len]
    else:
        stem = (parsed.netloc + "_" + parsed.path.replace("/", "_"))[-max_len:]
    # 去掉不合法文件名字符
    return re.sub(r"[^\w\-.]", "_", stem)


def _unique_path(dir_path: Path, stem: str, ext: str) -> Path:
    """避免同名覆盖：<stem>.ext, <stem>_1.ext, <stem>_2.ext ..."""
    base = dir_path / f"{stem}{ext}"
    if not base.exists():
        return base
    i = 1
    while True:
        p = dir_path / f"{stem}_{i}{ext}"
        if not p.exists():
            return p
        i += 1


@tool
def download_images(urls: str, subdir: str = "wallpapers", referer: str = "") -> str:
    """批量下载图片或视频到项目下载目录 downloads/。

    适用场景：用户要求保存图片/视频到本地（壁纸缩略图、预告片预览、
    社交平台封面图等）。**所有媒体文件必须放到 downloads/ 子目录下**
    （项目根/downloads/<子目录名>），绝对不允许写到别的地方。

    参数：
        urls: 一个或多个 URL。支持格式：
              - 单条 URL
              - 英文逗号 / 换行 / 分号 / 空格分隔
              - JSON 数组字符串，如 '["http://a.jpg","http://b.jpg"]'
        subdir: downloads/ 下的子目录名（默认 "wallpapers"）。
                建议值："wallpapers" / "douyin_videos" / "bilibili_covers"
        referer: 可选。针对有热链保护的站点传入 Referer 头
                 （传目标站点主页 URL，例 "https://example.com/"）。

    返回：
        格式化字符串：下载成功数 + 失败 URL 清单 + 已保存本地绝对路径。
    """
    url_list = _split_urls(urls)
    if not url_list:
        return "download_images failed: no valid urls provided"

    settings = get_settings()
    base = settings.project_root.resolve()
    downloads_root = settings.downloads_dir.resolve()
    out_dir: Path = (downloads_root / subdir).resolve()
    if not out_dir.is_relative_to(downloads_root) or not str(out_dir).startswith(str(base)):
        return "download_images failed: subdir escapes downloads root"
    out_dir.mkdir(parents=True, exist_ok=True)

    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer

    successes: list[str] = []
    failures: list[tuple[str, str]] = []

    for idx, url in enumerate(url_list, 1):
        try:
            r = requests.get(url, headers=headers, timeout=60, stream=True)
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            ext = _detect_ext(url, ct)
            stem = _safe_stem(url)
            if idx <= 999:
                stem = f"{idx:03d}_{stem}"
            out_file = _unique_path(out_dir, stem, ext)
            size_bytes = 0
            with open(out_file, "wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    if chunk:
                        f.write(chunk)
                        size_bytes += len(chunk)
            size_kb = size_bytes / 1024
            if size_kb > 1024:
                size_str = f"{size_kb/1024:.1f}MB"
            else:
                size_str = f"{size_kb:.0f}KB"
            successes.append(f"{out_file.name} ({size_str})")
        except Exception as e:
            failures.append((url, str(e)))
        # 礼貌：每张图之间 0.3s（非严格，够用）
        time.sleep(0.3)

    lines = [
        f"download_images completed: {len(successes)}/{len(url_list)} succeeded",
        f"  output dir: {out_dir}",
    ]
    if successes:
        lines.append(f"  saved files:")
        for s in successes:
            lines.append(f"    - {s}")
    if failures:
        lines.append(f"  failed ({len(failures)}):")
        for u, e in failures[:10]:
            lines.append(f"    - {u[:100]} | {e}")
    return "\n".join(lines)
