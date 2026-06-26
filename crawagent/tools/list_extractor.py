"""列表页结构化提取工具

功能：
- 自动识别列表结构
- 提取标题、链接、热度、排名、时间、作者等字段
- 支持分页爬取
- 批量保存到数据库
"""
from __future__ import annotations

import re
import json
from typing import Any
from urllib.parse import urljoin, urlparse
from dataclasses import dataclass, field

from ..config.settings import get_logger
logger = get_logger(__name__)


@dataclass
class ListItem:
    """列表项"""
    rank: str = ""
    title: str = ""
    url: str = ""
    hot: str = ""
    author: str = ""
    category: str = ""
    published_date: str = ""
    description: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "rank": self.rank,
            "title": self.title,
            "url": self.url,
            "hot": self.hot,
            "author": self.author,
            "category": self.category,
            "published_date": self.published_date,
            "description": self.description,
        }
        data.update(self.extra)
        return data

    def to_db_record(self, source: str = "") -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.description,
            "source": source,
            "author": self.author,
            "published_date": self.published_date,
            "category": self.category,
            "rank": self.rank,
            "hot": self.hot,
        }


class ListExtractor:
    """列表页结构化提取器"""

    # 常见的列表选择器模式
    LIST_SELECTORS = [
        # 热榜类
        r'.*HotItem.*',
        r'.*hot-item.*',
        r'.*rank-item.*',
        r'.*list-item.*',
        r'.*item-wrap.*',
        r'.*content-item.*',
        r'.*news-item.*',
        r'.*post-item.*',
        r'.*topic-item.*',
        # 通用
        r'li',
        r'div[^>]*class="[^"]*item[^"]*"',
        r'div[^>]*class="[^"]*card[^"]*"',
    ]

    # 字段提取正则
    TITLE_PATTERNS = [
        r'<a[^>]*>([^<]{5,200})</a>',
        r'title="([^"]+)"',
        r'<h[1-6][^>]*>([^<]+)</h[1-6]>',
    ]

    URL_PATTERNS = [
        r'href="([^"]+)"',
        r'data-url="([^"]+)"',
        r'data-href="([^"]+)"',
    ]

    HOT_PATTERNS = [
        r'(\d+\.?\d*\s*[万wW亿kK]?\s*[度热浏览量点击]?)',
        r'hot[^>]*>\s*([^<]+)',
        r'heat[^>]*>\s*([^<]+)',
        r'热度[：:]\s*([^<\s]+)',
    ]

    RANK_PATTERNS = [
        r'class="[^"]*rank[^"]*"[^>]*>\s*(\d+)',
        r'<span[^>]*>\s*(\d{1,3})\s*</span>\s*<a',
        r'^(\d{1,3})[\.\、]',
    ]

    AUTHOR_PATTERNS = [
        r'author[^>]*>\s*([^<]+)',
        r'作者[：:]\s*([^<\s]+)',
        r'user[^>]*>\s*([^<]+)',
    ]

    DATE_PATTERNS = [
        r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',
        r'\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}',
        r'\d+\s*(分钟|小时|天|月|年)前',
        r'(\d+)小时前',
        r'(\d+)天前',
    ]

    def __init__(self, base_url: str = ""):
        self.base_url = base_url

    def extract_from_html(self, html: str, max_items: int = 50) -> list[ListItem]:
        """
        从 HTML 中提取列表项

        Args:
            html: 页面 HTML
            max_items: 最大提取数量

        Returns:
            ListItem 列表
        """
        items = []

        # 方法1：尝试用正则提取常见的列表结构
        # 提取所有 <li> 标签中的内容
        li_pattern = re.compile(r'<li[^>]*>(.*?)</li>', re.DOTALL | re.IGNORECASE)
        li_matches = li_pattern.findall(html)

        if li_matches and len(li_matches) > 3:
            for li_html in li_matches[:max_items]:
                item = self._parse_item(li_html)
                if item and item.title:
                    items.append(item)

        # 方法2：如果 li 提取效果不好，尝试用 a 标签批量提取
        if len(items) < 3:
            items = self._extract_from_links(html, max_items)

        # 去重（按 URL）
        seen_urls = set()
        unique_items = []
        for item in items:
            if item.url and item.url not in seen_urls:
                seen_urls.add(item.url)
                unique_items.append(item)
            elif not item.url and item.title:
                # 没有URL的，按标题去重
                key = item.title
                if key not in seen_urls:
                    seen_urls.add(key)
                    unique_items.append(item)

        return unique_items[:max_items]

    def _parse_item(self, item_html: str) -> ListItem:
        """解析单个列表项"""
        item = ListItem()

        # 提取标题
        for pattern in self.TITLE_PATTERNS:
            match = re.search(pattern, item_html, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                if len(title) > 3:
                    item.title = title
                    break

        # 提取 URL
        for pattern in self.URL_PATTERNS:
            match = re.search(pattern, item_html, re.IGNORECASE)
            if match:
                url = match.group(1).strip()
                if url and not url.startswith('#') and not url.startswith('javascript:'):
                    item.url = self._resolve_url(url)
                    break

        # 提取排名
        for pattern in self.RANK_PATTERNS:
            match = re.search(pattern, item_html, re.IGNORECASE)
            if match:
                item.rank = match.group(1).strip()
                break

        # 提取热度
        for pattern in self.HOT_PATTERNS:
            match = re.search(pattern, item_html, re.IGNORECASE)
            if match:
                hot = match.group(1).strip()
                if hot and len(hot) < 20:
                    item.hot = hot
                    break

        # 提取作者
        for pattern in self.AUTHOR_PATTERNS:
            match = re.search(pattern, item_html, re.IGNORECASE)
            if match:
                item.author = match.group(1).strip()
                break

        # 提取日期
        for pattern in self.DATE_PATTERNS:
            match = re.search(pattern, item_html)
            if match:
                item.published_date = match.group(0).strip()
                break

        return item

    def _extract_from_links(self, html: str, max_items: int) -> list[ListItem]:
        """从链接中批量提取列表项"""
        items = []

        # 提取所有有意义的链接
        a_pattern = re.compile(
            r'<a[^>]*href="([^"]+)"[^>]*>([^<]{5,200})</a>',
            re.DOTALL | re.IGNORECASE
        )

        matches = a_pattern.findall(html)

        seen_urls = set()
        for url, title in matches:
            url = url.strip()
            title = title.strip()

            # 过滤无效链接
            if (not url or url.startswith('#') or url.startswith('javascript:')
                    or not title or len(title) < 5):
                continue

            # 过滤导航链接
            skip_keywords = ['登录', '注册', '首页', '关于', '联系', '帮助', '设置',
                           '更多', '查看全部', '下一页', '上一页', '首页', '末页',
                           '返回', '退出', '搜索', '提交', '下载']
            if any(kw in title for kw in skip_keywords):
                continue

            # 去重
            if url in seen_urls:
                continue
            seen_urls.add(url)

            item = ListItem(
                title=title,
                url=self._resolve_url(url),
            )
            items.append(item)

            if len(items) >= max_items:
                break

        return items

    def _resolve_url(self, url: str) -> str:
        """解析相对 URL 为绝对 URL"""
        if not self.base_url:
            return url

        try:
            return urljoin(self.base_url, url)
        except:
            return url

    def extract_with_llm(self, html: str, llm_client) -> list[ListItem]:
        """
        使用 LLM 提取结构化列表

        当正则提取效果不好时，调用 LLM 智能识别

        Args:
            html: 页面 HTML 文本
            llm_client: LLM 客户端（LangChain 格式，支持 invoke，返回 .content）

        Returns:
            ListItem 列表
        """
        # 清理 HTML（去掉 script/style 等无用标签）
        cleaned_html = self._clean_html(html)
        # 截断 HTML 避免 token 过多
        html_snippet = cleaned_html[:6000] if len(cleaned_html) > 6000 else cleaned_html

        system_prompt = """你是一个专业的网页数据提取专家。从给定的 HTML 片段中，识别并提取列表数据。

任务：
1. 先判断页面类型（热榜/新闻列表/商品列表/帖子列表等）
2. 找出每条列表项，提取尽可能多的字段
3. 只返回 JSON 数组，不要其他解释

字段说明：
- rank: 排名/序号（数字）
- title: 标题/名称（必填）
- url: 链接（完整 URL 或相对路径）
- hot: 热度/浏览量/点赞数/评论数
- author: 作者/发布者
- published_date: 发布时间
- description: 简介/摘要/内容片段
- category: 分类/标签

输出格式（严格 JSON 数组）：
[
  {
    "rank": "1",
    "title": "xxx",
    "url": "https://xxx",
    "hot": "123万",
    "author": "xxx",
    "published_date": "2024-01-01",
    "description": "xxx",
    "category": "xxx"
  }
]

注意：
- 只提取页面中实际存在的字段，没有的就不填
- URL 如果是相对路径，保持原样（外面会处理）
- 只返回 JSON，不要任何其他文字、markdown 代码块等
- 尽量提取所有有意义的列表项"""

        user_prompt = f"""HTML 片段：
{html_snippet}

请提取列表数据，返回 JSON 数组："""

        try:
            from langchain_core.messages import SystemMessage, HumanMessage

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]

            response = llm_client.invoke(messages)
            response_text = response.content if hasattr(response, 'content') else str(response)

            items = self._parse_llm_response(response_text)
            return items
        except Exception as e:
            logger.error(f"LLM 提取失败: {e}")
            return []

    def _clean_html(self, html: str) -> str:
        """清理 HTML，去除无用内容"""
        # 移除 script 和 style
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # 移除注释
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        # 压缩空白
        html = re.sub(r'\s+', ' ', html)
        return html.strip()

    def _parse_llm_response(self, response: str) -> list[ListItem]:
        """解析 LLM 返回的 JSON"""
        items = []

        # 提取 JSON 部分
        json_match = re.search(r'\[[\s\S]*\]', response)
        if not json_match:
            return items

        try:
            data = json.loads(json_match.group(0))
            for item_data in data:
                item = ListItem(
                    rank=str(item_data.get('rank', '')),
                    title=item_data.get('title', ''),
                    url=item_data.get('url', ''),
                    hot=item_data.get('hot', ''),
                    author=item_data.get('author', ''),
                    published_date=item_data.get('published_date', ''),
                    description=item_data.get('description', ''),
                )
                # 解析绝对 URL
                if item.url and self.base_url:
                    item.url = self._resolve_url(item.url)
                items.append(item)
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"解析 LLM 响应失败: {e}")

        return items


