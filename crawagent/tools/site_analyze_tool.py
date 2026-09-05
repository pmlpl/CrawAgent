"""站点结构分析工具 — 用 Playwright 深入分析视频站点的结构。

analyze_site_structure(page_url) 做三件事：
1. 提取剧集列表（集数、是否有完整剧集、是否有多播放源）
2. 检测播放器类型（DPlayer / H5Player / iframe / video 标签）
3. 提取站点导航结构（分类、更新信息）
"""
import asyncio
import json

from langchain_core.tools import tool

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def _analyze(page_url: str) -> dict:
    from playwright.async_api import async_playwright

    result = {
        "url": page_url,
        "episode_count": 0,
        "episode_links": [],
        "has_multiple_sources": False,
        "player_type": "unknown",
        "iframe_player_urls": [],
        "nav_categories": [],
        "site_name": "",
        "error": None,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=_UA)
        page = await context.new_page()

        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # 站点名
            result["site_name"] = await page.title() or ""

            # 剧集列表：常见选择器（ul/li 里的 a 链接，含"第X集"或数字）
            episode_selectors = [
                ".stui-content__playlist li a",
                ".playlist li a",
                ".video-list li a",
                ".module-play-list a",
                ".tab-content a[href*='play']",
                "ul.myui-content__list a",
                ".stui-pannel__bottom a",
                "a[href*='vod-play']",
                ".playlist-content a",
                ".play-list a",
            ]
            for sel in episode_selectors:
                links = await page.query_selector_all(sel)
                if len(links) > 2:
                    for link in links:
                        text = await link.inner_text()
                        href = await link.get_attribute("href")
                        if text.strip():
                            result["episode_links"].append({
                                "text": text.strip(),
                                "url": href or "",
                            })
                    result["episode_count"] = len(result["episode_links"])
                    break

            # 如果没找到，用通用方式：所有含"集"或纯数字的链接
            if result["episode_count"] == 0:
                all_links = await page.query_selector_all("a")
                for link in all_links:
                    text = (await link.inner_text()).strip()
                    if text and (text.endswith("集") or text.isdigit()):
                        href = await link.get_attribute("href")
                        result["episode_links"].append({"text": text, "url": href or ""})
                result["episode_count"] = len(result["episode_links"])

            # 播放器类型检测
            for ptype, sel in [
                ("dplayer", ".dplayer, #dplayer, [class*='dplayer']"),
                ("h5player", ".html5player, .h5-player, [class*='html5']"),
                ("videojs", ".video-js, .vjs-tech"),
                ("jwplayer", ".jwplayer, .jw-video"),
                ("iframe", "iframe[src*='player'], iframe[src*='play']"),
                ("video", "video"),
            ]:
                els = await page.query_selector_all(sel)
                if els:
                    result["player_type"] = ptype
                    if ptype == "iframe":
                        for el in els:
                            src = await el.get_attribute("src")
                            if src:
                                result["iframe_player_urls"].append(src)
                    break

            # 多播放源检测（"播放源1" "播放源2" 等切换按钮）
            source_tabs = await page.query_selector_all(
                "[class*='playlist-tab'], .stui-pannel__head, .module-tab-item, "
                ".tab-list .tab-item, [class*='source-tab']"
            )
            result["has_multiple_sources"] = len(source_tabs) > 1

            # 导航分类
            nav_links = await page.query_selector_all("nav a, .nav a, .header a, .menu a")
            for link in nav_links[:15]:
                text = (await link.inner_text()).strip()
                if text and len(text) < 12:
                    result["nav_categories"].append(text)

        except Exception as e:
            result["error"] = str(e)
        finally:
            await browser.close()

    return result


@tool
def analyze_site_structure(page_url: str) -> str:
    """分析视频站的结构：剧集清单、播放器类型、站点导航。

    调完 probe_video_player 之后再用它做**更深入的结构信息**补充：
    - 剧集总数有多少（完整度检查）
    - 是否有多个播放源切换
    - 使用什么播放器技术（DPlayer / H5Player / iframe 嵌套等）
    - 站点导航分类

    参数：
        page_url: 视频详情页 / 剧集列表页 URL（不要给站点首页）。

    返回：
        JSON 字符串：{episode_count, episode_links, player_type,
        has_multiple_sources, nav_categories, site_name, error}
    """
    try:
        data = asyncio.run(_analyze(page_url))
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"url": page_url, "error": str(e)}, ensure_ascii=False)
