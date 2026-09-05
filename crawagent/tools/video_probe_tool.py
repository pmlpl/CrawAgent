"""Video player probe tool — 用 Playwright 探测第三方视频站播放质量。

probe_video_player(page_url) 做四件事（一次性完成，返回结构化 JSON）：
1. 打开页面，监听网络流量收集 .m3u8 / .ts / .mp4 请求；统计弹窗与 iframe 数
2. 手写解析 m3u8 主清单的 #EXT-X-STREAM-INF，取最高分辨率（不引第三方 m3u8 包）
3. 首片测速：只下载第一个 .ts 分片的前 256KB 算 MB/s（不下载整片）
4. 安全扫描：检查收集到的 URL 是否有 .exe 等恶意下载
"""
import asyncio
import json
import time

import requests
from langchain_core.tools import tool

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.google.com/",
}
SPEED_TEST_BYTES = 256 * 1024  # 首片测速只拉 256KB


def _parse_master_m3u8(text: str) -> dict:
    """手写解析 m3u8 主清单，返回最高分辨率/带宽的流信息。

    主清单含 #EXT-X-STREAM-INF:RESOLUTION=1920x1080,BANDWIDTH=... 后跟子清单 URL。
    若已是媒体清单（无 STREAM-INF），返回 segments 数。
    """
    import re
    streams = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            res = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
            bw = re.search(r"BANDWIDTH=(\d+)", line)
            uri = lines[i + 1].strip() if i + 1 < len(lines) and not lines[i + 1].startswith("#") else ""
            streams.append({
                "resolution": f"{res.group(1)}x{res.group(2)}" if res else None,
                "bandwidth": int(bw.group(1)) if bw else 0,
                "uri": uri,
            })
    if streams:
        best = max(streams, key=lambda s: (s["bandwidth"], s["resolution"] or ""))
        return {"is_master": True, "best": best, "variant_count": len(streams)}
    segments = [l for l in lines if l and not l.startswith("#")]
    return {"is_master": False, "segment_count": len(segments)}


def _resolve_url(base: str, uri: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, uri)


def _speed_test(url: str) -> float:
    """下载第一个 .ts 分片的前 256KB，返回 MB/s。失败返回 0。"""
    try:
        start = time.perf_counter()
        got = 0
        with requests.get(url, headers=_UA, stream=True, timeout=15) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=65536):
                got += len(chunk)
                if got >= SPEED_TEST_BYTES:
                    break
        elapsed = time.perf_counter() - start
        return round(got / 1024 / 1024 / elapsed, 2) if elapsed > 0 and got > 0 else 0.0
    except Exception:
        return 0.0


async def _collect_page_signals(page_url: str) -> dict:
    """打开页面收集网络信号：m3u8/ts/mp4 URL、弹窗数、iframe 数。"""
    from playwright.async_api import async_playwright

    media_urls: list[str] = []   # 去重后的 m3u8/ts/mp4
    unsafe_urls: list[str] = []  # .exe 等可执行文件下载
    popup_count = 0
    page_error = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=_UA["User-Agent"])

        def on_response(resp):
            url = resp.url
            low = url.lower()
            if any(low.split("?")[0].endswith(ext) or ext in low for ext in (".m3u8", ".ts", ".mp4")):
                if url not in media_urls:
                    media_urls.append(url)
            if low.split("?")[0].endswith((".exe", ".bat", ".scr", ".apk")):
                unsafe_urls.append(url)

        page = await context.new_page()
        page.on("response", on_response)

        def on_popup(_page):
            nonlocal popup_count
            popup_count += 1

        context.on("page", on_popup)

        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(8000)  # 等播放器发起视频请求
            if not media_urls:
                # headless 下自动播放常被阻止 — 尝试点击播放（video 元素/常见播放按钮）
                try:
                    await page.evaluate("""() => {
                        const v = document.querySelector('video');
                        if (v) { v.muted = true; v.play && v.play().catch(() => {}); }
                        const btn = document.querySelector(
                            '.play-btn, .vjs-big-play-button, [class*="play"], .dplayer-play-btn');
                        if (btn) btn.click();
                    }""")
                    await page.wait_for_timeout(5000)  # 等播放器拉流
                except Exception:
                    pass
            frame_urls = [f.url for f in page.frames if f.url != page.url]
        except Exception as e:
            page_error = str(e)
            frame_urls = []
        finally:
            await browser.close()

    return {
        "media_urls": media_urls,
        "unsafe_urls": unsafe_urls,
        "popup_count": popup_count,
        "frame_urls": frame_urls[:5],
        "page_error": page_error,
    }


