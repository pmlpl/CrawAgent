"""Web search tool — DuckDuckGo HTML endpoint, Baidu fallback. No API key needed.

只依赖 requests + bs4（项目已有），不引入第三方搜索 SDK。
DuckDuckGo 在中国访问可能不稳，失败时自动 fallback 到百度搜索结果页。
"""
import re
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup, Tag
from langchain_core.tools import tool

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def _ddg_search(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo HTML 版搜索（无需 JS），解析 a.result__a。"""
    resp = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=_UA,
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for a in soup.select("a.result__a")[: max_results]:
        raw = a.get("href") or ""
        href = raw if isinstance(raw, str) else " ".join(raw)  # bs4 多值属性才返回 list，href 实际恒为 str
        # DDG 用 /l/?uddg=<urlencoded> 包裹真实链接，解开它
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            href = unquote(m.group(1))
        elif href.startswith("//"):
            href = "https:" + href
        if not href.startswith("http"):
            continue
        wrapper = a.find_parent(class_="result")
        snippet_el = wrapper.select_one(".result__snippet") if wrapper else None
        results.append({
            "title": a.get_text(" ", strip=True),
            "url": href,
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
        })
    return results


def _baidu_search(query: str, max_results: int) -> list[dict]:
    """百度搜索结果页解析（fallback）。链接是百度跳转链，但可直接访问。"""
    resp = requests.get(
        "https://www.baidu.com/s",
        params={"wd": query, "rn": max_results},
        headers=_UA,
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for c in soup.select("div.c-container")[: max_results]:
        a = c.find("a", href=True)
        if not isinstance(a, Tag):
            continue
        h3 = a.find("h3")
        title_el = h3 if isinstance(h3, Tag) else a
        snippet_el = c.select_one(".c-abstract") or c.select_one("[class*='content-right']")
        raw_href = a.get("href") or ""
        results.append({
            "title": title_el.get_text(" ", strip=True),
            "url": raw_href if isinstance(raw_href, str) else " ".join(raw_href),
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
        })
    return results


@tool
def web_search(query: str, max_results: int = 8) -> str:
    """联网搜索主题相关的第三方网站（例如某部剧/电影的在线站）。

    需要在公网上找网站时**第一个调用它** —— 例如搜第三方影视聚合站看哪个能看。
    返回带编号的搜索结果列表（标题、URL、摘要）。之后你再对最有希望的几条
    调 browse_and_crawl 或 probe_video_player 逐个验证。

    参数：
        query: 搜索关键词；中文剧/中文内容请用中文搜，例 "师兄太稳健 在线观看"。
        max_results: 最多返回多少条（默认 8，上限 15）。

    返回：
        带编号的 Markdown 列表；若啥也没搜到返回 "NO_RESULTS"。
    """
    max_results = min(max(max_results, 1), 15)
    errors = []

    for backend, fn in (("duckduckgo", _ddg_search), ("baidu", _baidu_search)):
        try:
            results = fn(query, max_results)
            if results:
                lines = [f"Search backend: {backend}, {len(results)} results for '{query}':"]
                for i, r in enumerate(results, 1):
                    lines.append(f"{i}. [{r['title']}]({r['url']})")
                    if r["snippet"]:
                        lines.append(f"   {r['snippet'][:150]}")
                return "\n".join(lines)
            errors.append(f"{backend}: no results parsed")
        except Exception as e:
            errors.append(f"{backend}: {e}")

    return "Search failed. " + "; ".join(errors)
