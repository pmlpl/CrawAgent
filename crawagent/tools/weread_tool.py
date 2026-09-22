"""WeRead (微信读书) 站点工具 — Cookie 认证 + 内部 API

微信读书的正文与目录在未登录状态下完全不返回（API 返回 404 或"用户不存在"），
必须携带登录后的 Cookie（wr_vid + wr_ssk）才能访问内部 API。

工具流程：
    list_weread_chapters(url)  → 从详情页提取 bookId → 调 /web/book/info 拿章节列表
    get_weread_chapter(v, cid) → 调 /web/book/getBookReader 拿单章正文

Cookie 获取方式：
    1. 浏览器登录 https://weread.qq.com/
    2. F12 → Application → Cookies → 复制 wr_vid 和 wr_ssk 的值
    3. 把 Cookie（格式：wr_vid=xxx; wr_ssk=yyy）发给智能体保存，或在
       「站点档案」页为 weread.qq.com 配置 —— 存在站点档案 data/sites.json 里，
       与全局设置无关。
"""
import json
import re

import requests
from langchain_core.tools import tool

from crawagent.tools.crawl_tool import DEFAULT_UA, _enforce_delay
from crawagent.tools.site_profile_tool import get_site_cookies

_BASE = "https://weread.qq.com"


def _weread_cookie() -> str:
    """从微信读书的站点档案读取 Cookie（未配置时返回空串）"""
    return get_site_cookies(_BASE)


def _weread_headers() -> dict[str, str]:
    """构造带 Cookie 的请求头"""
    h = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": _BASE + "/",
    }
    cookie = _weread_cookie()
    if cookie:
        h["Cookie"] = cookie
    return h


