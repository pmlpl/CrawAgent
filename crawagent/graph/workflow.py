"""LangGraph 工作流 - CrawAgent 的大脑

工作流:
    用户输入 → 意图解析 → 策略选择 → 爬取执行 → (可选) LLM 解析 → 输出汇总
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from ..config.settings import Settings, get_logger

logger = get_logger(__name__)

from ..llm.factory import LLMFactory
from ..tools import BaseCrawler, Crawler, CrawlResult, extract_urls, looks_like_url
from ..tools.base_crawler import (
    extract_image_urls, download_images, WallpaperItem,
    smart_scrape_wallpapers,
    VideoItem, smart_scrape_videos,
    scrape_douyin_search, scrape_douyin_video, DouyinVideoItem,
)

# 网络抓包爬虫（可选依赖）
try:
    from ..tools.packet_crawler import (
        PacketCrawler,
        VideoItem as PacketVideoItem,
        scrape_and_download as packet_scrape_and_download,
        is_available as packet_is_available,
    )
    _PACKET_AVAILABLE = True
except ImportError:
    _PACKET_AVAILABLE = False
    PacketCrawler = None
    PacketVideoItem = None
    packet_scrape_and_download = None
    packet_is_available = None


# ============================================================
# State - 工作流状态对象
# ============================================================

CrawlIntent = Literal["crawl", "analyze", "chat", "unknown"]


@dataclass
class CrawState:
    """CrawAgent 的工作流状态"""

    # 输入
    user_input: str = ""

    # 意图解析
    intent: CrawlIntent = "unknown"
    target_urls: list[str] = field(default_factory=list)

    # 策略选择
    strategy: str = "basic"   # basic / browser (后续扩展)
    need_llm_parse: bool = False  # 是否需要 LLM 帮忙解析数据
    need_image_download: bool = False  # 是否下载图片

    # 执行
    crawl_results: list[CrawlResult] = field(default_factory=list)
    extracted_data: list[dict[str, Any]] = field(default_factory=list)
    downloaded_images: list[dict[str, str]] = field(default_factory=list)  # 下载的图片/视频
    image_output_dir: str = "output/img"  # 图片输出目录
    max_wallpapers: int = 12  # 壁纸爬虫最多下载数量
    downloaded_wallpapers: list[WallpaperItem] = field(default_factory=list)  # 智能壁纸爬虫结果
    is_wallpaper_site: bool = False  # 是否识别为壁纸网站

    # 视频元数据爬取
    scraped_videos: list[VideoItem] = field(default_factory=list)  # 视频元数据爬取结果
    is_video_site: bool = False  # 是否识别为视频网站
    video_output_dir: str = "output/video"  # 视频元数据/流信息输出目录

    # 抖音搜索页面爬取
    is_douyin_search: bool = False  # 是否为抖音搜索页面
    douyin_videos: list[DouyinVideoItem] = field(default_factory=list)  # 抖音视频列表
    douyin_download_results: list[tuple[str, bool, str]] = field(default_factory=list)  # 下载结果

    # 抖音视频详情页爬取
    is_douyin_video: bool = False  # 是否为抖音视频详情页
    douyin_video_results: list[tuple[str, str | None]] = field(default_factory=list)  # 抖音视频下载结果 (url, filepath)

    # 网络抓包爬虫（抖音用户页、B 站视频等）
    use_packet_crawler: bool = False  # 是否使用抓包方式
    packet_videos: list[Any] = field(default_factory=list)  # 抓包提取到的视频元数据
    packet_downloaded: list[str] = field(default_factory=list)  # 抓包成功下载的文件路径
    packet_status_msg: str = ""  # 抓包状态消息

    # 浏览器模式
    debug_mode: bool = False       # 是否开启有头调试
    force_browser: bool | None = None  # None=自动检测, True=强制浏览器, False=只用httpx

    # LLM 解析结果
    llm_summary: str = ""

    # 错误
    errors: list[str] = field(default_factory=list)

    # 最终给用户的答复
    final_reply: str = ""


# ============================================================
# 节点 - Nodes
# ============================================================

# ---- 节点 1: 意图解析 ----

_INTENT_KEYWORDS = {
    "crawl": ["爬", "抓", "抓取", "爬虫", "crawl", "scrape", "get", "下载", "拉取"],
    "analyze": ["分析", "总结", "摘要", "总结一下", "分析一下", "analyze", "summary"],
    "chat": ["聊天", "对话", "闲聊", "问", "解释", "介绍", "什么是", "如何"],
}


def node_parse_intent(state: CrawState) -> CrawState:
    """从用户输入中解析意图、URL 和自定义路径"""
    text = state.user_input.strip()

    # 1) 提取 URL
    state.target_urls = extract_urls(text)

    # 2) 提取用户自定义的输出路径
    # 匹配模式："放在xxx"、"保存到xxx"、"保存路径xxx"、"路径xxx"、"输出到xxx"
    # 兼容路径带引号的情况：放在"C:\Users\Downloads" 或 放在D:\Video
    path_patterns = [
        r'放在\s*(.+?)(?:[，,。\s]|$)',
        r'保存到\s*(.+?)(?:[，,。\s]|$)',
        r'保存路径\s*(.+?)(?:[，,。\s]|$)',
        r'路径\s*(.+?)(?:[，,。\s]|$)',
        r'输出到\s*(.+?)(?:[，,。\s]|$)',
    ]
    for pattern in path_patterns:
        match = re.search(pattern, text)
        if match:
            custom_path = match.group(1).strip()
            custom_path = custom_path.strip('"').strip("'")
            custom_path = custom_path.rstrip("下").rstrip("。").rstrip("，").rstrip(",").strip()
            if custom_path and os.path.isabs(custom_path):
                state.video_output_dir = custom_path
                state.image_output_dir = custom_path
                logger.debug(f"  [debug] 识别到自定义输出路径: {custom_path}")
                break

    # 3) 简单意图识别
    text_lower = text.lower()
    scores: dict[str, int] = {k: 0 for k in _INTENT_KEYWORDS}
    for intent, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[intent] += 1

    # 如果有 URL，优先当成爬取意图
    if state.target_urls:
        scores["crawl"] += 3

    # 选出得分最高的意图
    best_intent = max(scores.items(), key=lambda x: x[1])
    if best_intent[1] > 0:
        state.intent = best_intent[0]  # type: ignore[assignment]
    elif state.target_urls:
        state.intent = "crawl"
    else:
        state.intent = "chat"

    return state


# ---- 节点 2: 策略选择 ----

def node_choose_strategy(state: CrawState) -> CrawState:
    """根据意图和目标 URL 选择爬取策略"""
    if state.intent in ("chat", "unknown") and not state.target_urls:
        state.strategy = "chat"
        state.need_llm_parse = True
        return state

    # Phase 1 只实现基础爬虫
    # 未来可以加入: 判断是否需要浏览器(有 JS 动态内容)、是否需要代理等
    state.strategy = "basic"

    # 如果用户提到"总结 / 分析 / 中文摘要"等，就触发 LLM 解析
    parse_keywords = ("总结", "分析", "摘要", "中文", "整理", "extract", "summary", "parse")
    if any(kw in state.user_input.lower() for kw in parse_keywords):
        state.need_llm_parse = True
    else:
        state.need_llm_parse = False

    # 检测是否需要下载图片：用户提到"下载"、"图片"、"壁纸"、"保存"等
    image_keywords = (
        "下载", "图片", "壁纸", "保存", "image", "images", "picture",
        "photo", "jpg", "png", "photo", "img", "wallpaper",
    )
    if any(kw in state.user_input.lower() for kw in image_keywords):
        state.need_image_download = True

    # 检测是否需要使用网络抓包爬虫
    # 关键词：抓包、数据包、video_url、aweme、packet、network、抖音、b站、bilibili、视频、优酷、youku、下载视频、视频下载
    packet_keywords = (
        "抓包", "数据包", "aweme", "packet", "网络数据包",
        "抖音用户", "抖音主页", "douyin", "bilibili", "b站",
        "优酷", "youku", "下载视频", "视频下载", "爬取视频",
    )
    
    # 默认对所有包含视频相关关键词的请求使用抓包爬虫
    has_video_keyword = any(kw in state.user_input.lower() for kw in packet_keywords)
    
    # 检测 URL 是否是已知的视频网站
    known_video_domains = [
        "douyin.com", "bilibili.com", "v.youku.com", "youku.com",
        "land8028.com", "nnyy.in", "555dy.vip", "dyxs.la",
        "aqy1.com", "pptv3.com", "mgtv8.com", "iqiyi3.com",
        "iqiyi.com", "v.qq.com", "mgtv.com", "cntv.cn",
        "youtube.com", "bilibili.tv", "acfun.cn", "tucao.cc",
        "kuaishou.com", "ixigua.com", "haokan.baidu.com",
        "baofeng10.com", "moli.tv", "le.com", "sohu.com",
        "tv.cctv.com", "tv.sohu.com", "film.sohu.com",
    ]
    
    is_video_site = False
    for url in state.target_urls:
        url_lower = url.lower()
        for domain in known_video_domains:
            if domain in url_lower:
                is_video_site = True
                break
        if is_video_site:
            break
    
    # 如果用户请求视频相关内容，或者 URL 是已知视频网站，都使用抓包爬虫
    # 如果用户输入了 URL 但没有明确说明类型，也尝试使用抓包爬虫（可能是视频网站）
    if has_video_keyword or is_video_site or (state.target_urls and not state.need_image_download):
        if _PACKET_AVAILABLE:
            state.use_packet_crawler = True
            state.strategy = "packet"

    return state


# ---- 节点 3: 执行爬取 ----

def node_crawl(state: CrawState) -> CrawState:
    """执行爬取 - 支持壁纸网站智能爬取、视频网站元数据爬取、普通图片下载"""
    if state.intent == "chat":
        return state

    if not state.target_urls:
        state.errors.append("没有在你的请求中找到可爬取的 URL。请在输入中包含完整的网址，例如 https://example.com")
        return state

    # ===== 网络抓包爬虫（优先：抖音用户页、B 站视频页等动态内容）=====
    if state.use_packet_crawler and _PACKET_AVAILABLE:
        logger.info(f"\n  [抓包爬虫] 开始处理 {len(state.target_urls)} 个 URL...")
        
        # 检测是否是专辑页面（用于批量下载预览）
        show_page_keywords = ["weihu", "lianxuju", "zongyi", "dianying", "dianshiju"]
        is_show_page = any(kw in url.lower() for url in state.target_urls for kw in show_page_keywords)
        
        for url in state.target_urls:
            logger.debug(f"  [抓包爬虫] URL: {url[:80]}...")
            logger.debug(f"  [抓包爬虫] 输出目录: {state.video_output_dir}")
            
            # 第三方聚合视频网站（如 land8028.com）需要非无头模式才能正确加载 iframe
            third_party_domains = [
                "land8028.com", "nnyy.in", "555dy.vip", "dyxs.la",
                "aqy1.com", "pptv3.com", "mgtv8.com", "iqiyi3.com",
            ]
            is_third_party = any(domain in url.lower() for domain in third_party_domains)
            use_headless = not state.debug_mode and not is_third_party
            
            if is_third_party:
                logger.debug(f"  [抓包爬虫] 检测到第三方聚合网站，使用非无头模式")
            
            # 检测是否是专辑页面（需要批量提取剧集）
            show_page_keywords = ["weihu", "lianxuju", "zongyi", "dianying", "dianshiju"]
            is_show_page = any(kw in url.lower() for kw in show_page_keywords)
            
            if is_show_page:
                # 专辑页面：批量提取剧集列表，保存为 JSON（不下载视频）
                logger.debug(f"  [抓包爬虫] 检测到专辑页面，将提取剧集列表...")
                from crawagent.tools.packet_crawler import PacketCrawler
                
                with PacketCrawler(headless=use_headless) as crawler:
                    preview_result = crawler.crawl_show_preview(url, wait_seconds=15)
                    
                    if preview_result.get("success"):
                        show_name = preview_result.get("show_name", "未知剧名")
                        episodes = preview_result.get("episodes", [])
                        json_path = preview_result.get("json_path", "")
                        
                        status_msg = f"""📺 剧集列表已保存（预览模式）
