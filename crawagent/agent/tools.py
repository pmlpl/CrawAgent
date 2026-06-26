"""Agent 工具定义 - 所有 @tool 装饰的函数

包含爬虫、数据库、视频播放等 Agent 可调用的工具。
"""
from __future__ import annotations

from langchain_core.tools import tool

from ..config.settings import get_logger

logger = get_logger(__name__)

# 网络抓包爬虫（可选依赖）
try:
    from ..tools.packet_crawler import (
        scrape_and_download as packet_scrape_and_download,
        is_available as packet_is_available,
    )
    _PACKET_AVAILABLE = True
except ImportError:
    _PACKET_AVAILABLE = False
    packet_scrape_and_download = None


def _get_tool_state(state: dict) -> dict:
    """从 AgentState 中提取工具执行需要的上下文"""
    return {
        "debug_mode": state.get("debug_mode", False),
        "force_browser": state.get("force_browser"),
        "image_output_dir": state.get("image_output_dir", "output/img"),
        "video_output_dir": state.get("video_output_dir", "output/video"),
        "max_wallpapers": state.get("max_wallpapers", 12),
    }


@tool
def basic_crawler(url: str, extra_headers: dict | None = None) -> str:
    """基础 HTTP 爬虫，适合静态页面（博客、新闻、普通网站）。

    适用场景：不需要 JS 渲染的普通网页。
    优点：快速、稳定、消耗资源少。
    缺点：无法处理 JS 动态内容，可能被反爬拦截。
    """
    from ..tools import BaseCrawler
    crawler = BaseCrawler()
    result = crawler.fetch(url, extra_headers=extra_headers)
    if result.success:
        articles = BaseCrawler.extract_article_list(result.html, limit=30)
        return f"✅ 成功爬取 {url}，HTTP {result.status_code}，标题：{result.title or '无'}，找到 {len(articles)} 个链接"
    return f"❌ 爬取失败：{result.error}"


@tool
def browser_crawler(url: str, wait_seconds: int = 5, headless: bool = True) -> str:
    """浏览器自动化爬虫，使用 Playwright 渲染 JS，适合 SPA/反爬页面。

    适用场景：JS 渲染页面、SPA 应用、有反爬检测的站点。
    优点：能处理动态内容，更接近真实用户。
    缺点：速度较慢，需要安装 Chromium 浏览器。
    """
    from ..tools import Crawler
    crawler = Crawler(headless=headless)
    result = crawler.fetch(url)
    crawler.close()
    if result.success:
        return f"✅ 浏览器爬取成功，策略：{result.strategy}，标题：{result.title or '无'}"
    return f"❌ 浏览器爬取失败：{result.error}"


@tool
def packet_crawler(url: str, wait_seconds: int = 15, max_videos: int = 20) -> str:
    """网络抓包爬虫，捕获 XHR/Fetch 请求，提取 API 原始数据。

    适用场景：视频网站（优酷/B站/抖音/爱奇艺等）、需要登录的动态内容。
    优点：能获取 API 返回的原始数据，绕过前端加密。
    缺点：速度较慢，需要浏览器，可能需要登录。
    """
    if not _PACKET_AVAILABLE:
        return "❌ 抓包爬虫模块（DrissionPage）未安装"
    from ..tools.packet_crawler import scrape_and_download
    videos, downloaded, status_msg = scrape_and_download(
        url, output_dir="output/video", wait_seconds=wait_seconds, max_videos=max_videos, headless=True
    )
    return status_msg


@tool
def image_downloader(url: str, limit: int = 20) -> str:
    """从页面提取并下载图片，适合图库、壁纸等场景。

    适用场景：需要批量下载图片的页面。
    优点：自动去重，支持多种图片来源（<img>/CSS background/链接）。
    缺点：可能下载到低质量缩略图。
    """
    from ..tools import Crawler, extract_image_urls, download_images
    crawler = Crawler(headless=True)
    result = crawler.fetch(url)
    crawler.close()
    if not result.success or not result.html:
        return f"❌ 无法获取页面：{result.error or '未知错误'}"
    img_urls = extract_image_urls(result.html, base_url=url, limit=limit)
    if not img_urls:
        return "⚠️ 页面中未找到图片"
    downloaded = download_images(img_urls, "output/img", page_url=url)
    success = sum(1 for img in downloaded if img.get("path"))
    return f"找到 {len(img_urls)} 张图片，成功下载 {success} 张"


