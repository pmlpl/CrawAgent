"""浏览器工具 — Playwright 无头渲染动态/JS 渲染页面。

用 Playwright 跑完整 JS、处理 SPA 跳转、抽出普通 requests 拿不到的已渲染内容。
额外能力：番茄小说这类把真实字映射到 PUA 私用码位的「字体加密」页面，
内置思源黑体字形比对自动解密；VIP 锁定章节通过内容代理接口抓取。
"""
import asyncio
import re

import html2text
from langchain_core.tools import tool
from crawagent.config.settings import get_settings


def _html_to_md(raw_html: str) -> str:
    """浏览器渲染后的 HTML → Markdown（保留标题/列表/代码块/行内链接）。

    内容过短或转换后为空时返回空串，调用方回退纯文本。"""
    if not raw_html or len(raw_html) < 200:
        return ""
    h2t = html2text.HTML2Text()
    h2t.body_width = 0              # 不自动折行
    h2t.backquote_code_style = True # 块级代码用 ``` 围栏并保留原始缩进
    h2t.ul_item_mark = "-"          # 无序列表用 - 标记
    h2t.single_line_break = False
    md = h2t.handle(raw_html).strip()
    return md if len(md) > 50 else ""


def _fetch_locked_chapter(item_id: str) -> str:
    """通过代理接口抓取 VIP 锁定章节的完整正文。

    参数：
        item_id: 章节 itemId（阅读器 URL 的最后一段路径）。

    返回：
        清洗后的全章节纯文本；失败返回空串。
    """
    import requests
    from bs4 import BeautifulSoup

    api = get_settings().locked_chapter_api
    if not api:
        return ""

    try:
        resp = requests.get(
            api,
            params={"item_id": item_id},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/120.0.0.0 Safari/537.36"},
            timeout=20,
        )
        data = resp.json()
        if data.get("code") != 200:
            return ""

        raw_html = data.get("data", {}).get("content", "")
        if not raw_html:
            return ""

        # Clean HTML: extract <p> tags, remove audio/ASR markers
        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup.find_all(["div", "span", "section"]):
            if tag.get("class") and "novel-fm-asr" in " ".join(tag.get("class")):
                tag.decompose()
        p_tags = soup.find_all("p")
        if p_tags:
            lines = [p.get_text(strip=True) for p in p_tags if p.get_text(strip=True)]
            text = "\n\n".join(lines)
        else:
            text = soup.get_text(separator="\n")
        # Remove PGC voice markers
        text = re.sub(r"\{!--\s*PGC_VOICE:.*?--\}", "", text, flags=re.DOTALL)
        return text.strip()
    except Exception:
        return ""


def _build_proxy_kwargs() -> dict:
    """构建 Playwright launch 参数，含代理透传（settings.default_proxy 或 HTTPS_PROXY env）。"""
    launch_kw = {"headless": True}
    _proxy_url = ""
    try:
        from crawagent.config.settings import get_settings
        _proxy_url = get_settings().default_proxy or ""
    except Exception:
        pass
    if not _proxy_url:
        import os as _os
        _proxy_url = _os.environ.get("HTTPS_PROXY") or _os.environ.get("HTTP_PROXY") or ""
    if _proxy_url:
        from urllib.parse import urlparse as _up, unquote as _uq
        _pu = _up(_proxy_url if "://" in _proxy_url else "http://" + _proxy_url)
        _scheme = _pu.scheme or "http"
        _ph = _pu.hostname or ""
        _pp = _pu.port or (1080 if "socks" in _scheme else 8080)
        launch_kw["proxy"] = {"server": f"{_scheme}://{_ph}:{_pp}"}
        if _pu.username:
            launch_kw["proxy"]["username"] = _uq(_pu.username)
        if _pu.password:
            launch_kw["proxy"]["password"] = _uq(_pu.password)
    return launch_kw


def _format_chapter_list(result: dict, task: str) -> str:
    """格式化章节列表页结果。"""
    parts = []
    if result.get("title"):
        parts.append(f"Title: {result['title']}")
    parts.append(f"Chapter List ({result.get('chapterCount', 0)} chapters):")
    parts.append(result.get("chapterList", ""))
    if task:
        parts.append(f"Focus: {task}")
    return "\n\n".join(parts)


