"""Social media extractor for public Douyin / Bilibili links.

One @tool function that detects the platform and returns structured JSON with
metadata, comments and/or media URLs. Uses only requests + stdlib; no login,
no cookies, no browser.
"""
import json
import re
import shutil
import subprocess
import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse, quote

import requests
from langchain_core.tools import tool

from crawagent.config.settings import get_settings


MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


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


def _douyin(url: str, fields: set[str]) -> dict:
    aweme_id = _extract_aweme_id(url)
    if not aweme_id:
        aweme_id = _extract_aweme_id(_resolve(url, headers={"User-Agent": MOBILE_UA}))
    if not aweme_id:
        return {"platform": "douyin", "error": "Could not resolve a Douyin video id from this link."}

    item: dict = {}
    html = ""
    try:
        resp = _get(
            f"https://www.iesdouyin.com/share/video/{aweme_id}",
            headers={"User-Agent": MOBILE_UA},
        )
        html = resp.text
        item = _find_item(_parse_router_data(html)) or {}
    except requests.RequestException:
        item = {}

    if not item:
        try:
            data = _get(
                "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/",
                params={"item_ids": aweme_id},
                headers={"User-Agent": MOBILE_UA},
            ).json()
            item = (data.get("item_list") or [{}])[0]
        except (requests.RequestException, ValueError):
            item = {}

    result: dict = {"platform": "douyin", "aweme_id": aweme_id}
    if "metadata" in fields:
        author = (item.get("author") or {}).get("nickname", "")
        stats = item.get("statistics") or {}
        result["title"] = item.get("desc") or item.get("preview_title", "")
        result["author"] = author
        result["create_time"] = item.get("create_time", "")
        result["statistics"] = {
            "digg": stats.get("digg_count", 0),
            "comment": stats.get("comment_count", 0),
            "share": stats.get("share_count", 0),
            "collect": stats.get("collect_count", 0),
            "play": stats.get("play_count", 0),
        }
    if "media" in fields:
        video = item.get("video") or {}
        play_url = _pick_url(video.get("play_addr"))
        if "/playwm/" in play_url:
            play_url = play_url.replace("/playwm/", "/play/")
        play_url = play_url.replace("ratio=720p", "ratio=1080p")
        result["video_url"] = play_url
        result["video_url_720p"] = play_url.replace("ratio=1080p", "ratio=720p")
        result["cover_url"] = _pick_url(video.get("cover"))
        result["audio_note"] = "audio is embedded in video_url (muxed MP4)"
        if item.get("images"):
            result["images"] = [_pick_url(img) for img in item["images"] if _pick_url(img)]
    if "comments" in fields:
        result["comments"] = _douyin_comments(aweme_id)
    return result


def _douyin_comments(aweme_id: str) -> list[dict]:
    try:
        resp = _get(
            "https://www.iesdouyin.com/web/api/v2/comment/list/",
            params={
                "aweme_id": aweme_id,
                "cursor": 0,
                "count": 20,
            },
            headers={
                "User-Agent": MOBILE_UA,
                "Referer": f"https://www.iesdouyin.com/share/video/{aweme_id}",
            },
        )
        data = resp.json()
        raw_comments = data.get("comments") or []
        return [
            {
                "text": (c.get("text") or "").strip(),
                "user": ((c.get("user") or {}).get("nickname") or ""),
                "likes": c.get("digg_count", 0),
                "create_time": c.get("createTime") or c.get("create_time", 0),
                "reply_count": c.get("reply_comment_total", 0),
            }
            for c in raw_comments
            if (c.get("text") or "").strip()
        ]
    except (requests.RequestException, ValueError):
        return []


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


def _extract_data(url: str, wanted: set[str]) -> dict:
    if not wanted:
        wanted = {"metadata", "comments", "media"}
    try:
        if re.search(r"(douyin\.com|iesdouyin\.com)", url):
            return _douyin(url, wanted)
        if re.search(r"(bilibili\.com|b23\.tv)", url):
            return _bilibili(url, wanted)
        return {"error": "Unsupported URL. Provide a Douyin or Bilibili link."}
    except Exception as exc:  # tool boundary: never crash the agent
        return {"error": f"Extraction failed: {exc}"}


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
    ]
    return next((str(p) for p in candidates if p and Path(str(p)).exists()), "")


def _merge_media(video_path: Path, audio_path: Path, output_path: Path, ffmpeg: str) -> None:
    subprocess.run(
        [ffmpeg, "-y", "-i", str(video_path), "-i", str(audio_path), "-c", "copy", str(output_path)],
        check=True,
        capture_output=True,
        timeout=300,
    )


def _download_bilibili_video(data: dict, out_dir: Path, title: str, headers: dict) -> tuple[list[dict], list[str]]:
    downloaded: list[dict] = []
    errors: list[str] = []
    video_url = data.get("dash_video_url")
    audio_url = data.get("dash_audio_url")

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

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        downloaded.append({"type": "video", "path": str(raw_video)})
        downloaded.append({"type": "audio", "path": str(raw_audio)})
        downloaded.append({"note": "ffmpeg not found; merge manually"})
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

    return downloaded, errors