@tool
def wallpaper_scraper(url: str, max_count: int = 12) -> str:
    """智能壁纸爬虫，适合 haowallpaper 等壁纸站点。自动进入详情页提取主图/视频壁纸。

    适用场景：壁纸网站批量下载。
    优点：自动提取高质量壁纸，支持视频壁纸。
    缺点：仅适用于特定壁纸站点。
    """
    from ..tools import Crawler
    from ..tools.base_crawler import smart_scrape_wallpapers
    crawler = Crawler(headless=True)
    wallpapers, status_msg = smart_scrape_wallpapers(crawler, url, "output/img", max_wallpapers=max_count)
    crawler.close()
    return status_msg


@tool
def video_metadata_scraper(url: str) -> str:
    """视频元数据爬虫，提取标题/简介/演员/标签/流地址等信息。

    适用场景：优酷/B站/爱奇艺/腾讯视频等视频站点。
    优点：提取丰富元数据，自动检测流地址。
    缺点：加密 m3u8 可能无法直接下载。
    """
    from ..tools import Crawler
    from ..tools.base_crawler import smart_scrape_videos
    crawler = Crawler(headless=True)
    video, status_msg = smart_scrape_videos(crawler, url)
    crawler.close()
    return status_msg


@tool
def chat(message: str) -> str:
    """直接与用户对话，回答通用知识问题或闲聊。

    适用场景：
    - 用户询问常识性问题、天气、历史、文学等纯知识问题
    - 用户主动打招呼或闲聊
    - 用户输入完全不涉及任何工具可以处理的需求
    
    注意：如果用户说"看XXX"/"播放XXX"/"我想看XXX"等观看视频的请求，不要用chat，要用watch_video工具！
    """
    return f"[对话模式] 收到消息：{message}"


@tool
def database_save(
    url: str,
    title: str = "",
    content: str = "",
    source: str = "",
    author: str = "",
    published_date: str = "",
    category: str = "",
    **kwargs
) -> str:
    """保存爬取的数据到 SQLite 数据库。

    适用场景：用户要求"保存到数据库"、"入库"等。
    
    参数：
    - url: 网页链接（必填，作为唯一标识）
    - title: 标题
    - content: 内容
    - source: 来源网站（如 zhihu、weibo，从 URL 自动提取）
    - author: 作者
    - published_date: 发布时间
    - category: 分类
    
    返回：保存结果信息
    """
    from ..tools.database import save_to_db
    
    try:
        record_id = save_to_db(
            url=url,
            title=title,
            content=content,
            source=source,
            author=author,
            published_date=published_date,
            category=category,
            **kwargs
        )
        return f"✅ 已保存到数据库，记录ID: {record_id}"
    except Exception as e:
        return f"❌ 保存失败: {e}"


