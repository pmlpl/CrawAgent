"""共享翻页模块 — 静态列表页的通用翻页（ADR-0002：v1 只做静态，JS 分页出局）。

两种策略按序尝试：
1. 下一页链接追踪：rel="next" / class 含 next / 「下一页」类文案（覆盖 /page/2/ 路径式分页）
2. URL 参数猜测：page / p / pageNum，首个翻出新条目的参数被记住，后续页沿用

终止条件：一页内找不到任何新条目 URL、或翻满 max_pages。
所有抓取走 _enforce_delay() 节流（request_delay 是全项目唯一限流手段）。
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from crawagent.tools.crawl_tool import DEFAULT_UA, _enforce_delay

HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_PAGE_PARAMS = ("page", "p", "pageNum")

_NEXT_TEXT_RE = re.compile(r"^(?:下一页|下页|后一页|下一頁|next(?:\s*page)?|›|»|>)$", re.I)
_NEXT_CLASS_RE = re.compile(r"(?:^|[-_])next(?:[-_]|$)|next[-_]?page|page[-_]?next", re.I)


def http_get(url: str, timeout: int = 20) -> str:
    """抓取页面 HTML（节流 + 固定 UA）。wallpaper_tool 与翻页流程共用。

    实现委托 ``crawagent.tools._http.http_get``（变更 020 统一封装），保持外部签名不变。
    """
    from crawagent.tools._http import http_get as _shared_http_get
    return _shared_http_get(url, headers=HEADERS, timeout=timeout)


def _with_param(page_url: str, param: str, value: int) -> str:
    """替换/追加 URL 里的分页参数，保留其余 query。"""
    parts = urlparse(page_url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param]
    q.append((param, str(value)))
    return urlunparse(parts._replace(query=urlencode(q)))


def _find_next_link(html: str, page_url: str) -> str:
    """从页面找「下一页」链接，找不到返回空串。"""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return ""
    for a in soup.find_all("a", href=True):
        href = a["href"] or ""
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(page_url, href).split("#")[0]
        if not full.startswith("http") or full == page_url:
            continue
        rel = a.get("rel")
        rel_text = " ".join(rel) if isinstance(rel, list) else str(rel or "")
        cls = " ".join(a.get("class") or [])
        text = (a.get_text(strip=True) or "").strip()
        if (
            "next" in rel_text.lower().split()
            or _NEXT_CLASS_RE.search(cls)
            or _NEXT_TEXT_RE.match(text)
            or _NEXT_TEXT_RE.match(text.strip(" >»›"))
        ):
            return full
    return ""


def walk_pages(
    start_url: str,
    *,
    extract_urls: Callable[[str, str], list[str]],
    max_pages: int = 20,
    fetch: Callable[[str], str] | None = None,
) -> Iterator[tuple[str, str, list[str]]]:
    """逐页翻动静态列表，产出 (page_url, html, 本页新条目 URL 列表)。

    extract_urls(html, page_url) 返回该页全部条目 URL（跨页去重与终止判断靠它）。
    终止：本页无新条目 / 翻满 max_pages / 抓取失败。fetch 可注入测试替身。
    """
    get = fetch or http_get
    visited: set[str] = set()
    seen: set[str] = set()
    param_tpl: str | None = None   # 命中的分页参数名，命中后沿用
    current = start_url
    page_no = 1
    pending: tuple[str, str] | None = None  # 参数探测时已抓到的 (url, html)，下轮直接用不重抓

    while current and page_no <= max_pages:
        if pending:
            current, html = pending
            pending = None
        else:
            try:
                html = get(current)
            except Exception:
                break
        visited.add(current)
        fresh = [u for u in extract_urls(html, current) if u not in seen]
        seen.update(fresh)
        yield current, html, fresh
        if not fresh:
            break
        page_no += 1

        # ① 下一页链接
        nxt = _find_next_link(html, current)
        if nxt in visited:
            nxt = ""
        # ② 参数猜测：先试已命中的参数，再逐个试候选；翻出新条目才算命中
        if not nxt:
            for name in ((param_tpl,) if param_tpl else _PAGE_PARAMS):
                cand = _with_param(current, name, page_no)
                if cand in visited:
                    continue
                try:
                    h2 = get(cand)
                except Exception:
                    continue
                if any(u not in seen for u in extract_urls(h2, cand)):
                    param_tpl = name
                    nxt = cand
                    pending = (cand, h2)
                    break
        current = nxt
