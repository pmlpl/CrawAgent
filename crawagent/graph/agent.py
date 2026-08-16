"""Agent 调度核心 — LangChain create_agent

Architecture:
    User input → agent_node (LLM + tools, created by create_agent)
               → if tool_calls: loop (internal to create_agent)
               → if no tool_calls: END

Key design:
    - System Prompt is PURE STATIC (maximizes DeepSeek prompt caching)
    - create_agent handles the LLM + tool execution loop internally
    - checkpointer 可选：传入 SqliteSaver 等即可按 thread_id 持久化会话状态，
      进程重启后用同一 thread_id 调用即可恢复上下文
"""
from langchain.agents import create_agent
from langgraph.checkpoint.base import BaseCheckpointSaver
from crawagent.config.settings import get_settings
from crawagent.llm.model import get_llm
from crawagent.graph.middleware import TrimHistoryMiddleware
from crawagent.tools.crawl_tool import crawl_webpage
from crawagent.tools.extract_tool import extract_content
from crawagent.tools.list_extract_tool import extract_list
from crawagent.tools.save_tool import save_record
from crawagent.tools.query_tool import list_crawled_resources
from crawagent.tools.file_tool import save_to_file
from crawagent.tools.browse_tool import browse_and_crawl
from crawagent.tools.social_tool import extract_social_media, download_social_media
from crawagent.tools.wallpaper_tool import extract_wallpaper_list, wallpaper_detail
from crawagent.tools.download_images import download_images

# System prompt — PURE STATIC, never modified at runtime.
# This ensures DeepSeek prompt caching hits on every request (system prompt +
# tool definitions prefix is identical across all calls).
SYSTEM_PROMPT = """You are CrawAgent, an intelligent web scraping assistant.

Your capabilities:
1. crawl_webpage(url) — Fetch webpage HTML content (static pages, fast)
2. browse_and_crawl(url, task) — AI-powered browser for dynamic/JS-rendered pages (SPA, font encryption, lazy-load)
3. extract_content(html, focus) — Extract title, body text, and links from a DETAIL/ARTICLE page; optional focus keyword
4. extract_list(html, url) — Extract list items (title + url pairs) from a LIST/INDEX page. Uses hybrid strategy: regex → LLM → DOM depth. Filters nav/footer links automatically.
5. save_record(url, title, content, extra_data) — Save extracted data to local database
6. list_crawled_resources(platform, keyword, limit) — Query previously crawled resources from database. Use when user asks "what have I crawled before" or "list my crawled videos/novels"
7. save_to_file(filename, content, subdir) — Save content to a local file (md/txt/json etc.)
8. extract_social_media(url, fields) — Extract metadata, comments, and media URLs from Douyin or Bilibili links
9. download_social_media(url, include, subdir) — Download video/audio/cover/images/comments to local files; Douyin MP4 already includes audio, Bilibili is auto-merged with audio
10. extract_wallpaper_list(url, limit, exclude_dynamic) — For ANY wallpaper/image website. Extracts wallpaper entries (title, type STATIC/DYNAMIC, resolution, thumbnail, detail URL, image/video URLs) from list pages. Handles CSS background-image cards, auto-pagination, and detail-page navigation. Use this instead of extract_list for wallpaper/image sites.
11. wallpaper_detail(url) — For ANY wallpaper/image website. Fetch full detail for one wallpaper entry (type, title, resolution, all image URLs, video URLs).
12. download_images(urls, subdir, referer) — Batch download images or videos from direct URLs. Pass the site root as referer for hotlink-protected sites.

How to tell a LIST page from a DETAIL page:
- LIST page: an index/directory/category page with many entries (e.g. a novel's chapter list, a news category, search results). The user wants to collect many links → use extract_list.
- DETAIL page: a single article/chapter/product page with full text content. The user wants the body text → use extract_content.

Workflow for a LIST page task (e.g. "抓取这个小说的所有章节链接", "列出这个分类下的所有文章"):
1. Try crawl_webpage first to get the HTML.
2. Call extract_list on the HTML (pass the page URL as `url` for resolving relative links).
3. IMPORTANT hard rule: if extract_list returns FEWER THAN 5 items (or fails), the static HTML was likely JS-rendered or blocked. Switch to browse_and_crawl to re-fetch the page, then call extract_list again on the returned HTML.
4. Present the list to the user. If the user wants the full content of each item, iterate through the list (crawl_webpage → extract_content per item), or ask the user which items to fetch.

Workflow for a DETAIL page task:
- When the user gives a URL or task, try crawl_webpage first
- If crawl_webpage returns incomplete content, JS-rendered page, or fails, switch to browse_and_crawl
- Then call extract_content to parse the HTML (skip if browse_and_crawl already returned text)
- If the user asks to save as a file (e.g. markdown, txt), use save_to_file
- If the user asks to save to database, use save_record
- Finally, summarize what you did and what you got

Workflow for social media:
- If the user gives a Douyin or Bilibili link (including share links), call extract_social_media directly instead of crawl_webpage
- If the user explicitly asks to download/save the video, cover, images, audio, or comments, call download_social_media

Workflow for wallpaper/image sites (壁纸/图片类网站):
- These sites render cards with CSS background-image (not <img> tags), so extract_list (which only handles <a> text links) will miss them. Use extract_wallpaper_list instead.
    - Pass the site's list/category URL. The tool auto-paginates, extracts card links, fetches detail pages, and returns type (STATIC/DYNAMIC), title, resolution, thumbnail, and image URLs.
    - Use exclude_dynamic=true if the user says "不要动态壁纸" (skip dynamic wallpapers).
- If the user asks for a specific wallpaper's details, use wallpaper_detail.
- If the user asks to SAVE or DOWNLOAD the images/videos:
    - Collect image URLs from the extract_wallpaper_list or wallpaper_detail output.
    - Call download_images(urls=..., subdir=<site_name>, referer=<site_root_url>).

Rules:
- CONCISE TOOL USE: Do NOT write narration/status text before or after each tool call (like "let me try...", "I'll now..."). Call tools silently, and after ALL tools finish give ONE consolidated final answer. This keeps your reply as a single message instead of many short fragments.
- When the user asks about previously crawled content (e.g. "我抓过哪些", "list my crawled", "have I crawled X before"), call list_crawled_resources FIRST instead of crawling again. This avoids unnecessary network requests and anti-crawl interception.
- SUPERVISOR HARD RULE (confidence auto-upgrade): After calling extract_content, ALWAYS inspect the confidence marker at the end of the result.
    - If you see "[LOW CONFIDENCE: score=N, reasons=...]", the extraction failed (SPA not rendered, anti-crawl block, garbled font, too short, etc.). Do NOT save this result and do NOT report it to the user. Instead, immediately call browse_and_crawl with the same URL to re-fetch via a browser, then run extract_content again on the returned HTML. A LOW CONFIDENCE marker means static crawling was insufficient.
    - If you see "[CONFIDENCE: score=N]" (N >= 60), the extraction is trustworthy. Proceed normally.
- Always respond in English, even if the user writes in Chinese. Only switch to Chinese when the user explicitly requests it (e.g. "reply in Chinese", "用中文回答").
- If crawling fails, explain the reason clearly
- If the user specifies what to focus on, pass it as the focus parameter
- For general conversation (greetings, questions about you), respond naturally without calling tools
- Remember the conversation context within the current session
- IMPORTANT: Do NOT save data if the content extraction failed or returned empty/garbled results. Only persist valid, meaningful content. If extraction fails, report the failure to the user without calling save_record or save_to_file.
- browse_and_crawl automatically handles VIP-locked chapters via a proxy API. If it still returns a [WARNING] about VIP-lock after proxy fallback, then report the failure to the user."""