@tool
def database_query(
    keyword: str = "",
    source: str = "",
    limit: int = 10
) -> str:
    """查询数据库中的爬取记录。

    适用场景：用户查询已保存的数据。
    
    参数：
    - keyword: 搜索关键词（匹配标题和内容）
    - source: 来源网站筛选
    - limit: 返回数量限制（默认10条）
    
    返回：查询结果
    """
    from ..tools.database import get_default_db
    
    try:
        db = get_default_db()
        records = db.query(keyword=keyword, source=source, limit=limit)
        
        if not records:
            return "未找到匹配的记录"
        
        lines = [f"找到 {len(records)} 条记录："]
        for r in records[:limit]:
            lines.append(f"  - [{r.source}] {r.title or r.url}")
            if r.author:
                lines.append(f"    作者: {r.author}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"查询失败: {e}"


@tool
def database_export(format: str = "json", source: str = "") -> str:
    """导出数据库记录到文件。

    适用场景：用户要求导出数据。
    
    参数：
    - format: 导出格式（json 或 csv）
    - source: 来源筛选（可选）
    
    返回：导出文件路径
    """
    from ..tools.database import get_default_db
    from pathlib import Path
    import time
    
    try:
        db = get_default_db()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        
        if source:
            filename = f"crawl_{source}_{timestamp}"
        else:
            filename = f"crawl_export_{timestamp}"
        
        if format.lower() == "csv":
            path = db.export_to_csv(f"output/{filename}.csv")
        else:
            path = db.export_to_json(f"output/{filename}.json")
        
        return f"✅ 已导出到: {path}"
    except Exception as e:
        return f"导出失败: {e}"


@tool
def watch_video(name: str) -> str:
    """播放视频 - 自动启动服务器并打开浏览器播放电影、电视剧、综艺节目。

    适用场景：用户说"我想看XXX"、"播放XXX"、"打开XXX"等观看视频的请求。
    功能：自动启动视频播放器服务器 + 搜索视频 + 打开浏览器播放，支持电影、电视剧、综艺、动漫等。
    
    参数：
    - name: 视频名称（如 "奔跑吧兄弟"、"狂飙"、"变形金刚"）
    
    返回：播放结果说明
    """
    from ..tools.video_agent import watch_video as open_player
    
    try:
        result = open_player(name)
        return result
    except Exception as e:
        return f"❌ 播放失败: {e}\n\n请手动运行: python video_player_app.py"


@tool
def list_extract(
    html: str = "",
    url: str = "",
    save_to_db: bool = False,
    source: str = "",
    category: str = "",
    max_items: int = 50,
    use_llm: str = "auto",
    keyword_filter: str = "",
) -> str:
    """从 HTML 或 URL 中提取结构化列表数据（热榜、排行榜、新闻列表等）。

    适用场景：爬取列表页/热榜/排行榜，提取每条记录的标题、链接、热度、排名等字段。
    混合策略：先用正则快速提取，效果不好时自动用 LLM 增强。
    
    参数：
    - html: 页面 HTML 内容（如果已经有爬取结果，直接传 HTML）
    - url: 网页 URL（如果传了 URL 但没有 html，会自动爬取）
    - save_to_db: 是否保存到数据库（默认 False）
    - source: 来源标识（如 zhihu、weibo，保存到数据库时用）
    - category: 分类（保存到数据库时用）
    - max_items: 最大提取数量（默认 50）
    - use_llm: LLM 使用策略："auto"（自动，效果不好才用）/ "always"（总是用）/ "never"（从不）
    - keyword_filter: 关键词过滤，多个用英文逗号分隔，只保留包含这些关键词的内容（如 "军事,战争,军队"）
    
    返回：格式化的列表结果 + 保存状态
    """
    from ..tools.list_extractor import (
        extract_list,
        save_list_to_db,
        format_list_output,
        _assess_quality,
        ListExtractor,
        _is_valid_list_item,
        find_category_links,
        extract_from_dom,
    )
    from urllib.parse import urlparse
    import json as _json

    # 解析 keyword_filter：支持 JSON 数组字符串 或 逗号分隔
    kw_list: list[str] | None = None
    if keyword_filter:
        keyword_filter = keyword_filter.strip()
        # 尝试解析 JSON
        if keyword_filter.startswith('[') and keyword_filter.endswith(']'):
            try:
                parsed = _json.loads(keyword_filter)
                if isinstance(parsed, list):
                    kw_list = [str(k) for k in parsed if k]
            except:
                pass
        # 逗号分隔
        if not kw_list:
            kw_list = [k.strip() for k in keyword_filter.split(',') if k.strip()]

    def _quick_check_quality(html: str, base_url: str = "") -> tuple:
        """快速检查 HTML 提取质量（用于判断是否需要重爬）"""
        extractor = ListExtractor(base_url=base_url)
        items = extractor.extract_from_html(html, max_items=30)
        valid = [item for item in items if _is_valid_list_item(item, base_url)]
        quality = _assess_quality(valid)
        return valid, quality

    result_html = html

    # 如果传了 URL 但没有 HTML，先爬取（直接用底层爬虫获取完整 HTML）
    if url and not result_html:
        try:
            from ..tools import Crawler
            crawler = Crawler(headless=True)
            result = crawler.fetch(url)
            crawler.close()
            if result.success:
                result_html = result.html or result.text or ""
            else:
                return f"爬取失败: {result.error}"
            
            # 如果第一次提取效果不好，重试一次强制浏览器模式
            temp_items, _ = _quick_check_quality(result_html, base_url=url)
            if len(temp_items) < 3:
                try:
                    crawler2 = Crawler(headless=True)
                    crawler2.set_browser_mode(True)  # 强制浏览器模式
                    result2 = crawler2.fetch(url)
                    crawler2.close()
                    if result2.success and len(result2.html or "") > len(result_html):
                        result_html = result2.html or result2.text or ""
                except:
                    pass
        except Exception as e:
            return f"爬取失败: {e}"

    if not result_html:
        return "错误：需要提供 html 或 url 参数"

    # 确定 base_url
    base_url = url or ""

    # 获取 LLM 客户端（仅在需要时）
    llm_client = None
    if use_llm in ("auto", "always"):
        try:
            from ..llm.factory import LLMFactory
            from ..config.settings import load_settings
            settings = load_settings()
            llm_factory = LLMFactory(settings)
            llm_client = llm_factory.get_default()
        except Exception as e:
            logger.warning(f"[list_extract] 获取 LLM 失败，将只用正则: {e}")
            llm_client = None

    # 提取列表（混合策略）
    items, method = extract_list(
        result_html,
        base_url=base_url,
        max_items=max_items,
        use_llm=use_llm,
        llm_client=llm_client,
        keyword_filter=kw_list,
    )

    # DOM 深度提取兜底：如果正则+LLM效果都不好，尝试直接从 DOM 提取
    dom_info = ""
    if url:
        quality_score = _assess_quality(items)
        has_rank_count = sum(1 for item in items if item.rank and item.rank.isdigit())
        # 触发 DOM 提取的条件（满足任意一条）：
        # 1. 数量太少（< 5条）
        # 2. 质量太低（< 0.5）且没有排名的项（说明可能提取到的都是导航）
        should_try_dom = (
            len(items) < 5
            or (quality_score < 0.55 and has_rank_count == 0 and len(items) >= 5)
        )
        if should_try_dom:
            logger.debug(f"[list_extract] 提取效果不佳(数量:{len(items)} 质量:{quality_score:.2f} 有排名:{has_rank_count})，尝试 DOM 深度提取")
            try:
                dom_items, dom_method = extract_from_dom(
                    url,
                    max_items=max_items,
                    keyword_filter=kw_list,
                )
                if dom_items:
                    dom_quality = _assess_quality(dom_items)
                    dom_has_rank = sum(1 for item in dom_items if item.rank and item.rank.isdigit())
                    logger.debug(f"[list_extract] DOM 提取: {len(dom_items)} 条, 质量: {dom_quality:.2f}, 有排名: {dom_has_rank}")
                    # 使用 DOM 结果的条件：
                    # 1. 数量更多
                    # 2. 有排名的项更多（说明是真正的榜单）
                    # 3. 质量更高
                    should_use_dom = (
                        len(dom_items) > len(items)
                        or dom_has_rank > has_rank_count
                        or dom_quality > quality_score
                    )
                    if should_use_dom:
                        items = dom_items
                        method = dom_method
                        dom_info = "\n✨ 使用 DOM 深度提取模式"
                        logger.debug(f"[list_extract] 使用 DOM 提取结果")
            except Exception as e:
                logger.debug(f"[list_extract] DOM 提取失败: {e}")

    # 自动频道跳转：如果有过滤关键词但结果很少，尝试查找相关频道链接并跳转
    jump_info = ""
    if kw_list and len(items) < 3 and url:
        cat_links = find_category_links(result_html, kw_list, base_url=base_url)
        if cat_links:
            jump_url = cat_links[0]
            jump_info = f"\n🔗 自动跳转至频道: {jump_url}"
            logger.debug(f"[list_extract] 结果过少，自动跳转频道: {jump_url}")
            try:
                from ..tools import Crawler
                crawler_jump = Crawler(headless=True)
                jump_result = crawler_jump.fetch(jump_url)
                crawler_jump.close()
                if jump_result.success:
                    jump_html = jump_result.html or jump_result.text or ""
                    # 如果第一次提取效果不好，重试一次强制浏览器模式
                    temp_jump_items, _ = _quick_check_quality(jump_html, base_url=jump_url)
                    if len(temp_jump_items) < 5:
                        try:
                            crawler_jump2 = Crawler(headless=True)
                            crawler_jump2._browser_mode = True
                            jump_result2 = crawler_jump2.fetch(jump_url)
                            crawler_jump2.close()
                            if jump_result2.success and len(jump_result2.html or "") > len(jump_html):
                                jump_html = jump_result2.html or jump_result2.text or ""
                        except:
                            pass
                    jump_items, jump_method = extract_list(
                        jump_html,
                        base_url=jump_url,
                        max_items=max_items,
                        use_llm=use_llm,
                        llm_client=llm_client,
                        keyword_filter=None,
                    )
                    # 频道跳转后也尝试 DOM 提取兜底
                    if len(jump_items) < 5:
                        try:
                            dom_jump_items, dom_jump_method = extract_from_dom(
                                jump_url,
                                max_items=max_items,
                                keyword_filter=None,
                            )
                            if dom_jump_items and len(dom_jump_items) > len(jump_items):
                                jump_items = dom_jump_items
                                jump_method = dom_jump_method
                                logger.debug(f"[list_extract] 频道跳转后 DOM 提取成功: {len(jump_items)} 条")
                        except Exception as e:
                            logger.debug(f"[list_extract] 频道跳转后 DOM 提取失败: {e}")
                    if len(jump_items) > len(items):
                        items = jump_items
                        method = jump_method
                        base_url = jump_url
            except Exception as e:
                logger.debug(f"[list_extract] 频道跳转失败: {e}")

    # 自动推断 source
    if not source and url:
        try:
            domain = urlparse(url).netloc.replace("www.", "")
            parts = domain.split(".")
            if len(parts) >= 2:
                source = parts[-2]
        except:
            source = ""

    # 保存到数据库
    save_result = ""
    if save_to_db and items:
        try:
            count = save_list_to_db(items, source=source, category=category)
            save_result = f"\n\n✅ 已保存 {count} 条到数据库 (source: {source or 'auto'})"
        except Exception as e:
            save_result = f"\n\n❌ 保存数据库失败: {e}"

    # 格式化输出
    method_map = {
        "regex": "正则提取",
        "llm": "LLM 提取",
        "dom": "DOM 深度提取",
    }
    method_display = method_map.get(method, method)
    filter_info = ""
    if kw_list:
        filter_info = f"\n过滤关键词: {', '.join(kw_list)}"
    output = format_list_output(items, show_url=True) + f"\n\n提取方式: {method_display}{filter_info}{jump_info}{dom_info}"
    if save_result:
        output += save_result

    return output


# 工具列表（用于 bind_tools）
TOOLS = [
    basic_crawler,
    browser_crawler,
    packet_crawler,
    image_downloader,
    wallpaper_scraper,
    video_metadata_scraper,
    database_save,
    database_query,
    database_export,
    list_extract,
    watch_video,
    chat,
]

TOOL_NAMES = {t.name for t in TOOLS}

TOOL_SYSTEM_PROMPT = """你是一个专业的爬虫 Agent。你有多个工具可用，根据用户输入选择最合适的工具。

工具列表：
- basic_crawler: 基础 HTTP 爬虫，适合静态页面（博客、新闻，普通网站）
- browser_crawler: 浏览器自动化爬虫，适合 JS 渲染/SPA/反爬页面
- packet_crawler: 网络抓包爬虫，适合视频网站（优酷/B站/抖音等）
- image_downloader: 从页面提取并下载图片
- wallpaper_scraper: 智能壁纸爬虫，适合壁纸网站
- video_metadata_scraper: 视频元数据提取
- list_extract: ⭐ 列表页专用工具（热榜、排行榜、新闻列表、热搜），自动爬取+提取结构化数据+保存到数据库，一站式完成。内置三级提取策略：正则→LLM→DOM深度提取，自动适配各种页面
- database_save: 保存爬取数据到 SQLite 数据库（URL唯一，自动更新）
- database_query: 查询数据库中的记录
- database_export: 导出数据库记录到 JSON/CSV 文件
- watch_video: ⭐ 播放视频 - 用户说"看XXX"/"播放XXX"/"我想看XXX"时，自动打开浏览器播放电影、电视剧、综艺、动漫
- chat: 对话模式（无 URL 时使用）

选择规则（非常重要，按优先级）：
1. 【最高优先级 - 看视频】用户说"看XXX"、"播放XXX"、"我想看XXX"、"打开XXX电视剧"等观看视频的请求 → 直接用 watch_video！
   - 参数 name 填视频名称
   - watch_video 会自动搜索视频并打开浏览器播放
2. 【最高优先级】用户要求"所有信息"/"列表"/"热榜"/"热搜"/"排行榜"/"榜单"/"新闻"/"内容"/"数据" + 有 URL → 直接用 list_extract！
   - 要保存到数据库就加 save_to_db=True
   - 如果指定了分类（如军事、科技、财经），加 keyword_filter=["相关关键词"]
   - list_extract 内部会自动选择提取方式（正则/LLM/DOM），不用你管
3. 视频网站（抖音/B站/优酷等）"仅抓取页面文本/标题/UP主" → basic_crawler
4. 视频网站"下载视频/抓取流地址" → packet_crawler
5. 壁纸网站 → wallpaper_scraper
6. 有"下载图片"意图 → image_downloader
7. 有"保存到数据库"意图 → 如果是列表用 list_extract(save_to_db=True)，否则用 database_save
8. 普通网页内容爬取 → 选 basic_crawler 或 browser_crawler
9. 无 URL 或纯咨询 → chat

注意：
- packet_crawler 当前有 bug 慎用
- list_extract 是列表页的一站式工具，优先使用它，不要选 basic_crawler/browser_crawler
- watch_video 是视频播放的一站式工具，用户说看视频时直接调用它！

重要：你必须调用工具，不要只是回答！"""