def _score(resolution: str | None, speed_mbs: float, popup_count: int, iframe_count: int) -> dict:
    """清晰度 / 流畅度 / 广告量 → 各 0-100 分 + 综合分。"""
    if resolution:
        try:
            h = int(resolution.split("x")[1])
        except (ValueError, IndexError):
            h = 0
        clarity = 95 if h >= 1080 else 80 if h >= 720 else 60 if h >= 480 else 40
    else:
        clarity = 30  # 未能识别清晰度

    smoothness = (95 if speed_mbs >= 2 else 80 if speed_mbs >= 1
                  else 60 if speed_mbs >= 0.5 else 40 if speed_mbs > 0 else 20)

    ads = max(0, 90 - popup_count * 25 - max(0, iframe_count - 1) * 15)

    composite = round(clarity * 0.4 + smoothness * 0.4 + ads * 0.2)
    return {"clarity": clarity, "smoothness": smoothness, "ads": ads, "composite": composite}


@tool
def probe_video_player(page_url: str) -> str:
    """探测第三方视频站播放页质量，返回结构化 JSON。

    用在 web_search 筛出来的候选视频站上，实测指标：
    - 清晰度（解析 m3u8 主清单里最高带宽的流）
    - 流畅度（首段 256KB 下载速度 MB/s，不会拉完整文件）
    - 广告烦扰度（弹窗次数 + iframe 嵌套数）
    - 安全性（检测 .exe/.apk 下载提示）

    参数：
        page_url: 实际播放页 URL（详情/播放页，不能给站点首页）。

    返回：
        JSON 字符串：{url, page_loaded, resolution, bandwidth, speed_mbs, popup_count,
        iframe_count, has_m3u8, segment_count, unsafe_downloads, scores{清晰度,
        流畅度, 广告, 综合}, error}
    """
    try:
        signals = asyncio.run(_collect_page_signals(page_url))
    except Exception as e:
        return json.dumps({"url": page_url, "page_loaded": False, "error": str(e)},
                          ensure_ascii=False)

    result = {
        "url": page_url,
        "page_loaded": signals["page_error"] is None,
        "error": signals["page_error"],
        "popup_count": signals["popup_count"],
        "iframe_count": len(signals["frame_urls"]),
        "unsafe_downloads": signals["unsafe_urls"],
        "resolution": None,
        "bandwidth": 0,
        "speed_mbs": 0.0,
        "has_m3u8": False,
        "segment_count": 0,
    }

    # 解析最高优先级的 m3u8 主清单
    m3u8_urls = [u for u in signals["media_urls"] if ".m3u8" in u.lower()]
    ts_urls = [u for u in signals["media_urls"] if ".ts" in u.lower()]
    if m3u8_urls:
        result["has_m3u8"] = True
        for m3u8_url in m3u8_urls[:3]:  # 最多试 3 个，取第一个解析成功的
            try:
                text = requests.get(m3u8_url, headers=_UA, timeout=10).text
                parsed = _parse_master_m3u8(text)
                if parsed.get("is_master"):
                    best = parsed["best"]
                    result["resolution"] = best["resolution"]
                    result["bandwidth"] = best["bandwidth"]
                    if best["uri"]:
                        # 子清单里通常列出真正的 .ts 分片
                        sub_url = _resolve_url(m3u8_url, best["uri"])
                        sub_text = requests.get(sub_url, headers=_UA, timeout=10).text
                        sub_parsed = _parse_master_m3u8(sub_text)
                        if not sub_parsed.get("is_master"):
                            result["segment_count"] = sub_parsed["segment_count"]
                            first_seg = sub_text and next(
                                (l.strip() for l in sub_text.splitlines()
                                 if l.strip() and not l.strip().startswith("#")), None)
                            if first_seg:
                                ts_urls.insert(0, _resolve_url(sub_url, first_seg))
                    break
                else:
                    result["segment_count"] = parsed["segment_count"]
                    first_seg = next((l.strip() for l in text.splitlines()
                                      if l.strip() and not l.strip().startswith("#")), None)
                    if first_seg:
                        ts_urls.insert(0, _resolve_url(m3u8_url, first_seg))
                    break
            except Exception:
                continue

    # 首片测速（只拉 256KB）
    if ts_urls:
        result["speed_mbs"] = _speed_test(ts_urls[0])

    result["scores"] = _score(result["resolution"], result["speed_mbs"],
                              result["popup_count"], result["iframe_count"])
    return json.dumps(result, ensure_ascii=False)
