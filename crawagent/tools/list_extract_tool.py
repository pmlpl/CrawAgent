"""列表页抽取工具 — 混合策略：正则优先，LLM 兜底，DOM 深度作为最终兜底。

硬约束：
- List extraction must use hybrid strategy (regex first, LLM fallback, DOM depth extraction as ultimate兜底)
- Navigation/footer links must be filtered from list extraction results
- If basic crawling extracts <5 items, automatically retry with browser-based crawling
  （此条由 Agent system prompt 协调：extract_list <5 条 → 切 browse_and_crawl 重抓）

三阶段设计动机：
- 正则：最快，对结构规范的列表页（<a href> 重复模式）一次命中，无 LLM 成本
- LLM：正则不够时让语义理解兜底，能处理不规则结构，但有 API 成本
- DOM 深度：LLM 也失败时的最终兜底，用 BeautifulSoup 找最深重复结构容器
"""
import re
import json
from collections import Counter, defaultdict
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from langchain_core.tools import tool


# 导航/页脚常见关键词（统一小写），用于过滤明显非内容链接
_NAV_FOOTER_KEYWORDS = {
    # 中文
    "首页", "主页", "登录", "注册", "登入", "登出", "关于我们", "联系我们",
    "帮助", "反馈", "服务条款", "隐私", "版权", "友情链接", "网站地图",
    "上一页", "下一页", "上一章", "下一章", "返回", "更多",
    # 英文
    "home", "login", "sign in", "sign up", "register", "logout", "log in",
    "about", "contact", "help", "feedback", "privacy", "terms",
    "sitemap", "previous", "next", "back", "more", "read more",
}

# 明显的导航 URL 路径模式
_NAV_URL_PATTERN = re.compile(
    r"/(login|register|signup|signin|logout|about|contact|help|privacy|terms|sitemap)([/\?#]|$)",
    re.IGNORECASE,
)


def _is_nav_footer_link(text: str, url: str) -> bool:
    """判断链接是否属于导航/页脚（按硬约束必须过滤）"""
    t = (text or "").strip().lower()
    if t and any(kw in t for kw in _NAV_FOOTER_KEYWORDS):
        return True
    url_lower = (url or "").lower()
    if _NAV_URL_PATTERN.search(url_lower):
        return True
    return False


def _normalize_url(href: str, base_url: str) -> str:
    """规范化 URL：补全 base、去掉 fragment"""
    if not href:
        return ""
    if href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    full = urljoin(base_url, href) if base_url else href
    return full.split("#")[0]


def _remove_noise(soup: BeautifulSoup) -> None:
    """移除明显的导航/页脚/script/style 等噪音标签"""
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    # nav/footer/header/aside 通常含导航链接，硬约束要求过滤
    for tag in soup.find_all(["nav", "footer", "header", "aside"]):
        tag.decompose()


# =============================================================
# 阶段 1：正则优先
# =============================================================

def _url_group_key(url: str) -> str:
    """生成 URL 分组键：去掉末尾数字 ID/日期，暴露共同前缀。

    同一个列表页的详情链接通常共享前缀，末尾是各异的 ID。
    例：/article/123.html /article/456.html → /article/
    """
    u = url.split("?")[0].split("#")[0]
    # 去掉协议和域名，只保留路径做分组（同站点列表项路径同源）
    if "://" in u:
        u = "/" + "/".join(u.split("/", 3)[3:])
    # 末尾数字段 → 归一
    u = re.sub(r"/\d+[^/]*$", "/", u)
    u = re.sub(r"_\d+\.html?$", ".html", u, flags=re.IGNORECASE)
    u = re.sub(r"/\d{4,}/", "/", u)  # 长数字 ID 路径段
    return u