@tool
def extract_social_media(url: str, fields: str = "metadata,comments,media") -> str:
    """Extract publicly available data from a Douyin or Bilibili link.

    Handles share links (v.douyin.com, b23.tv) and normal video links.
    Returns JSON text with the requested fields.

    Args:
        url: A Douyin or Bilibili video/share URL.
        fields: Comma-separated list of what to fetch. Supported values:
            "metadata", "comments", "media". Default: "metadata,comments,media".

    Returns:
        A JSON string. Comments/media may be empty when the platform requires
        login, cookies, or an API signature.
    """
    wanted = {f.strip() for f in fields.split(",") if f.strip()}
    result = _extract_data(url, wanted)
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def download_social_media(url: str, include: str = "video,cover,comments", subdir: str = "downloads") -> str:
    """Download public media/comments from a Douyin or Bilibili link to local files.

    Use this only when the user explicitly asks to save/download video, audio,
    cover, images, or comments. It first resolves the link with the same
    no-login public APIs used by extract_social_media.

    Args:
        url: A Douyin or Bilibili video/share URL.
        include: Comma-separated list of things to download. Supported values:
            "video", "audio", "cover", "images", "comments".
            Default: "video,cover,comments".
        subdir: Subdirectory under the project root (default: "downloads").

    Returns:
        A JSON summary with absolute file paths for each downloaded item.
    """
    data = _extract_data(url, {"metadata", "media", "comments"})
    if data.get("error"):
        return json.dumps(data, ensure_ascii=False, indent=2)

    include_set = {s.strip() for s in include.split(",") if s.strip()}
    if not include_set:
        include_set = {"video", "cover", "comments"}

    settings = get_settings()
    platform = data.get("platform", "unknown")
    post_id = data.get("aweme_id") or data.get("bvid") or "item"
    title = _safe_name(data.get("title", ""))
    base = settings.project_root.resolve()
    out_dir = (base / subdir / f"{platform}_{post_id}").resolve()
    if not out_dir.is_relative_to(base):
        return json.dumps({"error": "subdir escapes project root"}, ensure_ascii=False)
    out_dir.mkdir(parents=True, exist_ok=True)

    headers = {"User-Agent": MOBILE_UA}
    if platform == "bilibili":
        headers = {"User-Agent": DESKTOP_UA, "Referer": "https://www.bilibili.com/"}
    else:
        headers["Referer"] = f"https://www.iesdouyin.com/share/video/{post_id}"

    downloaded: list[dict] = []
    errors: list[str] = []

    def save(kind: str, url_value: str, name: str) -> bool:
        if not url_value:
            return False
        try:
            path = _download_to_file(url_value, out_dir / name, headers)
            downloaded.append({"type": kind, "path": str(path)})
            return True
        except Exception as exc:
            errors.append(f"{kind}: {exc}")
            return False

    if "video" in include_set:
        if platform == "bilibili":
            extra_downloaded, extra_errors = _download_bilibili_video(data, out_dir, title, headers)
            downloaded.extend(extra_downloaded)
            errors.extend(extra_errors)
        elif data.get("video_url"):
            video_name = f"{title}.{_suffix(data['video_url'], 'video').lstrip('.')}"
            if not save("video", data["video_url"], video_name) and data.get("video_url_720p"):
                save("video", data["video_url_720p"], video_name)
            if platform == "douyin" and downloaded:
                downloaded[-1]["has_audio"] = True
                downloaded[-1]["note"] = "Douyin MP4 already includes audio"
        else:
            if data.get("dash_video_url"):
                save("dash_video", data["dash_video_url"], f"{title}.video.m4s")
            if data.get("dash_audio_url"):
                save("dash_audio", data["dash_audio_url"], f"{title}.audio.m4s")

    if "audio" in include_set:
        if data.get("audio_url"):
            save("audio", data["audio_url"], f"{title}.{_suffix(data['audio_url'], 'audio').lstrip('.')}")
        elif data.get("dash_audio_url"):
            save("audio", data["dash_audio_url"], f"{title}.audio.m4s")

    if "cover" in include_set:
        save("cover", data.get("cover_url"), f"{title}.cover{_suffix(data.get('cover_url', ''), 'cover')}")

    if "images" in include_set:
        for idx, image_url in enumerate(data.get("images") or [], start=1):
            save("image", image_url, f"{title}.image{idx}{_suffix(image_url, 'image')}")

    if "comments" in include_set:
        comments = data.get("comments") or []
        comments_path = out_dir / "comments.json"
        comments_path.write_text(json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")
        downloaded.append({"type": "comments", "path": str(comments_path), "count": len(comments)})

    return json.dumps(
        {"platform": platform, "output_dir": str(out_dir), "downloaded": downloaded, "errors": errors},
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    # Runnable self-check for the URL parsers; no network required.
    assert _extract_bvid("https://www.bilibili.com/video/BV1xx411c7mD?p=1") == "BV1xx411c7mD"
    assert _extract_aweme_id("https://www.douyin.com/video/7123456789012345678") == "7123456789012345678"
    assert _extract_aweme_id("https://v.douyin.com/abc123/") == ""
    assert _mixin_key(
        "7cd084941338484aae1ad9425b84077c",
        "4932caff0ff746eab6f01bf08b70ac45",
    ) == "ea1db124af3c7062474693fa704f4ff8"
    print("social_tool self-check passed")