━━━━━━━━━━━━━━━━━━━━━━━━
🎬 剧名: {show_name}
📁 文件: {json_path}
━━━━━━━━━━━━━━━━━━━━━━━━
📋 剧集列表（共 {len(episodes)} 集）:"""
                        
                        for ep in episodes[:10]:  # 只显示前10集
                            status_msg += f"\n  • {ep['title']}"
                        
                        if len(episodes) > 10:
                            status_msg += f"\n  ... 还有 {len(episodes) - 10} 集"
                        
                        status_msg += """

━━━━━━━━━━━━━━━━━━━━━━━━
💡 提示: JSON 文件已保存，如需下载请说"下载这部剧"或"确认下载" """
                        
                        state.packet_status_msg = status_msg
                        state.show_preview = preview_result  # 保存预览结果，供后续下载使用
                        logger.info(f"  [抓包爬虫] ✅ 剧集列表已保存: {json_path}")
                    else:
                        error_msg = preview_result.get("error", "未知错误")
                        state.errors.append(f"提取剧集列表失败: {error_msg}")
                        logger.error(f"  [抓包爬虫] ❌ 提取失败: {error_msg}")
                
                continue
            
            videos, downloaded, status_msg = packet_scrape_and_download(
                url,
                output_dir=state.video_output_dir,
                wait_seconds=15,
                max_videos=20,
                headless=use_headless,
            )
            logger.info(f"  [抓包爬虫] 结果: {len(videos)} 个视频, {len(downloaded)} 个已下载")
            state.packet_videos.extend(videos)
            state.packet_downloaded.extend(downloaded)
            if state.packet_status_msg:
                state.packet_status_msg += "\n" + status_msg
            else:
                state.packet_status_msg = status_msg
        return state

    crawler = Crawler(headless=not state.debug_mode)

    if state.force_browser is not None:
        crawler.set_browser_mode(state.force_browser)

    for url in state.target_urls:

        # ===== 智能壁纸爬虫（专为 haowallpaper 等网站优化）=====
        is_hwallpaper = "haowallpaper" in url.lower() or "hwallpaper" in url.lower()
        if (is_hwallpaper or state.need_image_download) and "homeView" in url:
            # 调用智能壁纸爬虫：首页 → 详情页 → 下载主视频
            wallpapers, status_msg = smart_scrape_wallpapers(
                crawler, url, state.image_output_dir, max_wallpapers=state.max_wallpapers
            )
            state.downloaded_wallpapers.extend(wallpapers)
            state.is_wallpaper_site = True

            # 添加基础爬取结果（保留流程）
            r = crawler.fetch(url)
            state.crawl_results.append(r)

            # 也提取文本元数据（用于列表展示）
            if r.success and r.strategy == "browser" and r.text and len(r.text) > 50:
                lines = [line.strip() for line in r.text.split('\n') if line.strip()]
                wps = _extract_wallpapers(lines)
                if wps:
                    state.extracted_data.extend(wps)

            continue

        # ===== 抖音搜索页面爬取（下载视频）=====
        if crawler.is_douyin_search(url):
            douyin_videos, download_results, status_msg = scrape_douyin_search(
                crawler, url, state.video_output_dir, max_downloads=10
            )
            state.douyin_videos.extend(douyin_videos)
            state.douyin_download_results.extend(download_results)
            state.is_douyin_search = True

            r = crawler.fetch(url)
            state.crawl_results.append(r)

            continue

        # ===== 抖音视频详情页爬取（单个视频下载）=====
        if crawler.is_douyin_video(url):
            filepath, status_msg = scrape_douyin_video(
                crawler, url, state.video_output_dir, use_third_party=True
            )
            state.douyin_video_results.append((url, filepath))
            state.is_douyin_video = True

            continue

        # ===== 视频网站智能爬取（优酷/B站/腾讯视频/爱奇艺等）=====
        if crawler.is_video_site(url):
            video_item, video_msg = smart_scrape_videos(crawler, url)
            state.scraped_videos.append(video_item)
            state.is_video_site = True

            # 也保留基础爬取结果（用于整体流程展示）
            r = crawler.fetch(url)
            state.crawl_results.append(r)

            continue

        # ===== 普通爬取流程 =====
        result = crawler.fetch(url)
        state.crawl_results.append(result)

        if result.success:
            # 如果用户请求下载图片（非壁纸站）
            if state.need_image_download:
                if result.html:
                    img_urls = extract_image_urls(result.html, base_url=url, limit=20)
                    if img_urls:
                        downloaded = download_images(
                            img_urls, state.image_output_dir, page_url=url,
                        )
                        state.downloaded_images.extend(downloaded)

            # 原有的壁纸文本提取逻辑（继续保留，用于展示元数据）
            if result.strategy == "browser" and result.text and len(result.text) > 50:
                lines = [line.strip() for line in result.text.split('\n') if line.strip()]
                wallpapers = _extract_wallpapers(lines)
                if wallpapers:
                    state.extracted_data.extend(wallpapers)
                else:
                    filtered = []
                    for line in lines:
                        if len(line) >= 2 and not line.startswith(('Copyright', '©', 'ICP', '备案', '隐私', '服务条款')):
                            filtered.append(line)
                    content_lines = filtered[:20]
                    if content_lines:
                        state.extracted_data.append({
                            "title": result.title or url,
                            "url": url,
                            "content": "\n".join(content_lines),
                        })
            else:
                articles = BaseCrawler.extract_article_list(result.html, limit=30)
                if articles:
                    state.extracted_data.extend(articles)
                elif result.text:
                    state.extracted_data.append({
                        "title": result.title or url,
                        "url": url,
                        "content_preview": result.text[:500],
                    })
        else:
            state.errors.append(f"{url} - {result.error}")

    crawler.close()
    return state


def _extract_wallpapers(lines: list[str]) -> list[dict]:
    """从页面文本中提取壁纸条目"""
    wallpapers = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # 跳过导航和已知非内容行
        if line in ('哲风壁纸', '壁纸社区', '电脑壁纸', '手机壁纸', '头像制作', '首页', '登录', '注册', '前往', 'N'):
            i += 1
            continue

        # 跳过纯数字（收藏数/下载数）
        if line.isdigit():
            i += 1
            continue

        # 跳过分辨率格式（如 3840x2160）
        if 'x' in line and len(line) <= 20 and line.replace('x', '').replace('.', '').isdigit():
            i += 1
            continue

        # 跳过大小格式（如 28.85 MB）
        if ('MB' in line or 'KB' in line) and len(line) <= 20:
            i += 1
            continue

        # 如果这行看起来像壁纸名称（长度适中）
        if 4 <= len(line) <= 50:
            # 收集条目信息
            wallpaper = {"name": line, "tags": [], "resolution": "", "size": "", "downloads": ""}

            # 收集后面的标签和元数据
            j = i + 1
            while j < len(lines) and j < i + 10:
                next_line = lines[j]

                # 跳过"前往"
                if next_line == '前往':
                    j += 1
                    continue

                # 跳过纯数字（收藏数）
                if next_line.isdigit() and len(next_line) < 6:
                    j += 1
                    continue

                # 检测分辨率 (如 3840x2160)
                if 'x' in next_line and len(next_line) <= 20 and next_line.replace('x', '').replace('.', '').isdigit():
                    wallpaper["resolution"] = next_line
                    j += 1
                    continue

                # 检测大小 (如 28.85 MB)
                if 'MB' in next_line or 'KB' in next_line:
                    wallpaper["size"] = next_line
                    j += 1
                    continue

                # 检测下载数（纯数字且较长）
                if next_line.isdigit() and len(next_line) >= 5:
                    wallpaper["downloads"] = next_line
                    j += 1
                    continue

                # 其他非数字行作为标签（长度适中）
                if 2 <= len(next_line) <= 30 and not next_line.isdigit():
                    wallpaper["tags"].append(next_line)
                    j += 1
                    continue

                break

            # 只有当有足够信息时才添加
            if wallpaper["tags"] or wallpaper["resolution"] or wallpaper["size"]:
                title = wallpaper["name"]
                if wallpaper["tags"]:
                    title += f" ({', '.join(wallpaper['tags'][:3])})"
                if wallpaper["resolution"]:
                    title += f" [{wallpaper['resolution']}]"

                wallpapers.append({
                    "title": title,
                    "name": wallpaper["name"],
                    "tags": wallpaper["tags"],
                    "resolution": wallpaper["resolution"],
                    "size": wallpaper["size"],
                    "downloads": wallpaper["downloads"],
                })

        i += 1

    return wallpapers


# ---- 节点 4: LLM 智能解析（可选） ----

def node_llm_parse(state: CrawState, llm_factory: LLMFactory) -> CrawState:
    """用 LLM 解析爬取结果，生成摘要或中文翻译"""
    # chat 模式：直接让 LLM 回答
    if state.intent == "chat":
        try:
            reply = llm_factory.chat(
                state.user_input,
                system_msg="你是 CrawAgent，一个专注于网页爬取与数据整理的助手。"
                           "回答简洁明了。如果用户问爬虫相关问题，给出实用建议。",
            )
            state.llm_summary = reply
        except Exception as e:
            state.errors.append(f"LLM 调用失败: {e}")
        return state

    # crawl + need_llm_parse: 用 LLM 做摘要
    if state.need_llm_parse and state.extracted_data:
        # 构造发给 LLM 的数据（限制长度避免超限）
        data_preview = state.extracted_data[:15]
        data_text = "\n".join(
            f"{i+1}. {item.get('title', '(无标题)')}  ({item.get('url', '')})"
            for i, item in enumerate(data_preview)
        )

        prompt = (
            f"以下是爬取到的页面内容（共 {len(state.extracted_data)} 条）：\n"
            f"{data_text}\n\n"
            "请完成：\n"
            "1. 每条给一句话中文摘要\n"
            "2. 最后给出整体判断：这些内容是什么类型的网站/主题？\n"
            "回答请简洁明了。"
        )

        try:
            state.llm_summary = llm_factory.chat(
                prompt,
                system_msg="你是 CrawAgent 的中文内容编辑，擅长翻译和总结英文或中文网页内容。",
            )
        except Exception as e:
            state.errors.append(f"LLM 解析失败: {e}")

    return state


# ---- 节点 5: 输出汇总 ----

def node_summarize(state: CrawState) -> CrawState:
    """汇总工作流结果，生成最终给用户的答复"""
    lines: list[str] = []

    # 标题
    if state.intent == "chat":
        title = "💬 对话模式"
    elif state.intent == "crawl":
        title = "🕷️ 爬取结果"
    elif state.intent == "analyze":
        title = "📊 分析结果"
    else:
        title = "📋 结果"
    lines.append(title)
    lines.append("=" * 40)

    # 爬取结果
    if state.crawl_results:
        for r in state.crawl_results:
            if r.success:
                strategy_label = ""
                if r.strategy == "browser":
                    strategy_label = " [浏览器渲染]"
                elif r.strategy == "basic_fallback":
                    strategy_label = " [浏览器失败-降级]"
                elif r.strategy == "browser_unavailable":
                    strategy_label = " [浏览器未安装]"
                lines.append(f"✅ {r.url}  (HTTP {r.status_code}){strategy_label}")
                if r.title:
                    lines.append(f"   标题: {r.title}")
            else:
                lines.append(f"❌ {r.url}  - {r.error}")
        lines.append("")

    # 网络抓包爬虫结果（最高优先级展示）
    if state.use_packet_crawler and (state.packet_videos or state.packet_status_msg):
        total_extracted = len(state.packet_videos)
        total_downloaded = len(state.packet_downloaded)
        lines.append(f"🎬 网络抓包爬虫: 提取到 {total_extracted} 个视频，已下载 {total_downloaded} 个")
        lines.append(f"   保存位置: {state.video_output_dir}/")
        lines.append("-" * 40)

        # 展示前 10 个视频信息
        for i, video in enumerate(state.packet_videos[:10], 1):
            try:
                title = getattr(video, "title", "") or f"视频_{i}"
                platform = getattr(video, "platform", "") or ""
                video_url = getattr(video, "video_url", "")
                display_title = title[:80] if isinstance(title, str) else str(title)[:80]
                platform_label = f"[{platform}] " if platform else ""
                lines.append(f"   {i:>2}. {platform_label}{display_title}")
                if video_url:
                    lines.append(f"       🔗 {video_url[:100]}")
            except Exception:
                continue
        if total_extracted > 10:
            lines.append(f"       ... 还有 {total_extracted - 10} 个视频")
        lines.append("")

        # 已下载的文件
        if state.packet_downloaded:
            lines.append(f"   ✅ 已下载的视频文件:")
            for fp in state.packet_downloaded[:10]:
                import os
                fname = os.path.basename(fp) if fp else ""
                lines.append(f"      - {fname[:80]}")
            if len(state.packet_downloaded) > 10:
                lines.append(f"      ... 还有 {len(state.packet_downloaded) - 10} 个")
            lines.append("")

    # 抖音搜索页面视频下载结果（最高优先级展示）
    if state.is_douyin_search and state.douyin_download_results:
        success_count = sum(1 for _, success, _ in state.douyin_download_results if success)
        fail_count = len(state.douyin_download_results) - success_count
        lines.append(f"🎬 抖音视频爬取: 成功下载 {success_count} 个，失败 {fail_count} 个")
        lines.append(f"   保存位置: {state.video_output_dir}/")
        lines.append("-" * 40)

        for filename, success, msg in state.douyin_download_results[:15]:
            status = "✅" if success else "❌"
            lines.append(f"   {status} {filename}  ({msg})")

        if len(state.douyin_download_results) > 15:
            lines.append(f"   ... 还有 {len(state.douyin_download_results) - 15} 个")
        lines.append("")

    # 抖音视频详情页下载结果（最高优先级展示）
    if state.is_douyin_video and state.douyin_video_results:
        success_count = sum(1 for _, fp in state.douyin_video_results if fp)
        fail_count = len(state.douyin_video_results) - success_count
        lines.append(f"🎬 抖音视频详情页下载: 成功 {success_count} 个，失败 {fail_count} 个")
        lines.append(f"   保存位置: {state.video_output_dir}/")
        lines.append("-" * 40)

        for url, filepath in state.douyin_video_results[:15]:
            if filepath:
                filename = filepath.replace("\\", "/").split("/")[-1]
                lines.append(f"   ✅ {filename}")
            else:
                short_url = url[:60] + "..." if len(url) > 60 else url
                lines.append(f"   ❌ {short_url} (获取失败)")

        if len(state.douyin_video_results) > 15:
            lines.append(f"   ... 还有 {len(state.douyin_video_results) - 15} 个")
        lines.append("")

    # 视频网站元数据爬取结果（最高优先级展示）
    if state.scraped_videos:
        lines.append(f"🎬 视频网站元数据爬取: 成功解析 {len(state.scraped_videos)} 个视频页面")
        lines.append("-" * 40)

        for idx, video in enumerate(state.scraped_videos, 1):
            site_cn = {
                "youku": "优酷", "bilibili": "B站", "qq": "腾讯视频",
                "iqiyi": "爱奇艺", "youtube": "YouTube", "douyin": "抖音",
                "mgtv": "芒果TV", "sohu": "搜狐视频", "other": "其他",
            }.get(video.site, video.site)

            lines.append(f"   {idx:>2}. [{site_cn}] {video.title or '(未提取到标题)'}")
            if video.description and len(video.description) > 5:
                desc = video.description[:120]
                if len(video.description) > 120:
                    desc += "..."
                lines.append(f"       📄 简介: {desc}")
            if video.directors:
                lines.append(f"       🎥 导演: {', '.join(video.directors[:3])}")
            if video.actors:
                actors_str = ", ".join(video.actors[:6])
                if len(video.actors) > 6:
                    actors_str += f" 等{len(video.actors)}人"
                lines.append(f"       🎭 演员: {actors_str}")
            if video.tags:
                lines.append(f"       🏷️  标签: {', '.join(video.tags[:8])}")
            if video.episodes:
                ep_show = ", ".join(video.episodes[:10])
                if video.episode_count > 10:
                    ep_show += f" ... (共{video.episode_count}集)"
                lines.append(f"       📺 剧集: {ep_show}")
            if video.play_count:
                lines.append(f"       📈 播放: {video.play_count}")
            if video.rating:
                lines.append(f"       ⭐ 评分: {video.rating}")
            if video.stream_urls:
                lines.append(f"       🔗 检测到 {len(video.stream_urls)} 个视频流地址 (m3u8/mp4):")
                for su in video.stream_urls[:3]:
                    short = su[:80] + ("..." if len(su) > 80 else "")
                    lines.append(f"           → {short}")
            else:
                lines.append(f"       ℹ️  说明: 该视频流为加密协议/需登录, 无法直接获取直链。视频元数据已提取。")
            if video.poster_urls:
                lines.append(f"       🖼️  封面图: {len(video.poster_urls)} 张 (首址: {video.poster_urls[0][:60]}...)")
            lines.append("")

    # 智能壁纸爬虫结果（优先展示）
    elif state.downloaded_wallpapers:
        lines.append(f"🖼️  智能壁纸爬虫: 成功下载 {len(state.downloaded_wallpapers)} 个壁纸文件")
        lines.append(f"   保存位置: {state.image_output_dir}/")
        lines.append("-" * 40)

        # 列出前 10 个下载的壁纸（按文件名展示）
        preview = state.downloaded_wallpapers[:10]
        for i, wp in enumerate(preview, 1):
            ext_info = wp.filename.split(".")[-1].upper() if wp.filename else "MP4"
            res_info = wp.resolution if wp.resolution else ""
            lines.append(f"   {i:>2}. [{ext_info}] {wp.filename}  ({wp.size})")

        if len(state.downloaded_wallpapers) > 10:
            lines.append(f"   ... 还有 {len(state.downloaded_wallpapers) - 10} 个壁纸已保存")
        lines.append("")

    # 图片下载结果（普通图片下载模式）
    elif state.downloaded_images:
        ok_images = [img for img in state.downloaded_images if img.get("path")]
        fail_images = [img for img in state.downloaded_images if not img.get("path")]

        lines.append(f"🖼️ 图片下载: 成功 {len(ok_images)} 张，失败 {len(fail_images)} 张")
        lines.append(f"   保存位置: {state.image_output_dir}/")
        lines.append("-" * 40)

        # 显示前 8 张成功的图片
        preview_ok = ok_images[:8]
        for i, img in enumerate(preview_ok, 1):
            filename = img["path"].replace("\\", "/").split("/")[-1]
            lines.append(f"   {i:>2}. {filename}  ({img.get('size', '?')})")
        if len(ok_images) > 8:
            lines.append(f"   ... 还有 {len(ok_images) - 8} 张已保存")

        # 显示前 3 个失败
        if fail_images:
            lines.append("")
            lines.append("   失败的图片:")
            for img in fail_images[:3]:
                url_short = img["url"][:60] + ("..." if len(img["url"]) > 60 else "")
                lines.append(f"     ✗ {url_short}  ({img.get('error', 'unknown')})")

        lines.append("")

    # 提取到的结构化数据
    if state.extracted_data:
        lines.append(f"📄 提取到 {len(state.extracted_data)} 条内容:")
        lines.append("-" * 40)
        preview = state.extracted_data[:10]
        for i, item in enumerate(preview, 1):
            title = item.get("title") or item.get("text") or "(无标题)"
            url = item.get("url", "")
            content = item.get("content", "")
            content_preview = item.get("content_preview", "")

            # 壁纸特殊格式显示
            if item.get("name") and item.get("resolution"):
                name = item["name"]
                tags = item.get("tags", [])
                res = item.get("resolution", "")
                size = item.get("size", "")
                downloads = item.get("downloads", "")

                line = f"  {i:>2}. 🖼️ {name}"
                if tags:
                    line += f" ({', '.join(tags[:3])})"
                lines.append(line)

                meta_parts = []
                if res:
                    meta_parts.append(f"分辨率: {res}")
                if size:
                    meta_parts.append(f"大小: {size}")
                if downloads:
                    meta_parts.append(f"下载: {downloads}")
                if meta_parts:
                    lines.append(f"      {' | '.join(meta_parts)}")

            else:
                line = f"  {i:>2}. {title[:60]}"
                lines.append(line)

                if content:
                    content_lines = content.split('\n')[:5]
                    for cl in content_lines:
                        lines.append(f"      {cl[:70]}")
                elif content_preview:
                    lines.append(f"      {content_preview[:70]}")

            if url and url != title:
                lines.append(f"      🔗 {url}")

            lines.append("")
        if len(state.extracted_data) > 10:
            lines.append(f"  ... 还有 {len(state.extracted_data) - 10} 条")
        lines.append("")

    # LLM 摘要
    if state.llm_summary:
        lines.append("🤖 LLM 摘要:")
        lines.append("-" * 40)
        lines.append(state.llm_summary)
        lines.append("")

    # 错误信息
    if state.errors:
        lines.append("⚠️ 注意事项:")
        for err in state.errors:
            lines.append(f"  - {err}")
        lines.append("")

    # 保存数据提示
    if state.extracted_data:
        lines.append("💡 提示: 用 /export 命令可导出为 JSON/CSV 或 Markdown")

    state.final_reply = "\n".join(lines)
    return state


# ============================================================
# Workflow - 工作流编排
# ============================================================

class CrawWorkflow:
    """LangGraph 简化版工作流（不使用图的高级功能，顺序执行）

    Phase 1 只需要顺序执行的简单工作流。
    后续扩展时可接入 langgraph.StateGraph 以支持条件分支、循环等。
    """

    def __init__(self, settings: Settings, llm_factory: LLMFactory):
        self.settings = settings
        self.llm_factory = llm_factory

    def run(self, user_input: str, *, debug_mode: bool = False, force_browser: bool | None = None,
            max_wallpapers: int = 12, image_output_dir: str = "output/img",
            video_output_dir: str = "output/video") -> CrawState:
        """执行完整工作流

        Args:
            user_input: 用户输入
            debug_mode: 是否开启有头调试（显示浏览器窗口）
            force_browser: None=自动检测, True=强制使用浏览器, False=只用httpx
            max_wallpapers: 壁纸爬虫最多下载数量
            image_output_dir: 图片输出目录
            video_output_dir: 视频元数据输出目录
        """
        state = CrawState(
            user_input=user_input, debug_mode=debug_mode, force_browser=force_browser,
            image_output_dir=image_output_dir, max_wallpapers=max_wallpapers,
            video_output_dir=video_output_dir,
        )

        # 1. 解析意图
        state = node_parse_intent(state)

        # 2. 选择策略
        state = node_choose_strategy(state)

        # 3. 执行爬取（chat 模式跳过）
        if state.intent != "chat" or state.target_urls:
            state = node_crawl(state)

        # 4. LLM 解析（chat 模式、或用户要求 summary 时触发）
        if state.intent == "chat" or state.need_llm_parse:
            try:
                state = node_llm_parse(state, self.llm_factory)
            except Exception as e:
                state.errors.append(f"LLM 不可用: {e}")

        # 5. 汇总输出
        state = node_summarize(state)

        return state