def _stage1_regex(html: str, base_url: str = "") -> list[dict]:
    """阶段1：正则优先。识别重复的 <a href> 模式。

    策略：
    - 正则抓所有 <a href="...">文本</a>
    - 按 URL 路径前缀分组（同列表项通常 URL 结构相同）
    - 取最大分组作为候选，过滤导航/页脚
    """
    pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    raw_links = []
    for m in pattern.finditer(html):
        href = m.group(1).strip()
        # 去掉 a 标签内的子标签，只留文本
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not href or not text:
            continue
        full = _normalize_url(href, base_url)
        if not full:
            continue
        raw_links.append((full, text))

    if not raw_links:
        return []

    # 按 URL 前缀分组
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for full_url, text in raw_links:
        groups[_url_group_key(full_url)].append((full_url, text))

    # 取最大组
    best_group = max(groups.values(), key=len, default=[])

    # 至少 3 条才算列表
    if len(best_group) < 3:
        return []

    items: list[dict] = []
    seen: set[str] = set()
    for full_url, text in best_group:
        if full_url in seen:
            continue
        if _is_nav_footer_link(text, full_url):
            continue
        seen.add(full_url)
        items.append({"title": text, "url": full_url})
    return items


# =============================================================
# 阶段 2：LLM 兜底
# =============================================================

_LLM_PROMPT_TEMPLATE = """你是一个列表页抽取专家。从以下 HTML 中，抽取所有列表条目组成 JSON 数组返回。

规则：
- 每条格式：{{"title": "标题文字", "url": "完整跳转链接"}}
- URL 必须是绝对地址（以 base_url={base_url} 为参照做相对路径解析）
- 跳过：全站导航、页脚、登录/注册、分页按钮、"展开全文"、"阅读更多" 类文字链接
- 只返回纯 JSON 数组，不要解释文字、不要 ```json 包裹
- 如果这页面明显不是列表页，返回 []

HTML:
{snippet}

JSON 数组:"""


def _stage2_llm(html: str, base_url: str = "") -> list[dict]:
    """阶段2：LLM 兜底。正则不足时让 LLM 从 HTML 片段抽取。"""
    try:
        from crawagent.llm.model import get_llm
        from langchain_core.messages import HumanMessage
    except ImportError:
        return []

    # 截取主体 HTML 给 LLM（避免 token 爆炸）
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.IGNORECASE | re.DOTALL)
    snippet = cleaned[:8000]

    prompt = _LLM_PROMPT_TEMPLATE.format(base_url=base_url or "(none)", snippet=snippet)
    try:
        llm = get_llm()
        resp = llm.invoke([HumanMessage(content=prompt)])
        raw = resp.content
        content = raw.strip() if isinstance(raw, str) else str(raw)
        # 提取 JSON 数组（LLM 可能包裹在 markdown 里）
        m = re.search(r"\[.*\]", content, re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(0))
        items: list[dict] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            url_val = str(item.get("url", "")).strip()
            if not title or not url_val:
                continue
            full_url = _normalize_url(url_val, base_url) if not url_val.startswith("http") else url_val
            if not full_url or full_url in seen:
                continue
            if _is_nav_footer_link(title, full_url):
                continue
            seen.add(full_url)
            items.append({"title": title, "url": full_url})
        return items
    except Exception:
        return []


# =============================================================
# 阶段 3：DOM 深度兜底
# =============================================================