def _is_valid_list_item(item: ListItem, base_url: str = "") -> bool:
    """判断一个列表项是否有效（不是导航/页脚/垃圾链接）"""
    if not item.title or not item.url:
        return False

    title = item.title.strip()
    url = item.url.strip()

    # 标题太短
    if len(title) < 4:
        return False

    # 过滤导航/页脚关键词
    nav_keywords = [
        # 导航类
        '首页', '登录', '注册', '退出', '设置', '帮助', '关于', '联系',
        '反馈', '客服', '隐私', '协议', '条款', '服务', '中心',
        '下载', 'APP', '客户端', '微博', '微信', 'QQ',
        # 页脚类
        'About', 'about', 'Privacy', 'privacy', 'Terms', 'terms',
        'Contact', 'contact', 'Help', 'help', 'Home', 'home',
        'Login', 'login', 'Register', 'register',
        # 功能类
        '更多', '查看全部', '展开', '收起', '下一页', '上一页',
        '末页', '返回', '跳转', '搜索', '提交',
    ]
    for kw in nav_keywords:
        if title == kw or title.startswith(kw) or title.endswith(kw):
            return False

    # 过滤 URL 中的导航路径
    nav_paths = [
        '/about', '/contact', '/help', '/privacy', '/terms',
        '/login', '/register', '/signup', '/setting',
        'privacy.weibo', 'kefu.weibo', 'ir.weibo',
    ]
    for path in nav_paths:
        if path in url:
            return False

    # 过滤 javascript 和锚点链接
    if url.startswith('javascript:') or url.startswith('#'):
        return False

    # 过滤只有数字的标题（可能是页码）
    if title.isdigit():
        return False

    # 过滤纯日期/时间格式的标题
    import re
    date_patterns = [
        r'^\d{1,2}月\d{1,2}日',
        r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}',
        r'^\d{1,2}:\d{2}$',
        r'^昨天',
        r'^\d+小时前',
        r'^\d+天前',
        r'^\d+分钟前',
        r'^\d+\s*小时前',
        r'^\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}',  # 6-19 14:06 格式
        r'^\d+\s*分钟',
        r'^\d+\s*小时',
        r'^\d+\s*天',
    ]
    for pat in date_patterns:
        if re.match(pat, title):
            return False

    # 过滤 @用户名 格式（通常不是列表项）
    if title.startswith('@') and len(title) < 20:
        return False

    # 过滤用户主页链接（URL 包含 /n/ 或 /u/ 且标题是用户名）
    if re.search(r'/(n|u)/', url) and not title.startswith('#') and len(title) < 15:
        return False

    return True