def _process_reader_page(result: dict, url: str, task: str) -> str:
    """处理阅读器/文章页：字体解密 + VIP 锁定代理 + HTML 转 Markdown。"""
    text = result.get("text", "")
    title = result.get("title", "")
    font_urls = result.get("fontUrls", [])
    state_font_url = result.get("stateFontUrl")
    chapter_lock = result.get("chapterLock", False)
    chapter_word_number = result.get("chapterWordNumber", 0)

    from crawagent.tools.font_decrypt import has_pua, decrypt_text

    if has_pua(text):
        font_url = font_urls[0] if font_urls else state_font_url
        if font_url:
            text = decrypt_text(text, font_url)

    parts = []
    if title:
        parts.append(f"Title: {title}")
    if task:
        parts.append(f"Focus: {task}")
    if chapter_lock:
        item_id = url.rstrip("/").split("/")[-1]
        full_text = _fetch_locked_chapter(item_id)
        if full_text:
            parts.append(full_text)
        else:
            parts.append(
                f"[WARNING] This chapter is VIP-locked (isChapterLock=true). "
                f"Only a preview ({len(text)} chars) is available. "
                f"Full chapter is {chapter_word_number} words. "
                f"Proxy API failed; login with a VIP account may be required."
            )
            parts.append(text)
    else:
        if has_pua(text):
            parts.append(text)
        else:
            md = _html_to_md(result.get("html", ""))
            parts.append(md if md else text)
    return "\n\n".join(parts)


@tool
def browse_and_crawl(url: str, task: str = "") -> str:
    """用无头浏览器爬取 SPA/JS 动态渲染页面。

    当 crawl_webpage 失败或返回不完整内容时用它（例如 SPA 应用、
    JS 渲染正文、懒加载图片、字体加密文字）。浏览器完整渲染后再抽取内容，
    若命中 PUA 字体加密页会自动用思源黑体字形映射做解密。

    参数：
        url: 目标 URL，必须带 http:// 或 https://
        task: 可选指令，说明要从页面取什么内容；为空则只抽正文主体。

    返回：
        成功：抽取并（必要时）解密后的页面文本。
        失败："浏览失败：<错误详情>"。
    """
    async def _run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(**_build_proxy_kwargs())
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)

            # Extract content, title, chapter list, and font URLs in one call
            result = await page.evaluate("""() => {
                const title = document.title || '';

                // --- Chapter list extraction (book/catalog pages) ---
                // Detect /reader/ links which indicate a chapter list page
                const chapterLinks = Array.from(
                    document.querySelectorAll('a[href*="/reader/"]')
                ).map(a => ({
                    title: a.innerText.trim(),
                    url: a.href
                })).filter(c => c.title && c.title.length > 0);

                // Deduplicate by URL (page may have duplicate links)
                const seen = new Set();
                const chapters = [];
                for (const c of chapterLinks) {
                    if (!seen.has(c.url)) {
                        seen.add(c.url);
                        chapters.push(c);
                    }
                }

                // If we found a chapter list, return it directly
                if (chapters.length >= 3) {
                    return {
                        isChapterList: true,
                        title,
                        chapterCount: chapters.length,
                        chapterList: chapters.map((c, i) =>
                            `${i + 1}. ${c.title} | ${c.url}`
                        ).join('\\n')
                    };
                }

                // --- Body content extraction (reader/article pages) ---
                const el = document.querySelector('.muye-reader-content') ||
                           document.querySelector('article') ||
                           document.body;
                const text = el ? el.innerText : '';
                const html = el ? el.innerHTML : '';

                // Collect @font-face URLs from stylesheets
                const fontUrls = [];
                for (const s of document.styleSheets) {
                    try {
                        for (const r of s.cssRules) {
                            if (r.type === CSSRule.FONT_FACE_RULE) {
                                const m = r.cssText.match(/url\\(["']?(https?:\\/\\/[^"')\\s]+\\.woff2)/);
                                if (m) fontUrls.push(m[1]);
                            }
                        }
                    } catch(e) {}
                }

                // Fallback: check __INITIAL_STATE__ for font CSS and chapter lock status
                let stateFontUrl = null;
                let chapterLock = false;
                let chapterWordNumber = 0;
                try {
                    const st = window.__INITIAL_STATE__;
                    if (st && st.common && st.common.css) {
                        const m = st.common.css.match(/url\\((https?:\\/\\/[^)]+\\.woff2)/);
                        if (m) stateFontUrl = m[1];
                    }
                    // Detect VIP-locked chapters (Fanqie pattern)
                    if (st && st.reader && st.reader.chapterData) {
                        const cd = st.reader.chapterData;
                        chapterLock = cd.isChapterLock === true;
                        chapterWordNumber = parseInt(cd.chapterWordNumber) || 0;
                    }
                } catch(e) {}

                return { isChapterList: false, text, html, title, fontUrls, stateFontUrl,
                         chapterLock, chapterWordNumber };
            }""")

            await browser.close()

        if result.get("isChapterList"):
            return _format_chapter_list(result, task)
        return _process_reader_page(result, url, task)

    try:
        return asyncio.run(_run())
    except Exception as e:
        return f"Browse failed: {e}"
