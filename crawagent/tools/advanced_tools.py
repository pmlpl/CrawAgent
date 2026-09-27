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
import json
import os
from pathlib import Path

from langchain_core.tools import tool

from crawagent.tools.progress import report_progress


def _on_browser_step(state: object, output: object, step_no: int) -> None:
    """browser-use 每步回调（register_new_step_callback）→ report_progress 里程碑。

    动作摘要从 output.action[0] 提取（第一个非空动作键 + 参数，截断 ~60 字），
    取不出时退化为"步骤 N 执行中"。整个回调 try/except 静默吞异常：
    进度上报失败绝不影响浏览器任务主流程。由 browser-use 的事件循环线程调用，
    report_progress 内部 _LOCK 线程安全，无需额外处理。
    """
    try:
        summary = ""
        try:
            actions = getattr(output, "action", None) or []
            if actions:
                dump = actions[0].model_dump(exclude_unset=True)
                for key, val in dump.items():
                    if not _meaningful(val):
                        continue
                    if isinstance(val, str):
                        params = val
                    else:
                        try:
                            params = json.dumps(val, ensure_ascii=False)
                        except Exception:
                            params = str(val)
                    summary = f"{key}: {params}"[:60]
                    break
        except Exception:
            summary = ""
        report_progress(f"步骤 {step_no}：{summary}" if summary else f"步骤 {step_no} 执行中")
    except Exception:
        pass


def _meaningful(val: object) -> bool:
    """动作参数是否含有效信息：None/空串/全空字典（如 done: {"text": ""}）视为无效。"""
    if val is None or val == "":
        return False
    if isinstance(val, dict):
        return any(v not in (None, "") for v in val.values())
    return True


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


# 常见 locale 路径段（文档站的语言前缀）。preferred 及其变体保留，其余 deny。
_LOCALE_SEGMENTS = (
    "en", "en-us", "en-gb", "en-au", "en-ca", "de", "es", "es-es", "es-mx", "fr",
    "fr-ca", "it", "pt", "pt-br", "ru", "ja", "ja-jp", "ko", "ko-kr", "ar",
    "nl", "pl", "tr", "vi", "th", "id", "zh", "zh-cn", "zh-tw", "zh-hans",
    "zh-hant", "cs", "el", "fi", "sv", "da", "no", "hu", "ro", "uk", "hi",
    "bn", "fa", "ms", "fil", "he",
)


def _lang_deny_patterns(lang: str) -> list[str]:
    """lang="zh" → 返回 glob deny 模式：丢弃其它语种 /xx/ 路径段，
    保留 preferred(+变体) + 语言中性 URL（无 locale 前缀的页面，如 /api/...）。

    匹配"与起始语言前缀不一致"的语义：preferred 的变体（zh-cn/zh-tw/zh-hans/zh-hant）
    也保留；只 deny 明确是其它语种的段。语言中性 URL 不命中任何 deny → 保留。
    """
    lang = (lang or "").strip().lower()
    if not lang:
        return []
    # preferred 及其变体保留（lang=zh → zh/zh-cn/zh-tw/zh-hans/zh-hant 都留）
    keep = {c for c in _LOCALE_SEGMENTS if c == lang or c.startswith(lang + "-")}
    deny = [c for c in _LOCALE_SEGMENTS if c not in keep]
    # glob：*/de/* 命中路径中段 /de/；*/de 命中末段（少见的纯语种路径）
    return [f"*/{c}/*" for c in deny] + [f"*/{c}" for c in deny]


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
    patterns = _lang_deny_patterns(lang)
    if patterns:
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


def _pick_pool_vision_llm() -> tuple[str, str, str] | None:
    """模型池中第一个两维全过的模型（033）：vision 与 structured 都显式为 True。

    pattern 推测 / 手动勾选可以让 vision 生效（get_caps 合并语义），
    但 structured 只认探测结果——两维没全过就不自动选（browser-use 靠
    json_schema 结构化输出拿模型动作，JSON 关不过比盲跑更糟）。

    Returns:
        (model, base_url, api_key)，无合格模型返回 None（调用方回落主模型）。
    """
    from crawagent.llm.capabilities import get_caps
    from crawagent.llm.registry import load_providers

    for p in load_providers():
        base, key = p.get("base_url", ""), p.get("api_key", "")
        if not base or not key:
            continue
        for m in p.get("models") or []:
            caps = get_caps(p, m)
            if caps["vision"] is True and caps["structured"] is True:
                return m, base, key
    return None