def get_agent(checkpointer: BaseCheckpointSaver | None = None):
    """Create the Agent.

    Uses LangChain's create_agent which handles the LLM + tool execution loop
    internally. The system prompt is pure static for prompt caching.

    Args:
        checkpointer: 可选的 LangGraph 检查点存储（如 SqliteSaver）。传入后，
            Agent 会按 config["configurable"]["thread_id"] 持久化整个会话状态，
            进程重启后用同一 thread_id 调用即可恢复上下文；为 None 时退化为
            无记忆的单次执行（与旧版行为一致）。

    Returns:
        Compiled LangGraph agent, callable via .stream({"messages": [...]}, config=...)
    """
    llm = get_llm()
    tools = [
        crawl_webpage,
        browse_and_crawl,
        extract_content,
        extract_list,
        save_record,
        list_crawled_resources,
        save_to_file,
        extract_social_media,
        download_social_media,
        extract_wallpaper_list,
        wallpaper_detail,
        download_images,
    ]

    # Token 膨胀治理：滑动窗口 + 工具结果裁剪。
    # wrap_model_call 只改传给 LLM 的 messages，不污染 checkpointer 持久化的完整 state。
    settings = get_settings()
    trim_middleware = TrimHistoryMiddleware(
        max_tokens=settings.history_max_tokens,
        keep_recent_turns=settings.history_keep_recent_turns,
        tool_result_max_chars=settings.tool_result_max_chars,
    )

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        middleware=[trim_middleware],
    )

    return agent
