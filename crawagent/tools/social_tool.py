"""Social media extractor for public Douyin / Bilibili links.

One @tool function that detects the platform and returns structured JSON with
metadata, comments and/or media URLs. Uses only requests + stdlib; no login,
no cookies, no browser.

拆分说明（handoff §2.1）：本文件现仅为**转发层**（≈100 行）——
    - 平台实现搬至 bilibili_tool.py / douyin_tool.py
    - 通用 HTTP/下载工具搬至 social_utils.py
    - 对外签名 100% 兼容：agent.py 的 extract_social_media / download_social_media、
      test_smoke.py 的 _extract_bvid / _extract_aweme_id / _mixin_key 均原样保留
"""
import json
import re

from langchain_core.tools import tool

from crawagent.config.settings import get_settings
from crawagent.tools.social_utils import (  # noqa: F401  (re-export 供既有调用方使用)
    DESKTOP_UA,
    MOBILE_UA,
    _download_to_file,
    _extract_aweme_id,
    _extract_bvid,
    _find_ffmpeg,
    _merge_media,
    _resolve,
    _safe_name,
    _suffix,
)
from crawagent.tools.bilibili_tool import (  # noqa: F401  (_mixin_key 供 test_smoke.py)
    MIXIN_KEY_ENC_TAB,
    _mixin_key,
    bilibili_download,
    bilibili_extract,
)
from crawagent.tools.douyin_tool import douyin_extract, douyin_plan_downloads


def _extract_data(url: str, wanted: set[str]) -> dict:
    if not wanted:
        wanted = {"metadata", "comments", "media"}
    try:
        if re.search(r"(douyin\.com|iesdouyin\.com)", url):
            return douyin_extract(url, wanted)
        if re.search(r"(bilibili\.com|b23\.tv)", url):
            return bilibili_extract(url, wanted)
        return {"error": "Unsupported URL. Provide a Douyin or Bilibili link."}
    except Exception as exc:  # tool boundary: never crash the agent
        return {"error": f"Extraction failed: {exc}"}


@tool
def extract_social_media(url: str, fields: str = "metadata,comments,media") -> str:
    """从抖音或 B 站链接抽取**公开可见**的作品数据。

    兼容分享短链（v.douyin.com、b23.tv）与普通视频页长链接。
    返回 JSON 格式文本，按 fields 筛字段。

    参数：
        url: 抖音或 B 站的视频/分享 URL
        fields: 用英文逗号分隔的字段清单，支持：
            "metadata"（作品元数据）、"comments"（评论）、"media"（视频/图）
            默认 "metadata,comments,media"

    返回：
        JSON 字符串；若平台需要登录/Cookie/API 签名才能看的评论或媒体字段会为空数组。
    """
    wanted = {f.strip() for f in fields.split(",") if f.strip()}
    result = _extract_data(url, wanted)
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def download_social_media(url: str, include: str = "video,cover,comments", subdir: str = "downloads") -> str:
    """把抖音或 B 站作品的公开媒体/评论下载到本地文件。

    **仅在用户明确要求保存/下载 视频、音频、封面、图片集、评论**时使用。
    复用 extract_social_media 的免登录公开 API 先解析再下载，接口一致。

    参数：
        url: 抖音或 B 站的视频/分享 URL
        include: 英文逗号分隔的下载清单，支持：
            "video"（视频）、"audio"（音频）、"cover"（封面）、
            "images"（图集）、"comments"（评论）。默认 "video,cover,comments"
        subdir: 项目根下的子目录（默认 "downloads"）

    返回：
        JSON 汇总，每项下载附带本地绝对路径。
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
    downloads_root = settings.downloads_dir.resolve()
    # 默认 subdir="downloads" 时，等同于直接往 downloads_root/<平台>_<id> 写
    if subdir.strip("/\\") in ("downloads", ""):
        out_dir = (downloads_root / f"{platform}_{post_id}").resolve()
    else:
        out_dir = (downloads_root / subdir / f"{platform}_{post_id}").resolve()
    if not str(out_dir).startswith(str(base)):
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
        """下载单个文件并登记结果到 ``downloaded`` / ``errors``。

        Args:
            kind: 文件类型（``video`` / ``audio`` / ``cover`` / ``image``），仅用于报告。
            url_value: 下载 URL；空串视为缺数据直接返回 False。
            name: 输出文件名（不含扩展名，由调用方按 kind+url 补）。

        Returns:
            True 表示下载成功，False 表示失败（URL 为空或抛异常）。
        """
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
            extra_downloaded, extra_errors = bilibili_download(data, out_dir, title, headers)
            downloaded.extend(extra_downloaded)
            errors.extend(extra_errors)
        elif platform == "douyin":
            # 抖音：按下载清单执行；主直链失败时回退 720p
            for kind, url_value, name in douyin_plan_downloads(data):
                include_key = "images" if kind == "image" else kind
                if include_key not in include_set:
                    continue
                filename = f"{name}{_suffix(url_value, kind)}"
                ok = save(kind, url_value, filename)
                if (kind == "video" and not ok and data.get("video_url_720p")
                        and url_value != data.get("video_url_720p")):
                    save(kind, data["video_url_720p"], filename)
                if kind == "video" and downloaded:
                    downloaded[-1]["has_audio"] = True
                    downloaded[-1]["note"] = "Douyin MP4 already includes audio"

    if "audio" in include_set and platform != "douyin":
        # 抖音音频内嵌在 MP4（见 douyin audio_note），无独立音频；B 站 DASH 音轨已由
        # bilibili_download 处理，这里只兜底独立的 audio_url
        if data.get("audio_url"):
            save("audio", data["audio_url"], f"{title}{_suffix(data['audio_url'], 'audio')}")

    if "cover" in include_set and platform == "douyin":
        cover_url = data.get("cover_url") or ""
        save("cover", cover_url, f"{title}.cover{_suffix(cover_url, 'cover')}")

    if "images" in include_set and platform == "douyin":
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