@tool
def browser_use_navigate(url: str, task: str) -> str:
    """Drive a real browser via an LLM agent to complete an interactive task.

    Uses browser-use: an LLM-controlled browser that can log in, click, fill forms,
    paginate, and screenshot — for sites that need genuine interaction (login walls,
    "load more", click-to-reveal). Call this when crawl_webpage/browse_and_crawl can't
    get the content because the page REQUIRES interaction, e.g. "登录后把我的订单列表抓
    下来"、"点'下一页'翻完所有页"、"填搜索框搜 X 再抓结果".

    The LLM driving the browser is auto-selected (cheap/fast is fine for browser
    steps): manual browser-subagent config > BROWSER_USE_LLM_* env > first verified
    vision model in the model pool > main LLM. The result starts with a
    "[子Agent模型: ...]" header showing which model actually drove the browser.
    Needs Playwright browsers installed (crawl4ai-setup / playwright
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

    # LLM 配置（四档，浏览器操作用便宜模型即可）：
    #  1) 设置页「浏览器子 Agent」手配（Settings.browser_use_llm_model，032）：
    #     base_url / api_key 留空时回落主 provider 对应值（只填模型名即可复用现有服务商）
    #  2) os.environ 的 BROWSER_USE_LLM_* 兜底（向后兼容已手写 .env 的用户，语义原样）：
    #     _API_KEY（+可选 _BASE_URL/_MODEL）独立 cheap key；仅 _MODEL 复用主 provider 换模型
    #  3) 模型池中第一个两维全过的模型（033：vision + structured 显式探测通过）
    #  4) 都无：主 LLM（现状，可能盲跑）
    picked_label = ""
    try:
        from crawagent.config.settings import get_settings
        from crawagent.llm.registry import resolve_model
        from crawagent.llm.model import _ensure_no_proxy_for

        s = get_settings()
        cfg_model = (s.browser_use_llm_model or "").strip()
        cfg_base = (s.browser_use_llm_base_url or "").strip()
        cfg_key = (s.browser_use_llm_api_key or "").strip()

        env_key = os.environ.get("BROWSER_USE_LLM_API_KEY", "").strip()
        env_model = os.environ.get("BROWSER_USE_LLM_MODEL", "").strip()
        env_base = os.environ.get("BROWSER_USE_LLM_BASE_URL", "").strip()

        if cfg_model:
            # ① 设置页手配：base/key 留空回落主 provider（与"复用 provider 换模型"语义一致）
            _, main_base, main_key, _adapter = resolve_model(None)
            base = cfg_base or main_base
            llm = ChatOpenAILike(model=cfg_model, api_key=cfg_key or main_key, base_url=base)
            _ensure_no_proxy_for(base)
            picked_label = cfg_model
        elif env_key:
            base = env_base or "https://api.openai.com/v1"
            model = env_model or "gpt-4o-mini"
            llm = ChatOpenAILike(model=model, api_key=env_key, base_url=base)
            _ensure_no_proxy_for(base)
            picked_label = model
        elif env_model:
            # 复用 CrawAgent provider 只换模型（os.environ 的 _MODEL 通常已被 pydantic
            # 并入 Settings 走第①档；这里仅兜极少数未经 pydantic 的进程环境）
            model_name, base_url, api_key, _adapter = resolve_model(None)
            llm = ChatOpenAILike(model=env_model, api_key=api_key, base_url=base_url)
            _ensure_no_proxy_for(base_url)
            picked_label = env_model
        else:
            # ③ 池中第一个两维全过的模型；无合格模型回落主模型（④）
            pool = _pick_pool_vision_llm()
            if pool:
                model, base, key = pool
                llm = ChatOpenAILike(model=model, api_key=key, base_url=base)
                _ensure_no_proxy_for(base)
                picked_label = f"{model} (vision)"
            else:
                model_name, base_url, api_key, _adapter = resolve_model(None)
                llm = ChatOpenAILike(model=model_name, api_key=api_key, base_url=base_url)
                _ensure_no_proxy_for(base_url)
                picked_label = f"{model_name} (主模型兜底，池中无两维全过模型)"
    except Exception as e:
        return f"[ERROR] 无法构造 LLM（检查 .env 的 LLM_PROVIDERS 或 BROWSER_USE_LLM_* 环境变量）: {e}"

    async def _run() -> str:
        # browser-use 0.13：Agent 直接接 browser=Browser(headless=...)
        browser = Browser(headless=False)
        agent = Agent(
            task=f"打开 {url} 然后完成：{task}",
            llm=llm,
            browser=browser,
            register_new_step_callback=_on_browser_step,  # 步进里程碑 → trace 卡片实时展示
        )
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
        # 挑选结果让用户可感知（033）：自动选了谁、为什么
        return f"[子Agent模型: {picked_label}]\n{out}"
    except Exception as e:
        return f"[ERROR] 浏览器交互失败: {type(e).__name__}: {e}"