def _assess_quality(items: list[ListItem]) -> float:
    """
    评估提取质量，返回 0-1 的质量分数

    判断标准：
    - 有话题标签(#xxx#)的比例（微博热搜特征）
    - 标题长度适中（热搜榜：3-30字）
    - URL 多样性
    - URL 是否为文章链接（质量更高）
    """
    if not items:
        return 0.0

    import re

    score = 0.0
    n = len(items)

    # 指标1：有话题标签的比例（微博热搜特征）
    hashtag_count = sum(1 for item in items if item.title.startswith('#') and item.title.endswith('#'))
    hashtag_ratio = hashtag_count / n
    score += hashtag_ratio * 0.3

    # 指标2：标题长度适中（3-30字，适合大多数热榜和新闻）
    good_length = sum(1 for item in items if 3 <= len(item.title) <= 30)
    length_ratio = good_length / n
    score += length_ratio * 0.25

    # 指标3：URL 多样性
    unique_urls = len(set(item.url for item in items))
    url_ratio = unique_urls / n
    score += url_ratio * 0.15

    # 指标4：URL 是文章链接的比例（高质量列表特征）
    article_url_count = sum(1 for item in items if re.search(r'\.(s?html?|php)|\/(doc|article|detail)\/', item.url, re.I))
    article_ratio = article_url_count / n
    score += article_ratio * 0.2

    # 指标5：数量足够（至少 10 条才算高质量列表）
    if n >= 20:
        score += 0.1
    elif n >= 10:
        score += 0.05

    return min(score, 1.0)


