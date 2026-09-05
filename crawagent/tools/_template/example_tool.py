"""CrawAgent 工具插件模板 — 复制这个文件，改成你自己的爬虫。

3 步添加新工具：
  1. 把本文件复制到 crawagent/tools/my_plugin.py（或任何位置）
  2. 改 @register_tool 的 category/deps，写函数体和 docstring
  3. 重启后端 — discover_tools() 会自动扫到，进 system prompt 索引

  不需要改 agent.py！不需要改 registry.py！不需要改 system.md！

分类选一个：
  crawl   — HTTP/Playwright 抓取
  extract — HTML/文本解析提取
  save    — 文件/数据库持久化
  site    — 站点专用（social/wallpaper/weread/video 等）
  script  — 脚本执行
  mcp     — MCP 协议接入（通常动态装配，不用这个）
  skill   — 技能扩展（SKILL.md 驱动）

deps 填运行时依赖：requests / playwright / bs4 / yt-dlp 等。

旧版兼容：如果你不想用 @register_tool，直接用 langchain 的 @tool 装饰器也行 —
discover_tools() 会自动扫描到它，只是 category/deps 会靠名字猜（不如手写准）。
"""
from __future__ import annotations

import requests  # noqa: F401 — 示例依赖

from crawagent.tools.registry import register_tool


@register_tool(category="site", deps=["requests"])
def fetch_rss_feed(url: str, max_items: int = 10) -> str:
    """抓取 RSS feed，返回最新的 N 条标题 + 链接。

    适合播客、博客、新闻站点的内容发现。feedburner / feedly / 自建 feed 都行。

    Args:
        url: RSS feed URL, e.g. "https://example.com/rss" or "https://hnrss.org/frontpage"
        max_items: 最多返回几条（默认 10，最大 30）
    """
    # 伪代码 — 实际写你的逻辑
    import xml.etree.ElementTree as ET

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
        title = (item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or "(untitled)").strip()
        link = (item.findtext("link") or "").strip()
        lines.append(f"  [{i}] {title}\n      {link}")

    return "\n".join(lines)
