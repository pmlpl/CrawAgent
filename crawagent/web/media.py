"""从工具结果文本提取下载的媒体文件路径，生成可服务 URL。

工具结果有两种格式：
- download_social_media 返 JSON：{"downloaded":[{type,path},...]}（type 可能是
  video/cover/comment 等，cover 实为图片，按扩展名重判）
- download_images 返文本：含 "output dir: <dir>" + 行内文件名

提取后返 [{path,url,type,filename}]，url = /downloads/<rel_posix>，
路径不在 downloads_dir 下的跳过（防越界）。纯函数，幂等。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from crawagent.config.settings import get_settings

_MEDIA_EXT: dict[str, set[str]] = {
    "video": {"mp4", "webm", "mov"},
    "image": {"jpg", "jpeg", "png", "webp", "gif", "bmp"},
    "audio": {"mp3", "m4a", "aac", "wav", "ogg", "flac"},
}
_ALL_EXT = {e for exts in _MEDIA_EXT.values() for e in exts}
_FILE_RE = re.compile(r"(\S+\.(?:%s))" % "|".join(_ALL_EXT), re.IGNORECASE)
_OUTPUT_DIR_RE = re.compile(r"output\s*dir[:：]\s*(\S+)", re.IGNORECASE)


def _classify(ext: str) -> str | None:
    e = ext.lower().lstrip(".")
    for kind, exts in _MEDIA_EXT.items():
        if e in exts:
            return kind
    return None


def _to_url(abs_path: Path) -> str | None:
    """绝对路径 → /downloads/<rel_posix>；不在 downloads_dir 下返 None。"""
    dl = get_settings().downloads_dir
    try:
        rel = abs_path.relative_to(dl)
    except ValueError:
        return None
    return "/downloads/" + rel.as_posix()


def _item(abs_path_str: str, kind_hint: str | None = None) -> dict | None:
    p = Path(abs_path_str)
    kind = kind_hint if kind_hint in _MEDIA_EXT else _classify(p.suffix)
    if not kind:
        return None
    url = _to_url(p)
    if not url:
        return None
    return {"path": str(p), "url": url, "type": kind, "filename": p.name}


def extract_media(content: str) -> list[dict]:
    """从工具结果 content 提取媒体项。返 [{path,url,type,filename}]，空则 []。"""
    if not content:
        return []
    seen: set[str] = set()
    out: list[dict] = []
    # 优先 JSON：download_social_media 返 {"downloaded":[{type,path}]}
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("downloaded"), list):
        for d in data["downloaded"]:
            if not isinstance(d, dict):
                continue
            path = d.get("path")
            if not path:
                continue
            # 工具 type 可能是 video/cover/comment 等；非 media 类按扩展名重判
            hint = d.get("type")
            it = _item(path, hint)
            if it and it["path"] not in seen:
                seen.add(it["path"])
                out.append(it)
        return out
    # 文本兜底：download_images 返 "output dir: <dir>" + 行内文件名
    m = _OUTPUT_DIR_RE.search(content)
    if not m:
        return []
    base = Path(m.group(1))
    for fm in _FILE_RE.finditer(content):
        fname = fm.group(1)
        p = Path(fname)
        if not p.is_absolute():
            p = base / fname
        it = _item(str(p))
        if it and it["path"] not in seen:
            seen.add(it["path"])
            out.append(it)
    return out
