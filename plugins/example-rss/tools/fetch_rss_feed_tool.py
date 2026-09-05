"""CrawAgent 插件工具模板 — tools/ 目录下每个 .py 文件的模块级 BaseTool 都会被自动发现。

编写约定：
  - 用 langchain 的 @tool 或 crawagent 的 @register_tool 装饰模块级函数
  - docstring 就是给 LLM 看的工具说明，写清楚参数与返回
  - 不要在 import 时做网络请求等重活（发现机制会 import 本模块）
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import requests

from crawagent.tools.registry import register_tool


@register_tool(category="extract", deps=["requests"])
def fetch_rss_feed(url: str, max_items: int = 10) -> str:
    """抓取 RSS feed，返回最新的 N 条标题 + 链接。

    适合播客、博客、新闻站点的内容发现。feedburner / feedly / 自建 feed 都行。

    Args:
        url: RSS feed URL, e.g. "https://example.com/rss" or "https://hnrss.org/frontpage"
        max_items: 最多返回几条（默认 10，最大 30）
    """
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "CrawAgent/1.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        return f"[ERROR] fetch_rss_feed: {e}"

    # RSS 2.0: <item>; Atom: <entry>
    items = root.findall(".//item") or root.findall(".//entry")
    max_items = min(max_items, 30)

    lines = [f"RSS FEED: {url} ({len(items)} items total, showing top {max_items})"]
    for i, item in enumerate(items[:max_items], 1):
        title = (item.findtext("title")
                 or item.findtext("{http://www.w3.org/2005/Atom}title")
                 or "(untitled)").strip()
        link = (item.findtext("link") or "").strip()
        lines.append(f"  [{i}] {title}\n      {link}")

    return "\n".join(lines)
