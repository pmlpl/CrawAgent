"""高级原生工具 — 把预装的四库包成 CrawAgent 原生工具（变更 009）。

三个工具：
  - markitdown_convert(file_path)：PDF/Word/Excel/PPT/图片 → Markdown（纯转换，无 LLM）
  - crawl4ai_deep_crawl(url, max_pages)：整站 DFS/BFS 深度抓取 + Markdown 输出
  - browser_use_navigate(url, task)：LLM 驱动的浏览器交互（登录/点击/填表/翻页）

依赖（已预装进 .venv）：markitdown、crawl4ai、browser_use、scrapling、curl_cffi。
都用 lazy import（重依赖不在模块加载期 import，避免拖慢 Agent 构建）。
browser_use 复用 CrawAgent 的 LLM 接入（选项 A，get_llm()）。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from langchain_core.tools import tool


@tool
def markitdown_convert(file_path: str) -> str:
    """Convert a local file (PDF / Word / Excel / PPT / image / html) to Markdown.

    Uses the markitdown library — pure format conversion, no LLM tokens spent.
    Call this when the user has a local document file and wants its content as
    text/Markdown (e.g. "把这个 PDF 转成文字"、"读一下这个 Word/Excel 的内容").
    The output Markdown can then be saved via save_record to grow the knowledge base.

    Args:
        file_path: absolute path to the local file (pdf/docx/xlsx/pptx/png/jpg/html...).

    Returns:
        Markdown text of the file (truncated to ~30000 chars if huge).
        "[ERROR] ..." on failure (file not found / unsupported / conversion error).
    """
    p = Path(file_path).expanduser()
    if not p.is_file():
        return f"[ERROR] 文件不存在: {file_path}"
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(str(p))
        text = getattr(result, "text_content", None) or str(result)
        if not text or not text.strip():
            return f"[ERROR] 转换结果为空（文件可能加密/扫描件/不支持）: {p.name}"
        if len(text) > 30000:
            text = text[:30000] + f"\n\n... [truncated, original {len(text)} chars]"
        return text
    except Exception as e:
        return f"[ERROR] markitdown 转换失败 ({p.name}): {type(e).__name__}: {e}"


@tool
def crawl4ai_deep_crawl(url: str, max_pages: int = 50, lang: str = "") -> str:
    """Deep-crawl a whole site (DFS/BFS traversal) and return Markdown per page.

    Uses crawl4ai's AsyncWebCrawler + BestFirstCrawlingStrategy — handles JS rendering
    (Playwright), follows internal links, and outputs clean Markdown per page. Call this
    when the user wants a WHOLE site/docs site crawled in one shot (not a single page —
    for single page use crawl_webpage/browse_and_crawl), e.g. "把这个文档站全抓下来"、
    "整站爬取". Output is a list of {url, markdown_preview}; save each to the KB via
    save_record for retrieve-first later.

    Heavy: spawns a Playwright browser + crawls up to max_pages. Defaults to 50 pages.
    Raises per-page failures are skipped (one bad page doesn't kill the whole crawl).

    Language dedup: pass lang="zh" to DROP alternate-language variants of the same page
    (e.g. /de/ /es/ /fr/ /en/ ...), keeping only the preferred language + language-neutral
    URLs. Saves quota by not crawling the same content in 10 languages.

    Args:
        url: starting URL (site root or docs index).
        max_pages: cap on pages crawled (default 50; lower for quick tests).
        lang: preferred language code (e.g. "zh", "en"). Empty = no language filter.

    Returns:
        "Crawled N pages:\n1. <url>\n   <markdown 前 200 字>...\n2. ..."
        Or "[ERROR] ..." if crawl4ai unavailable / crawl fails entirely.
    """
    max_pages = max(1, min(int(max_pages or 50), 500))
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
        from crawl4ai.deep_crawling import BestFirstCrawlingStrategy
        from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter
    except Exception as e:
        return f"[ERROR] crawl4ai 未安装或损坏: {type(e).__name__}: {e}"

    # 语言变体去重：lang=zh 时 deny 其它语种的 /xx/ 路径段（语言中性 URL 保留）
    filter_chain = FilterChain()
    lang = (lang or "").strip().lower()
    if lang:
        # 常见语种；preferred 及其变体保留，其余 deny
        ALL_LANGS = ["en", "de", "es", "fr", "it", "pt", "ru", "ja", "ko", "ar",
                     "nl", "pl", "tr", "vi", "th", "id", "zh", "zh-cn", "zh-tw",
                     "zh-hans", "zh-hant", "ja-jp", "ko-kr", "en-us", "en-gb"]
        # preferred 的变体也保留（如 lang=zh → zh/zh-cn/zh-tw/zh-hans/zh-hant 都留）
        keep = {c for c in ALL_LANGS if c == lang or c.startswith(lang + "-")}
        deny = [c for c in ALL_LANGS if c not in keep]
        patterns = [f"*/{c}/*" for c in deny] + [f"*/{c}" for c in deny]
        # reverse=True = deny 模式：匹配的 URL 丢弃
        filter_chain = FilterChain([URLPatternFilter(patterns, reverse=True)])

    async def _run() -> list:
        strat = BestFirstCrawlingStrategy(
            max_depth=3, max_pages=max_pages, filter_chain=filter_chain
        )
        cfg = CrawlerRunConfig(deep_crawl_strategy=strat, stream=False, cache_mode="BYPASS")
        results: list = []
        async with AsyncWebCrawler() as crawler:
            res = await crawler.arun(url=url, config=cfg)
        items = res if isinstance(res, (list, tuple)) else [res]
        for r in items:
            try:
                md = getattr(r, "markdown", None) or getattr(r, "text_content", None) or ""
                if not isinstance(md, str):
                    md = str(md)
                results.append({"url": getattr(r, "url", "?"), "md": md})
            except Exception:
                continue
        return results

    try:
        items = asyncio.run(_run())
    except Exception as e:
        return f"[ERROR] 深度抓取失败: {type(e).__name__}: {e}"
    if not items:
        return f"[ERROR] 抓取 0 页（url={url} 可能不可达或全是 JS 空壳）"

    lines = [f"Crawled {len(items)} pages (from {url}" + (f", lang={lang}" if lang else "") + "):"]
    for i, it in enumerate(items, 1):
        preview = (it["md"] or "").replace("\n", " ").strip()[:200]
        lines.append(f"{i}. {it['url']}")
        lines.append(f"   {preview}{'...' if len(it['md']) > 200 else ''}")
    return "\n".join(lines)


@tool
def browser_use_navigate(url: str, task: str) -> str:
    """Drive a real browser via an LLM agent to complete an interactive task.

    Uses browser-use: an LLM-controlled browser that can log in, click, fill forms,
    paginate, and screenshot — for sites that need genuine interaction (login walls,
    "load more", click-to-reveal). Call this when crawl_webpage/browse_and_crawl can't
    get the content because the page REQUIRES interaction, e.g. "登录后把我的订单列表抓
    下来"、"点'下一页'翻完所有页"、"填搜索框搜 X 再抓结果".

    The LLM driving the browser is CrawAgent's own configured LLM (get_llm) — no separate
    key needed. Light/cheap models are fine for browser steps; the heavy reasoning stays
    in the main agent. Needs Playwright browsers installed (crawl4ai-setup / playwright
    install). Browser may be visible or headless; this is slower than crawl_webpage —
    only use it when interaction is truly required.

    Args:
        url: starting URL to open.
        task: natural-language instruction for the browser agent, e.g.
              "点击'登录'，用户名 admin 密码 xxx，登录后点'我的订单'，把订单表格抓下来".

    Returns:
        The browser agent's final result text (the extracted content / confirmation).
        "[ERROR] ..." on failure (browser-use unavailable / browser not installed / task failed).
    """
    try:
        from browser_use import Agent, Browser
        from browser_use.llm.openai.like import ChatOpenAILike
    except Exception as e:
        return f"[ERROR] browser_use 未安装或损坏: {type(e).__name__}: {e}"

    # LLM 配置（三档，浏览器操作用便宜模型即可）：
    #  1) BROWSER_USE_LLM_API_KEY（+ 可选 _BASE_URL/_MODEL）：独立 cheap key（推荐，如 OpenAI gpt-4o-mini）
    #  2) 仅 BROWSER_USE_LLM_MODEL：复用 CrawAgent 的 provider base_url+key，但换便宜模型
    #     （如 glm-5.3-flash / glm-5.3-free，同局域网免额外 key）
    #  3) 都不设：默认用 CrawAgent 的主 LLM（glm-5.2，带思考，较贵——不推荐用于浏览器步骤）
    try:
        from crawagent.llm.registry import resolve_model
        from crawagent.llm.model import _ensure_no_proxy_for

        env_key = os.environ.get("BROWSER_USE_LLM_API_KEY", "").strip()
        env_model = os.environ.get("BROWSER_USE_LLM_MODEL", "").strip()
        env_base = os.environ.get("BROWSER_USE_LLM_BASE_URL", "").strip()

        if env_key:
            base = env_base or "https://api.openai.com/v1"
            model = env_model or "gpt-4o-mini"
            llm = ChatOpenAILike(model=model, api_key=env_key, base_url=base)
            _ensure_no_proxy_for(base)
        else:
            # 复用 CrawAgent provider（.env LLM_PROVIDERS 解析）
            model_name, base_url, api_key, _adapter = resolve_model(None)
            model = env_model or model_name  # 允许只换模型不换 key
            llm = ChatOpenAILike(model=model, api_key=api_key, base_url=base_url)
            _ensure_no_proxy_for(base_url)
    except Exception as e:
        return f"[ERROR] 无法构造 LLM（检查 .env 的 LLM_PROVIDERS 或 BROWSER_USE_LLM_* 环境变量）: {e}"

    async def _run() -> str:
        # browser-use 0.13：Agent 直接接 browser=Browser(headless=...)
        browser = Browser(headless=False)
        agent = Agent(task=f"打开 {url} 然后完成：{task}", llm=llm, browser=browser)
        result = await agent.run()
        # browser-use AgentHistoryList：取最后一条 result / extracted_content
        final = getattr(result, "final_result", None)
        if callable(final):
            final = final()
        if not final:
            extracted = getattr(result, "extracted_content", None)
            final = extracted or str(result)
        return str(final)

    try:
        out = asyncio.run(_run())
        if len(out) > 20000:
            out = out[:20000] + f"\n\n... [truncated, original {len(out)} chars]"
        return out
    except Exception as e:
        return f"[ERROR] 浏览器交互失败: {type(e).__name__}: {e}"