def _stage3_dom_depth(html: str, base_url: str = "") -> list[dict]:
    """阶段3：DOM 深度兜底。找出最深层的重复结构容器。

    策略：
    - 遍历所有容器（div/ul/ol/section/article/tbody）
    - 按"直接子元素签名（标签名+class）"统计重复次数
    - 评分 = 重复次数 × 容器深度，取最高分容器
    - 从重复子元素里提取 (title, url) 和额外字段（图片等）
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    _remove_noise(soup)

    containers = soup.find_all(["div", "ul", "ol", "section", "article", "tbody"])

    best_items: list[dict] = []
    best_score = 0

    def signature(tag: Tag) -> str:
        cls = " ".join(sorted(tag.get("class", [])))
        return f"{tag.name}|{cls}"

    def container_depth(tag: Tag) -> int:
        d = 0
        node = tag
        while node.parent:
            d += 1
            node = node.parent
        return d

    for container in containers:
        children = [c for c in container.children if isinstance(c, Tag)]
        if len(children) < 3:
            continue

        sigs = [signature(c) for c in children]
        sig_counter = Counter(sigs)
        most_common_sig, most_common_count = sig_counter.most_common(1)[0]
        if most_common_count < 3:
            continue

        depth = container_depth(container)
        score = most_common_count * depth
        if score <= best_score:
            continue

        items: list[dict] = []
        seen: set[str] = set()
        for child in children:
            if signature(child) != most_common_sig:
                continue
            a = child.find("a", href=True)
            if not isinstance(a, Tag):
                continue
            raw_href = a.get("href") or ""
            full_url = _normalize_url(raw_href if isinstance(raw_href, str) else " ".join(raw_href), base_url)
            if not full_url:
                continue
            title = a.get_text(strip=True)
            if not title:
                # 退化：取子元素里最长的文本
                texts = [t.strip() for t in child.find_all(string=True) if t.strip()]
                title = max(texts, key=len, default="")
            if not title:
                continue
            if full_url in seen or _is_nav_footer_link(title, full_url):
                continue
            seen.add(full_url)
            item = {"title": title, "url": full_url}
            # 额外字段：图片（save_executor 硬约束要求识别图片字段）
            img = child.find("img")
            raw_src = img.get("src") if isinstance(img, Tag) else None
            if raw_src:
                src = raw_src if isinstance(raw_src, str) else " ".join(raw_src)
                item["image"] = _normalize_url(src, base_url)
            items.append(item)

        if len(items) > len(best_items):
            best_items = items
            best_score = score

    return best_items


# =============================================================
# 工具入口
# =============================================================

@tool
def extract_list(html: str, url: str = "") -> str:
    """从列表页/索引页提取列表条目（标题+URL 配对）。

    三段式混合策略（硬约束）：
    1. 正则优先 — 快速扫描重复 <a href> 结构，按 URL 前缀自动分组
    2. LLM 兜底 — 正则结果少于 5 条时，让 LLM 从 HTML 中语义抽取
    3. DOM 深度兜底 — 最终保险，找出最深层的重复子结构容器

    自动过滤：全站导航/页脚/登录注册/分页按钮 等非内容链接（硬约束）。

    参数：
        html: 列表页的 HTML 原文
        url: 可选基础 URL，用于补全相对链接

    返回：
        格式化列表 + 策略标签，例如：
        "列表（12 条，策略=正则）:
        1. [标题1](url1)
        2. [标题2](url2)
        ..."
        失败时："列表抽取失败：原因"
    """
    if not html or len(html) < 100:
        return "List extraction failed: HTML too short"

    # Stage 1: 正则优先
    items = _stage1_regex(html, url)
    strategy = "regex"

    # Stage 2: LLM 兜底（正则 <5 条）
    if len(items) < 5:
        llm_items = _stage2_llm(html, url)
        if len(llm_items) > len(items):
            items = llm_items
            strategy = "llm"

    # Stage 3: DOM 深度兜底（仍 <5 条）
    if len(items) < 5:
        dom_items = _stage3_dom_depth(html, url)
        if len(dom_items) > len(items):
            items = dom_items
            strategy = "dom_depth"

    if not items:
        return "List extraction failed: no items found (tried regex, LLM, DOM depth)"

    lines = [f"List ({len(items)} items, strategy={strategy}):"]
    for i, item in enumerate(items, 1):
        title = item.get("title", "")
        url_val = item.get("url", "")
        extra = ""
        if item.get("image"):
            img_short = item["image"][:60] + ("..." if len(item["image"]) > 60 else "")
            extra = f" [img: {img_short}]"
        lines.append(f"{i}. [{title}]({url_val}){extra}")

    return "\n".join(lines)
