from .types import VideoItem


def extract_from_douyin_aweme(data: dict, crawler=None) -> list[VideoItem]:
    """从抖音 aweme 接口提取视频"""
    videos: list[VideoItem] = []
    try:
        if not isinstance(data, dict):
            return videos

        if "aweme_list" in data:
            aweme_list = data["aweme_list"]
        elif "data" in data and isinstance(data["data"], dict):
            if "aweme_list" in data["data"]:
                aweme_list = data["data"]["aweme_list"]
            else:
                aweme_list = [data["data"]]
        else:
            aweme_list = [data]

        if not isinstance(aweme_list, list):
            return videos

        for aweme in aweme_list:
            try:
                if not isinstance(aweme, dict):
                    continue

                video_data = aweme.get("video", {})
                if not video_data:
                    continue

                play_addr = video_data.get("play_addr", {})
                url_list = play_addr.get("url_list", []) if isinstance(play_addr, dict) else []

                if not url_list:
                    url_list = video_data.get("url_list", [])

                if not url_list:
                    continue

                real_urls = [u for u in url_list
                             if isinstance(u, str) and u.startswith("http")]
                if crawler:
                    real_urls = [u for u in real_urls if crawler._is_video_file_url(u)]
                if not real_urls:
                    continue

                item = VideoItem()
                item.platform = "douyin"
                item.title = aweme.get("desc", "")[:80] or "抖音视频"
                item.video_url = real_urls[0]

                aweme_id = aweme.get("aweme_id", "")
                if aweme_id:
                    item.url = f"https://www.douyin.com/video/{aweme_id}"

                cover = video_data.get("cover", {})
                cover_list = cover.get("url_list", []) if isinstance(cover, dict) else []
                if cover_list:
                    item.cover_url = cover_list[0]

                author = aweme.get("author", {})
                if isinstance(author, dict):
                    item.author = author.get("nickname", "")

                duration = video_data.get("duration", 0)
                if duration:
                    item.duration = f"{int(duration)}s"

                item.raw_data = aweme
                videos.append(item)
            except Exception:
                continue

    except Exception:
        pass
    return videos


def extract_from_bilibili(data: dict, crawler=None) -> list[VideoItem]:
    """从 B 站 view 接口提取视频元数据"""
    videos: list[VideoItem] = []
    try:
        result = data.get("data", {}) if isinstance(data, dict) else {}
        if not result or not isinstance(result, dict):
            return videos

        title = result.get("title") or result.get("name")
        if not title:
            return videos

        video_url = None
        for key in ("video_url", "play_url", "playUrl"):
            val = result.get(key)
            if isinstance(val, str) and val.startswith("http"):
                if crawler:
                    if crawler._is_video_file_url(val):
                        video_url = val
                        break
                else:
                    video_url = val
                    break

        bvid = result.get("bvid")
        page_url = "https://www.bilibili.com/video/%s" % bvid if bvid else ""

        if not video_url and not page_url:
            return videos

        item = VideoItem()
        item.platform = "bilibili"
        item.title = str(title)[:100]
        item.url = page_url
        item.video_url = video_url or ""

        owner = result.get("owner", {})
        if isinstance(owner, dict):
            item.author = str(owner.get("name", ""))

        pic = result.get("pic", "")
        if isinstance(pic, str) and pic:
            item.cover_url = pic if pic.startswith("http") else "https:" + pic

        duration = result.get("duration", 0)
        if duration:
            item.duration = "%ss" % int(duration)

        item.raw_data = result
        videos.append(item)
    except Exception:
        pass
    return videos


def extract_from_bilibili_playurl(data: dict, crawler=None) -> list[VideoItem]:
    """从 B 站播放地址接口提取视频流 URL"""
    videos: list[VideoItem] = []
    try:
        result = data.get("data", {}) if isinstance(data, dict) else {}
        if not result:
            return videos

        durl = result.get("durl", [])
        if not isinstance(durl, list) or not durl:
            return videos

        for d in durl:
            url = d.get("url")
            if isinstance(url, str) and url.startswith("http"):
                if crawler and not crawler._is_video_file_url(url):
                    continue

                item = VideoItem()
                item.platform = "bilibili"
                item.video_url = url

                title = result.get("title") or ""
                if title:
                    item.title = str(title)[:100]

                item.raw_data = d
                videos.append(item)

        dash = result.get("dash", {})
        if isinstance(dash, dict):
            videos_list = dash.get("video", [])
            if isinstance(videos_list, list) and videos_list:
                v = videos_list[0]
                url = v.get("base_url") or v.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    if crawler and not crawler._is_video_file_url(url):
                        pass
                    else:
                        item = VideoItem()
                        item.platform = "bilibili"
                        item.video_url = url

                        title = result.get("title") or ""
                        if title:
                            item.title = str(title)[:100]

                        item.raw_data = dash
                        videos.append(item)

    except Exception:
        pass
    return videos


def extract_from_generic(data: dict, crawler=None) -> list[VideoItem]:
    """通用提取器：扫描所有 JSON 中的视频字段"""
    videos: list[VideoItem] = []

    def scan_dict(d: dict, path: str = ""):
        nonlocal videos
        for key, val in d.items():
            current_path = f"{path}.{key}" if path else key

            if isinstance(val, str) and val.startswith("http"):
                url_lower = val.lower()
                has_video_ext = any(ext in url_lower for ext in (".mp4", ".ts", ".flv", ".m4s", ".webm"))
                has_video_domain = any(domain in url_lower for domain in ("douyinvod", "bilibili", "youku"))
                if has_video_ext or has_video_domain:
                    if crawler and not crawler._is_video_file_url(val):
                        continue

                    item = VideoItem()
                    item.platform = "video_stream"
                    item.video_url = val
                    item.title = f"视频_{len(videos) + 1}"
                    item.raw_data = {"source_path": current_path}
                    videos.append(item)

            elif isinstance(val, dict):
                scan_dict(val, current_path)

    try:
        scan_dict(data)
    except Exception:
        pass

    return videos
