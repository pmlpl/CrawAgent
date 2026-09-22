"""Bilibili 平台层 — WBI 签名、视频/评论 API、DASH 下载合并。

从 social_tool.py 拆出（handoff §2.1）。公开入口：
    bilibili_extract(url, fields) — 抽取公开数据
    bilibili_download(data, out_dir, title, headers) — DASH 下载 + ffmpeg 合并
"""
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from crawagent.config.settings import get_settings
from crawagent.tools.social_utils import (
    DESKTOP_UA,
    _download_to_file,
    _extract_bvid,
    _find_ffmpeg,
    _get,
    _merge_media,
    _resolve,
)

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _wbi_sign(params: dict, img_key: str, sub_key: str) -> dict:
    """Return params with w_rid/wts appended using Bilibili's WBI algorithm."""
    signed = dict(params)
    signed["wts"] = int(time.time())
    pairs = []
    for key in sorted(signed):
        value = re.sub(r"[!'()*]", "", str(signed[key]))
        pairs.append(f"{quote(str(key), safe='')}={quote(value, safe='')}")
    query = "&".join(pairs)
    mixin = _mixin_key(img_key, sub_key)
    signed["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return signed


def _get_wbi_keys(headers: dict) -> tuple[str, str]:
    try:
        nav = _get("https://api.bilibili.com/x/web-interface/nav", headers=headers).json()
        wbi = (nav.get("data") or {}).get("wbi_img") or {}
        img_url = wbi.get("img_url", "")
        sub_url = wbi.get("sub_url", "")
        img_key = Path(urlparse(img_url).path).stem
        sub_key = Path(urlparse(sub_url).path).stem
        if len(img_key) == 32 and len(sub_key) == 32:
            return img_key, sub_key
    except (requests.RequestException, ValueError):
        pass
    return "", ""


def _bilibili_playurl(params: dict, headers: dict) -> dict:
    """Try WBI-signed playurl first, then fall back to the unsigned endpoint."""
    img_key, sub_key = _get_wbi_keys(headers)
    if img_key and sub_key:
        try:
            signed = _wbi_sign(params, img_key, sub_key)
            payload = _get(
                "https://api.bilibili.com/x/player/wbi/playurl",
                params=signed,
                headers=headers,
            ).json()
            if payload.get("code") == 0:
                return payload
        except (requests.RequestException, ValueError):
            pass
    return _get(
        "https://api.bilibili.com/x/player/playurl",
        params=params,
        headers=headers,
    ).json()


def _bilibili_media(bvid: str, cid, headers: dict) -> dict:
    media: dict = {}
    for fnval in (16, 1):
        try:
            payload = _bilibili_playurl(
                {"bvid": bvid, "cid": cid, "qn": 127, "fnval": fnval, "fourk": 1},
                headers,
            )
        except (requests.RequestException, ValueError):
            continue
        if payload.get("code") != 0:
            media["media_error"] = payload.get("message", "Playurl API error.")
            continue
        pdata = payload.get("data") or {}
        if pdata.get("quality") and not media.get("quality"):
            media["quality"] = pdata["quality"]
        if pdata.get("durl") and not media.get("video_url"):
            media["video_url"] = pdata["durl"][0].get("url", "")
        dash_data = pdata.get("dash") or {}
        if dash_data.get("video") and not media.get("dash_video_url"):
            media["dash_video_url"] = dash_data["video"][0].get("baseUrl", "")
        if dash_data.get("audio") and not media.get("dash_audio_url"):
            media["dash_audio_url"] = dash_data["audio"][0].get("baseUrl", "")
    return media


def _bilibili_comments(aid, headers: dict) -> list[dict]:
    try:
        resp = _get(
            "https://api.bilibili.com/x/v2/reply/main",
            params={"type": 1, "oid": aid, "mode": 3, "next": 0},
            headers=headers,
        )
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []
    if data.get("code") != 0:
        return []
    replies = (data.get("data") or {}).get("replies") or []
    return [
        {
            "text": (r.get("content") or {}).get("message", ""),
            "user": (r.get("member") or {}).get("uname", ""),
            "likes": r.get("like", 0),
        }
        for r in replies
        if (r.get("content") or {}).get("message")
    ]


def _bilibili(url: str, fields: set[str]) -> dict:
    bvid = _extract_bvid(url)
    if not bvid:
        bvid = _extract_bvid(_resolve(url))
    if not bvid:
        return {"platform": "bilibili", "error": "Could not resolve a Bilibili BV id from this link."}

    headers = {"User-Agent": DESKTOP_UA, "Referer": "https://www.bilibili.com/"}
    settings = get_settings()
    if settings.bilibili_cookie:
        headers["Cookie"] = settings.bilibili_cookie
    try:
        view = _get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid},
            headers=headers,
        ).json()
    except (requests.RequestException, ValueError):
        return {"platform": "bilibili", "bvid": bvid, "error": "Bilibili view API request failed."}
    if view.get("code") != 0:
        return {"platform": "bilibili", "bvid": bvid, "error": view.get("message", "Bilibili API error.")}

    data = view.get("data") or {}
    aid = data.get("aid")
    # ponytail: first part only; multi-part video needs pages[] iteration if requested
    cid = data.get("cid") or ((data.get("pages") or [{}])[0].get("cid"))
    result: dict = {"platform": "bilibili", "bvid": bvid, "aid": aid, "cid": cid}

    if "metadata" in fields:
        stat = data.get("stat") or {}
        result["title"] = data.get("title", "")
        result["description"] = data.get("desc", "")
        result["author"] = ((data.get("owner") or {}).get("name") or "")
        result["cover_url"] = data.get("pic", "")
        result["statistics"] = {
            "view": stat.get("view", 0),
            "like": stat.get("like", 0),
            "reply": stat.get("reply", 0),
            "danmaku": stat.get("danmaku", 0),
            "favorite": stat.get("favorite", 0),
            "coin": stat.get("coin", 0),
            "share": stat.get("share", 0),
        }
    if "media" in fields:
        result.update(_bilibili_media(bvid, cid, headers))
    if "comments" in fields:
        result["comments"] = _bilibili_comments(aid, headers)
    return result