def _filter_by_keywords(items: list[ListItem], keywords: list[str]) -> list[ListItem]:
    """
    根据关键词过滤列表项（标题或描述包含任一关键词即保留）
    
    Args:
        items: 列表项
        keywords: 关键词列表
    
    Returns:
        过滤后的列表项
    """
    if not keywords:
        return items
    
    filtered = []
    for item in items:
        text = (item.title or "") + " " + (item.description or "")
        for kw in keywords:
            if kw and kw in text:
                filtered.append(item)
                break
    return filtered


def find_category_links(html: str, keywords: list[str], base_url: str = "") -> list[str]:
    """
    在 HTML 中查找相关分类/频道的链接（用于自动跳转）
    
    Args:
        html: 页面 HTML
        keywords: 关键词列表
        base_url: 基础 URL
    
    Returns:
        相关链接列表（按相关度排序）
    """
    if not keywords:
        return []
    
    from urllib.parse import urljoin, urlparse
    
    # 文章链接特征（排除这些）
    article_patterns = [
        r'/doc[-/]',
        r'/article[-/]',
        r'\.shtml$',
        r'\.html$',
        r'\.htm$',
        r'/\d{4}[-/]\d{2}',
        r'/detail[-/]',
        r'/news/detail',
    ]
    
    def _is_article_url(url: str) -> bool:
        """判断是否为文章链接（非频道）"""
        for pat in article_patterns:
            if re.search(pat, url, re.IGNORECASE):
                return True
        # 路径层级太深的通常是文章（超过3级路径）
        parsed = urlparse(url)
        path = parsed.path.rstrip('/')
        depth = len([p for p in path.split('/') if p])
        if depth >= 3:
            return True
        return False
    
    def _is_nav_text(text: str) -> bool:
        """判断文本是否像导航/频道名称（短、简洁）"""
        if not text:
            return False
        # 导航文本通常 2-6 个字
        if len(text) < 2 or len(text) > 8:
            return False
        # 排除明显的文章标题特征
        if any(punct in text for punct in ['，', '。', '！', '？', '：', '“', '”', '——']):
            return False
        # 排除数字开头的（通常是新闻列表项）
        if text[0].isdigit():
            return False
        return True
    
    # 提取所有链接及文本
    links = []
    for match in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>([^<]*)</a>', html, re.IGNORECASE | re.DOTALL):
        href = match.group(1).strip()
        text = re.sub(r'\s+', ' ', match.group(2)).strip()
        if not href or not text:
            continue
        if href.startswith('javascript:') or href.startswith('#'):
            continue
        # 过滤掉太长的文本（通常不是导航）
        if len(text) > 15:
            continue
        # 解析成完整 URL
        if href.startswith('/'):
            href = urljoin(base_url, href)
        elif not href.startswith('http'):
            continue
        # 只保留同域名/子域名的链接
        if base_url:
            base_domain = urlparse(base_url).netloc.replace('www.', '')
            link_domain = urlparse(href).netloc.replace('www.', '')
            if base_domain and link_domain != base_domain and not link_domain.endswith('.' + base_domain):
                continue
        # 排除文章链接
        if _is_article_url(href):
            continue
        # 优先选择导航样式的文本
        if not _is_nav_text(text):
            continue
        links.append((text, href))
    
    # 去重（按 URL）
    seen_urls = set()
    unique_links = []
    for text, href in links:
        if href not in seen_urls:
            seen_urls.add(href)
            unique_links.append((text, href))
    
    # 按关键词匹配度排序
    scored = []
    for text, href in unique_links:
        score = 0
        for kw in keywords:
            if kw in text:
                score += 20  # 完全匹配权重高
            elif any(char in text for char in kw[:2]):
                score += 2
        # URL 中有关键词也加分
        for kw in keywords:
            if kw in href.lower():
                score += 10
        if score > 0:
            scored.append((score, text, href))
    
    scored.sort(reverse=True)
    return [href for _, _, href in scored[:5]]


