"""Browser tool — Playwright direct render for dynamic/JS-rendered pages.

Uses Playwright to render JS, handle SPA navigation, and extract rendered
content that plain HTTP requests cannot fetch. Includes font-encryption
decryption for sites like Fanqie Novel that map real chars to PUA codepoints.
VIP-locked chapters are fetched via a content proxy API.
"""
import asyncio
import re
from langchain_core.tools import tool
from crawagent.config.settings import get_settings


def _fetch_locked_chapter(item_id: str) -> str:
    """Fetch full content of a VIP-locked chapter via proxy API.

    Args:
        item_id: The chapter's itemId (last path segment of the reader URL).

    Returns:
        Cleaned plain text of the full chapter, or empty string on failure.
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


@tool
def browse_and_crawl(url: str, task: str = "") -> str:
    """Use a headless browser to crawl dynamic/JS-rendered web pages.

    Use this tool when crawl_webpage fails or returns incomplete content
    (e.g. SPA pages, JS-rendered content, lazy-loaded images, font-encrypted text).
    The browser renders the full page including JavaScript, then extracts the content.
    If the page uses font-based encryption (PUA characters), the text is automatically
    decrypted using Source Han Sans glyph matching.

    Args:
        url: The target URL, must include http:// or https://
        task: Optional instruction describing what content to extract from the page.
              If empty, extracts the main body text.

    Returns:
        On success: the extracted (and decrypted if needed) page content as text.
        On failure: "Browse failed: <error details>".
    """
    async def _run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
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

                return { isChapterList: false, text, title, fontUrls, stateFontUrl,
                         chapterLock, chapterWordNumber };
            }""")

            await browser.close()

        # --- Book/catalog page: return chapter list ---
        if result.get("isChapterList"):
            parts = []
            if result.get("title"):
                parts.append(f"Title: {result['title']}")
            parts.append(f"Chapter List ({result.get('chapterCount', 0)} chapters):")
            parts.append(result.get("chapterList", ""))
            if task:
                parts.append(f"Focus: {task}")
            return "\n\n".join(parts)

        # --- Reader page: extract and decrypt body text ---
        text = result.get("text", "")
        title = result.get("title", "")
        font_urls = result.get("fontUrls", [])
        state_font_url = result.get("stateFontUrl")
        chapter_lock = result.get("chapterLock", False)
        chapter_word_number = result.get("chapterWordNumber", 0)

        # Decrypt font-encrypted text if PUA characters are detected
        from crawagent.tools.font_decrypt import has_pua, decrypt_text

        if has_pua(text):
            # Prefer woff2 URL from stylesheets, then __INITIAL_STATE__
            font_url = font_urls[0] if font_urls else state_font_url
            if font_url:
                text = decrypt_text(text, font_url)

        # Build output
        parts = []
        if title:
            parts.append(f"Title: {title}")
        if task:
            parts.append(f"Focus: {task}")
        if chapter_lock:
            # VIP-locked chapter: try fetching full content via proxy API
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
            parts.append(text)
        return "\n\n".join(parts)

    try:
        return asyncio.run(_run())
    except Exception as e:
        return f"Browse failed: {e}"
