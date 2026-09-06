"""通用壁纸/图片类网站提取工具

针对「壁纸/图片类网站」这一类反爬机制的通用工具：
- 卡片用 CSS background-image 渲染（非 <img> 标签），extract_list 抽不到
- 列表页 → 详情页二次跳转才能拿完整信息
- 图片防盗链（需 referer）
- 分页加载（?page=N / &page=N / ?p=N）
- 静态/动态壁纸混合

不绑定任何特定网站域名，通过通用 DOM 解析 + 启发式规则适配任意壁纸站。
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from langchain_core.tools import tool

from crawagent.tools.pagination import http_get, walk_pages

# 排除这些路径模式（导航/功能链接，不是详情页）
_EXCLUDE_PATH_RE = re.compile(
    r"/(user|avatar|logo|icon|category|tag|search|login|register|"
    r"about|help|contact|api|static|assets?|css|js|font|comment|share)/",
    re.I,
)
# 详情页路径特征
_INCLUDE_PATH_RE = re.compile(
    r"/(detail|view|wallpaper|look|item|post|show|read|pic|photo|image|w)/",
    re.I,
)


def _extract_card_links(html: str, base_url: str) -> list[str]:
    """从列表页 HTML 提取详情页链接（通用启发式）。

    策略：找所有 <a href> 链接，过滤导航/分页，保留含数字 ID 或详情页路径特征的。
    """
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href.startswith("#") or href.startswith("javascript"):
            continue
        full_url = urljoin(base_url, href)
        path = urlparse(full_url).path.lower()

        if _EXCLUDE_PATH_RE.search(path):
            continue
        if re.search(r"[?&]page=\d+", full_url, re.I):
            continue

        # 保留：含 4 位以上数字 ID 或匹配详情页路径模式
        is_detail = bool(re.search(r"/\d{4,}", path)) or bool(_INCLUDE_PATH_RE.search(path))
        if is_detail and full_url not in seen:
            seen.add(full_url)
            links.append(full_url)

    return links


def _auto_paginate(base_url: str, target_count: int) -> tuple[list[str], int]:
    """自动翻页收集详情页链接（翻页逻辑在 pagination.walk_pages：下一页链接 + page/p/pageNum）。

    Returns:
        (detail_urls, pages_scanned)
    """
    all_links: list[str] = []
    pages = 0
    for _page_url, _html, fresh in walk_pages(
        base_url, max_pages=20, extract_urls=_extract_card_links,
    ):
        pages += 1
        all_links.extend(fresh)
        if len(all_links) >= target_count:
            break
    return all_links, pages


def _parse_title(title_str: str) -> tuple[str, str]:
    """从页面 <title> 解析分辨率前缀和壁纸名。

    Returns:
        (resolution_prefix, name)
    """
    if not title_str:
        return "", ""
    t = title_str.strip()
    # 去掉常见后缀
    for suffix in ["「哲风壁纸」", " - 壁纸", " | 壁纸", "_壁纸"]:
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    # 分辨率前缀（4K/5K/2K/8K/3K 等）
    m = re.match(r"^(\d+[kK])\s*", t)
    res = m.group(1).upper() if m else ""
    name_part = t[m.end():] if m else t
    # 去掉分隔符后的描述部分
    name_part = re.split(r"[｜\|\\/\-\~【\[\(]", name_part, maxsplit=1)[0].strip()
    return res, name_part


def _extract_detail_info(html: str, detail_url: str) -> dict:
    """从详情页 HTML 提取完整信息（通用方法）。

    Returns:
        {
            "title": str,
            "resolution": str,
            "name": str,
            "is_dynamic": bool,
            "images": list[str],       # 所有图片 URL（去重）
            "primary_image": str,      # 主图（出现次数最多）
            "videos": list[str],       # 视频 URL
        }
    """
    soup = BeautifulSoup(html, "lxml")

    # title
    title_str = ""
    if soup.title and soup.title.string:
        title_str = soup.title.string.strip()
    res, name = _parse_title(title_str)

    # 动静判断
    is_dynamic = bool(soup.find("video"))
    if not is_dynamic:
        is_dynamic = bool(re.search(r"动态|dynamic|背景视频|animated|live", title_str, re.I))

    # 提取所有图片 URL
    img_urls: list[str] = []
    base = detail_url

    # <img src="...">
    for img in soup.find_all("img", src=True):
        u = urljoin(base, img["src"])
        if u.startswith("http"):
            img_urls.append(u)
    # <img data-src="..."> (lazy load)
    for img in soup.find_all("img", attrs={"data-src": True}):
        u = urljoin(base, img["data-src"])
        if u.startswith("http"):
            img_urls.append(u)
    # <img data-original="..."> (lazy load)
    for img in soup.find_all("img", attrs={"data-original": True}):
        u = urljoin(base, img["data-original"])
        if u.startswith("http"):
            img_urls.append(u)
    # background-image: url(...) — 只从含 background 的 style 里提取，跳过 cursor 等
    for el in soup.find_all(style=True):
        style = el.get("style", "")
        if "background" not in style.lower():
            continue
        for m in re.finditer(r"url\(['\"]?([^'\")]+)['\"]?\)", style):
            u = urljoin(base, m.group(1))
            if u.startswith("http"):
                img_urls.append(u)
    # 正则兜底：扫描 HTML 中所有含图片扩展名的 URL
    for m in re.finditer(r"https?://[^\s\"'<>]+\.(?:jpg|jpeg|png|webp|gif|bmp)", html, re.I):
        img_urls.append(m.group(0))

    # 视频 URL
    video_urls: list[str] = []
    for v in soup.find_all("video", src=True):
        u = urljoin(base, v["src"])
        if u.startswith("http"):
            video_urls.append(u)
    for v in soup.find_all("source", src=True):
        u = urljoin(base, v["src"])
        if u.startswith("http"):
            video_urls.append(u)
    # 正则兜底
    for m in re.finditer(r"https?://[^\s\"'<>]+\.(?:mp4|webm|mov|m4v)", html, re.I):
        video_urls.append(m.group(0))

    # 选主图：出现次数最多（排除 UI 元素：logo/icon/cursor/光标等）
    def _is_noise(u: str) -> bool:
        return bool(re.search(
            r"(logo|icon|favicon|avatar|sprite|placeholder|blank|touch|other-img|cursor|arrow|btn|button|loading|spinner)",
            u, re.I,
        ))

    filtered = [u for u in img_urls if not _is_noise(u)]
    if not filtered:
        filtered = img_urls
    primary_image = ""
    if filtered:
        counts = Counter(filtered)
        primary_image = counts.most_common(1)[0][0]

    return {
        "title": title_str,
        "resolution": res,
        "name": name,
        "is_dynamic": is_dynamic,
        "images": list(dict.fromkeys(filtered)),  # 去重保持顺序
        "primary_image": primary_image,
        "videos": list(dict.fromkeys(video_urls)),
    }


@tool
def extract_wallpaper_list(url: str, limit: int = 10, exclude_dynamic: bool = False) -> str:
    """从**任何壁纸/图片站**抽取壁纸条目列表。

    适配场景：壁纸/图片站通常用 CSS background-image（而非 <img>）渲染卡片、
    必须进入详情页才能看到原图、还有分页。壁纸站用它而不是 extract_list ——
    因为 extract_list 只处理 <a> 文字链接，会漏掉背景图卡片这类站点。

    每条自动识别：静态图 STATIC / 动态视频 DYNAMIC；自动翻页到凑齐数量为止。

    参数：
        url: 列表页 URL（首页、分类页、或壁纸站搜索结果页）。
        limit: 返回条目数（默认 10）。
        exclude_dynamic: 为 True 时，跳过动态/视频壁纸，只返回静态图。

    返回：
        格式化列表，每条含：类型（静/动）、标题、分辨率、详情页 URL、
        缩略图 URL、其他图/视频 URL。
    """
    # 1. 自动翻页收集详情页链接
    target = limit * 4 if exclude_dynamic else limit * 2
    try:
        detail_urls, pages_scanned = _auto_paginate(url, target)
    except Exception as e:
        return f"extract_wallpaper_list failed: {e}"

    if not detail_urls:
        return f"extract_wallpaper_list failed: no detail-page links found in {url}"

    # 2. 拉取每个详情页
    entries: list[dict] = []
    for detail_url in detail_urls:
        try:
            html = http_get(detail_url)
        except Exception as e:
            entries.append({"detail": detail_url, "error": str(e)})
            continue

        info = _extract_detail_info(html, detail_url)
        if exclude_dynamic and info["is_dynamic"]:
            continue

        entries.append({
            "detail": detail_url,
            "type": "DYNAMIC" if info["is_dynamic"] else "STATIC",
            "res": info["resolution"],
            "name": info["name"] or info["title"][:50],
            "thumb": info["primary_image"],
            "images": info["images"][:5],
            "videos": info["videos"][:3],
        })

        if len(entries) >= limit:
            break

    # 3. 格式化输出
    out = entries[:limit]
    lines = [f"Wallpaper list ({len(out)} entries, source={url}, pages_scanned={pages_scanned}):"]
    for idx, e in enumerate(out, 1):
        if "error" in e:
            lines.append(f"{idx}. [fetch error: {e['error']}] {e['detail']}")
            continue
        res_label = f" {e['res']}" if e["res"] else ""
        lines.append(f"{idx}. [{e['type']}{res_label}] {e['name']}")
        lines.append(f"     detail: {e['detail']}")
        if e.get("thumb"):
            lines.append(f"     thumb:  {e['thumb']}")
        if e.get("images") and len(e["images"]) > 1:
            others = e["images"][1:4]
            lines.append(f"     images: {', '.join(others)}")
        if e.get("videos"):
            lines.append(f"     videos: {', '.join(e['videos'])}")

    return "\n".join(lines)


@tool
def wallpaper_detail(url: str) -> str:
    """从**任何壁纸/图片站**抓取单条壁纸的详情信息。

    参数：
        url: 单条壁纸的详情页 URL。

    返回：
        格式化详情：类型（静态/动态）、标题、分辨率、所有图片 URL、视频 URL。
    """
    try:
        html = http_get(url)
    except Exception as e:
        return f"wallpaper_detail failed: {e}"

    info = _extract_detail_info(html, url)
    kind_label = "DYNAMIC" if info["is_dynamic"] else "STATIC"

    lines = [
        f"Wallpaper detail ({url})",
        f"  Type:       {kind_label}",
        f"  Title:      {info['title']}",
        f"  Name:       {info['name']}",
        f"  Resolution: {info['resolution'] or 'unknown'}",
        f"  Primary:    {info['primary_image']}",
    ]
    if info["images"]:
        lines.append(f"  All images ({len(info['images'])}):")
        for u in info["images"]:
            lines.append(f"    - {u}")
    if info["videos"]:
        lines.append(f"  Videos ({len(info['videos'])}):")
        for u in info["videos"]:
            lines.append(f"    - {u}")
    return "\n".join(lines)