def extract_list(
    html: str,
    base_url: str = "",
    max_items: int = 50,
    use_llm: str = "auto",
    llm_client=None,
    min_quality: float = 0.6,
    keyword_filter: list[str] | None = None,
) -> tuple[list[ListItem], str]:
    """
    从 HTML 中提取列表数据（混合策略：正则优先，LLM 兜底）

    Args:
        html: 页面 HTML
        base_url: 基础 URL（用于解析相对路径）
        max_items: 最大提取数量
        use_llm: "auto"（自动）/ "always"（总是用）/ "never"（从不）
        llm_client: LLM 客户端（需要支持 invoke 方法，返回 .content）
        min_quality: 质量阈值（0-1，低于此值触发 LLM）
        keyword_filter: 关键词过滤列表，只保留标题/描述包含这些关键词的项

    Returns:
        (列表项, 使用的方法: "regex" / "llm")
    """
    extractor = ListExtractor(base_url=base_url)

    # 第一步：先用正则提取
    regex_items = extractor.extract_from_html(html, max_items=max_items * 3)

    # 第二步：质量过滤
    valid_items = [item for item in regex_items if _is_valid_list_item(item, base_url)]

    # 第三步：关键词过滤
    if keyword_filter:
        valid_items = _filter_by_keywords(valid_items, keyword_filter)

    # 第四步：评估质量
    quality_score = _assess_quality(valid_items)

    # 决定是否用 LLM
    need_llm = False
    if use_llm == "always":
        need_llm = True
    elif use_llm == "auto":
        if quality_score < min_quality or len(valid_items) < 5:
            need_llm = True

    # 第五步：如果需要，用 LLM 增强
    if need_llm and llm_client:
        llm_items = extractor.extract_with_llm(html, llm_client)
        llm_valid = [item for item in llm_items if _is_valid_list_item(item, base_url)]
        if keyword_filter:
            llm_valid = _filter_by_keywords(llm_valid, keyword_filter)
        llm_quality = _assess_quality(llm_valid)

        if llm_valid and llm_quality > quality_score:
            return llm_valid[:max_items], "llm"

    return valid_items[:max_items], "regex"


