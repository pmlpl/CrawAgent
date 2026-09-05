"""Douyin 平台层 — 分享链接解析、公开数据抽取、下载清单生成。

从 social_tool.py 拆出（handoff §2.1）。公开入口：
    douyin_extract(url, fields) — 抽取公开数据
    douyin_plan_downloads(data) — 生成 [(kind, url, filename_no_ext)] 下载清单
"""
import requests

from crawagent.tools._social_utils import (
    MOBILE_UA,
    _extract_aweme_id,
    _find_item,
    _get,
    _parse_router_data,
    _pick_url,
    _resolve,
    _safe_name,
)


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


# ---- 公开入口（转发层与外部调用使用） ----

def douyin_extract(url: str, fields: set[str]) -> dict:
    return _douyin(url, fields)


def douyin_plan_downloads(data: dict) -> list[tuple[str, str, str]]:
    """从 douyin_extract 结果生成下载清单 [(kind, url, filename_no_ext)]。

    kind ∈ video / audio / cover / image；文件后缀由调用方按 kind+url 补
    （_suffix(url, kind)）。视频优先 1080p 直链。
    """
    title = _safe_name(data.get("title", ""))
    plans: list[tuple[str, str, str]] = []

    video_url = data.get("video_url") or data.get("video_url_720p") or ""
    if video_url:
        plans.append(("video", video_url, title))
    if data.get("audio_url"):
        plans.append(("audio", data["audio_url"], f"{title}.audio"))
    if data.get("cover_url"):
        plans.append(("cover", data["cover_url"], f"{title}.cover"))
    for idx, img in enumerate(data.get("images") or [], start=1):
        if img:
            plans.append(("image", img, f"{title}.image{idx}"))
    return plans