def _extract_book_id(html: str) -> str | None:
    """从详情页 HTML 中提取数字 bookId"""
    # 常见位置：meta 标签、__INITIAL_STATE__、JSON-LD
    patterns = [
        r'"bookId"\s*:\s*"?(\d+)"?',
        r'weread:book_id"\s*content="(\d+)"',
        r'"bookId"\s*:\s*(\d+)',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def _extract_v_from_url(url: str) -> str | None:
    """从详情页 URL 提取 v 参数（阅读器路径标识）"""
    m = re.search(r"[?&]v=([a-f0-9]+)", url)
    return m.group(1) if m else None


@tool
def list_weread_chapters(url_or_book_id: str) -> str:
    """List all chapters of a WeRead (微信读书) book.

    Requires the WeRead cookie (wr_vid + wr_ssk) stored in the weread.qq.com
    site profile. If the cookie is not configured, returns a clear error
    message explaining how to set it up.

    Args:
        url_or_book_id: WeRead book detail URL
                        (e.g. https://weread.qq.com/book-detail?type=1&v=...)
                        or numeric book ID (e.g. "822995")

    Returns:
        Chapter list (index, name, chapterUid, word count, VIP status),
        or error message if cookie is missing / API fails.
    """
    if not _weread_cookie():
        return (
            "[WEREAD_COOKIE_NOT_SET] 微信读书需要登录 Cookie。\n"
            "获取方式：\n"
            "1. 浏览器登录 https://weread.qq.com/\n"
            "2. F12 → Application → Cookies → 复制 wr_vid 和 wr_ssk\n"
            "3. 把 Cookie 发给智能体（格式：wr_vid=xxx; wr_ssk=yyy），"
            "它会保存到微信读书的站点档案；或在「站点档案」页配置"
        )

    # 解析 bookId：如果传入的是 URL，先从详情页 HTML 提取
    book_id = None
    book_v = None

    if url_or_book_id.startswith("http"):
        book_v = _extract_v_from_url(url_or_book_id)
        # 详情页本身是公开的，不需要 Cookie 就能拿到 bookId
        from crawagent.tools._http import http_get
        try:
            html = http_get(url_or_book_id, headers={"User-Agent": DEFAULT_UA}, timeout=20)
            book_id = _extract_book_id(html)
        except Exception as e:
            return f"[ERROR] 无法获取详情页: {e}"
    else:
        book_id = url_or_book_id.strip()

    if not book_id:
        return "[ERROR] 无法从 URL 提取 bookId，请直接传入数字 bookId（如 822995）"

    # 调用章节列表 API
    _enforce_delay()
    try:
        api_url = f"{_BASE}/web/book/info?bookId={book_id}"
        resp = requests.get(api_url, headers=_weread_headers(), timeout=20)
        data = resp.json()
    except Exception as e:
        return f"[ERROR] 章节列表 API 请求失败: {e}"

    if data.get("errCode") != 0:
        err_msg = data.get("errMsg", "未知错误")
        if "用户不存在" in err_msg:
            return (
                f"[AUTH_FAILED] Cookie 已过期或无效（errMsg: {err_msg}）。\n"
                "请重新登录 weread.qq.com 并更新 Cookie。"
            )
        return f"[API_ERROR] errCode={data.get('errCode')}, errMsg={err_msg}"

    # 解析章节列表
    info = data.get("info") or {}
    title = info.get("title", "未知书名")
    author = info.get("author", "未知作者")

    # chapterInfos 可能是 [[{...}, {...}], ...] 或 [{...}, ...]
    raw_chapters = data.get("chapterInfos") or []
    chapters = []
    for item in raw_chapters:
        if isinstance(item, list):
            chapters.extend(item)
        elif isinstance(item, dict):
            chapters.append(item)

    if not chapters:
        return f"[WARNING] 书名：{title}，但未获取到任何章节。可能该书为付费书且当前账号无权限。"

    # 格式化输出
    lines = [f"书名：{title}", f"作者：{author}", f"共 {len(chapters)} 章", ""]
    for i, ch in enumerate(chapters, 1):
        uid = ch.get("chapterUid", "?")
        name = ch.get("chapterName", f"第{i}章")
        word_count = ch.get("wordCount", 0)
        can_read = ch.get("isCanRead", True)
        vip_mark = " [VIP/付费]" if not can_read else ""
        word_str = f" {word_count}字" if word_count else ""
        lines.append(f"{i}. {name} (uid={uid}){word_str}{vip_mark}")

    # 附带 book_v 供 get_weread_chapter 使用
    if book_v:
        lines.append(f"\n[book_v={book_v}]  ← 将此值传给 get_weread_chapter 的 book_v 参数")
    else:
        lines.append(f"\n[book_v 未找到] 请从详情页 URL 的 v= 参数获取，传给 get_weread_chapter")

    return "\n".join(lines)


@tool
def get_weread_chapter(book_v: str, chapter_uid: str) -> str:
    """Get full text content of a single WeRead (微信读书) chapter.

    Requires the WeRead cookie in the weread.qq.com site profile. Use
    list_weread_chapters first to get the chapter list and chapterUid values.

    Args:
        book_v: The book's V parameter from the detail page URL
                (e.g. "a57325c05c8ed3a57224187")
        chapter_uid: The chapter's chapterUid from list_weread_chapters output

    Returns:
        Chapter title + full text content, or error message.
    """
    if not _weread_cookie():
        return "[WEREAD_COOKIE_NOT_SET] 微信读书需要登录 Cookie：把 wr_vid=xxx; wr_ssk=yyy 发给智能体保存，或在「站点档案」页为 weread.qq.com 配置。"

    _enforce_delay()
    try:
        api_url = f"{_BASE}/web/book/getBookReader"
        resp = requests.get(
            api_url,
            params={"vid": book_v, "cid": str(chapter_uid)},
            headers=_weread_headers(),
            timeout=20,
        )
        data = resp.json()
    except Exception as e:
        return f"[ERROR] 章节正文 API 请求失败: {e}"

    if data.get("errCode") != 0:
        err_msg = data.get("errMsg", "未知错误")
        if "用户不存在" in err_msg:
            return f"[AUTH_FAILED] Cookie 已过期，请重新登录并更新 Cookie。"
        return f"[API_ERROR] errCode={data.get('errCode')}, errMsg={err_msg}"

    # 解析正文
    chapter_data = data.get("data") or {}
    chapter_name = chapter_data.get("chapterName", "未知章节")
    content_html = chapter_data.get("content", "")

    if not content_html:
        return f"[WARNING] 章节「{chapter_name}」未返回正文内容。可能为 VIP 付费章节。"

    # 清洗 HTML → 纯文本
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content_html, "html.parser")

    # 优先提取 <p> 标签
    p_tags = soup.find_all("p")
    if p_tags:
        lines = [p.get_text(strip=True) for p in p_tags if p.get_text(strip=True)]
        text = "\n\n".join(lines)
    else:
        text = soup.get_text(separator="\n").strip()

    # 去除残留的 HTML 实体和多余空白
    text = re.sub(r"\n{3,}", "\n\n", text)

    if not text or len(text) < 50:
        return f"[WARNING] 章节「{chapter_name}」正文过短（{len(text)} 字），可能抓取不完整。"

    return f"# {chapter_name}\n\n{text}"