def extract_from_dom(
    url: str,
    max_items: int = 50,
    headless: bool = True,
    keyword_filter: list[str] | None = None,
) -> tuple[list[ListItem], str]:
    """
    使用 Playwright 直接从 DOM 中提取列表数据（通用方案）

    适用于：JS 动态渲染的页面，HTML 正则提取效果不好的热榜/排行榜

    原理：
    1. 用 Playwright 渲染页面
    2. 直接从 DOM 中获取所有链接及其文本
    3. 通过文本特征（排名、长度、URL 模式）过滤出列表项

    Args:
        url: 页面 URL
        max_items: 最大提取数量
        headless: 是否无头模式
        keyword_filter: 关键词过滤

    Returns:
        (列表项, 方法名: "dom")
    """
    import re

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], "dom"

    raw_items = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            page.wait_for_timeout(2000)

            # 获取所有链接
            links = page.query_selector_all("a")

            for link in links:
                try:
                    text = link.inner_text()
                    href = link.get_attribute("href") or ""
                    if text and href:
                        raw_items.append((text.strip(), href.strip()))
                except:
                    pass

            browser.close()
    except Exception as e:
        logger.error(f"[extract_from_dom] Playwright 错误: {e}")
        return [], "dom"

    if not raw_items:
        return [], "dom"

    # 解析每个链接，提取排名和标题
    items: list[ListItem] = []
    seen_urls = set()
    seen_titles = set()

    for text, href in raw_items:
        if not text or not href:
            continue

        # 解析排名（如 "1\n标题"、"1 标题"、"1. 标题" 等）
        rank = ""
        title = text

        # 模式1: 数字 + 换行 + 标题
        rank_match = re.match(r'^\s*(\d{1,3})\s*\n+(.+)$', text, re.DOTALL)
        if rank_match:
            rank = rank_match.group(1)
            title = rank_match.group(2).strip()

        # 模式2: 数字 + 空格/点 + 标题
        if not rank:
            rank_match = re.match(r'^\s*(\d{1,3})[\.\s、]+(.+)$', text)
            if rank_match:
                rank = rank_match.group(1)
                title = rank_match.group(2).strip()

        # 清理标题：移除末尾的热度标记（热、新、爆、沸等）
        title = re.sub(r'\s*[热新爆沸荐]\s*$', '', title).strip()
        # 移除多余的空白
        title = re.sub(r'\s+', ' ', title).strip()

        # 过滤无效项
        if not title:
            continue
        if len(title) < 3 or len(title) > 80:
            continue

        # 过滤导航类链接
        nav_keywords = [
            '首页', '登录', '注册', '退出', '设置', '帮助', '反馈',
            '更多', '查看全部', '下一页', '上一页', '第一页', '最后一页',
            '首页', '新闻', '财经', '体育', '娱乐', '军事', '教育',
            '科技', '数码', '游戏', '汽车', '房产', '旅游', '美食',
        ]
        if title in nav_keywords or title in seen_titles:
            continue

        # URL 去重
        if href in seen_urls:
            continue
        seen_urls.add(href)
        seen_titles.add(title)

        # 提取标签（热、新、爆、沸）
        tag = ""
        if re.search(r'[热]$', text):
            tag = "热"
        elif re.search(r'[新]$', text):
            tag = "新"
        elif re.search(r'[爆]$', text):
            tag = "爆"
        elif re.search(r'[沸]$', text):
            tag = "沸"

        # 提取热度数字（如 "123万"、"4567"）
        hot = ""
        hot_match = re.search(r'(\d+[万亿]?)\s*$', text)
        if hot_match and not rank:
            hot = hot_match.group(1)

        item = ListItem(
            rank=rank,
            title=title,
            url=href,
            hot=hot,
            description="",
        )
        if tag:
            item.extra["tag"] = tag
        items.append(item)

    # 关键词过滤
    if keyword_filter and items:
        items = _filter_by_keywords(items, keyword_filter)

    # 按排名排序（有排名的排前面）
    def _sort_key(item):
        if item.rank and item.rank.isdigit():
            return (0, int(item.rank))
        return (1, 0)

    items.sort(key=_sort_key)

    return items[:max_items], "dom"


def save_list_to_db(
    items: list[ListItem],
    source: str = "",
    category: str = "",
) -> int:
    """
    批量保存列表到数据库

    Args:
        items: ListItem 列表
        source: 来源标识
        category: 分类

    Returns:
        保存的记录数
    """
    from ..tools.database import get_default_db

    db = get_default_db()
    count = 0

    for item in items:
        record = item.to_db_record(source=source)
        if category and not record.get('category'):
            record['category'] = category

        try:
            db.save_crawl(**record)
            count += 1
        except Exception as e:
            logger.error(f"保存失败 [{item.title[:20]}]: {e}")

    return count


def format_list_output(items: list[ListItem], show_url: bool = True) -> str:
    """格式化列表输出"""
    if not items:
        return "未提取到列表数据"

    lines = [f"共提取 {len(items)} 条记录："]
    for i, item in enumerate(items, 1):
        rank_str = f"[{item.rank}] " if item.rank else f"{i}. "
        title_str = item.title or "(无标题)"
        hot_str = f"  🔥 {item.hot}" if item.hot else ""
        author_str = f"  👤 {item.author}" if item.author else ""
        date_str = f"  📅 {item.published_date}" if item.published_date else ""

        lines.append(f"  {rank_str}{title_str}{hot_str}{author_str}{date_str}")

        if show_url and item.url:
            lines.append(f"     🔗 {item.url}")

    return "\n".join(lines)