def _download_bilibili_video(data: dict, out_dir: Path, title: str, headers: dict) -> tuple[list[dict], list[str]]:
    downloaded: list[dict] = []
    errors: list[str] = []
    video_url = data.get("dash_video_url")
    audio_url = data.get("dash_audio_url")
    ffmpeg = _find_ffmpeg()

    # 若没有 ffmpeg，直接用非 DASH 格式（已嵌入音频）
    if not ffmpeg and data.get("video_url"):
        try:
            path = _download_to_file(data["video_url"], out_dir / f"{title}.mp4", headers)
            downloaded.append({"type": "video", "path": str(path), "has_audio": True, "note": "downloaded via non-DASH (embedded audio)"})
        except Exception as exc:
            errors.append(f"video: {exc}")
        return downloaded, errors

    if not (video_url and audio_url):
        if data.get("video_url"):
            try:
                path = _download_to_file(data["video_url"], out_dir / f"{title}.mp4", headers)
                downloaded.append({"type": "video", "path": str(path), "note": "may be video-only"})
            except Exception as exc:
                errors.append(f"video: {exc}")
        return downloaded, errors

    raw_video = out_dir / f"{title}.video.m4s"
    raw_audio = out_dir / f"{title}.audio.m4s"
    try:
        _download_to_file(video_url, raw_video, headers)
        _download_to_file(audio_url, raw_audio, headers)
    except Exception as exc:
        errors.append(f"video/audio: {exc}")
        return downloaded, errors

    if not ffmpeg:
        # 理论上已在上方处理，但兜底
        downloaded.append({"type": "video", "path": str(raw_video)})
        downloaded.append({"type": "audio", "path": str(raw_audio)})
        downloaded.append({"note": "ffmpeg not found; video and audio saved separately. Install ffmpeg for auto-merge: https://ffmpeg.org/download.html"})
        return downloaded, errors

    merged = out_dir / f"{title}.mp4"
    try:
        _merge_media(raw_video, raw_audio, merged, ffmpeg)
        raw_video.unlink(missing_ok=True)
        raw_audio.unlink(missing_ok=True)
        downloaded.append({"type": "video", "path": str(merged), "has_audio": True})
    except Exception as exc:
        errors.append(f"merge: {exc}")
        downloaded.append({"type": "video", "path": str(raw_video)})
        downloaded.append({"type": "audio", "path": str(raw_audio)})
        downloaded.append({"note": "Audio/video merge failed; files saved separately"})

    return downloaded, errors


# ---- 公开入口（转发层与外部调用使用） ----

def bilibili_extract(url: str, fields: set[str]) -> dict:
    """公开入口：B 站公开数据抽取（视频/评论/作者）。

    Args:
        url: B 站视频 URL（含 ``BV`` 号解析）。
        fields: 要抽取的字段集合（``title`` / ``desc`` / ``author`` / ``stat`` / ``comments`` 等）。

    Returns:
        含有所需字段的 dict；缺失字段以 ``None`` 填充。
    """
    return _bilibili(url, fields)


def bilibili_download(data: dict, out_dir: Path, title: str, headers: dict) -> tuple[list[dict], list[str]]:
    """公开入口：B 站视频下载（DASH 流 + ffmpeg 合并）。

    Args:
        data: ``bilibili_extract`` 返回的数据（含 ``dash`` 字段）。
        out_dir: 输出目录。
        title: 文件名前缀（已 safe_name）。
        headers: HTTP 请求头（含 Cookie）。

    Returns:
        (downloaded, errors)：下载成功的文件列表 + 错误信息列表。
    """
    return _download_bilibili_video(data, out_dir, title, headers)
