"""DOM 广告移除器

在内容提取前移除广告/弹窗/推广元素，提高提取质量。

策略：
- CSS 选择器匹配常见广告容器
- 移除 script/style 中的广告脚本
- 移除已知广告网络的元素
- 移除遮挡正文的固定定位元素
"""
from __future__ import annotations

from typing import List, Optional

from bs4 import BeautifulSoup, Tag

from loguru import logger


# 广告容器的 CSS 选择器
AD_SELECTORS = [
    # Google Ads
    "ins.adsbygoogle",
    "[id*='google_ads']",
    "[id*='adsense']",
    ".ad-unit",

    # 通用广告 class
    "[class*='ad-banner']",
    "[class*='ad-container']",
    "[class*='ad-wrapper']",
    "[class*='advertisement']",
    "[class*='ad-slot']",
    "[class*='ad-zone']",
    "[id*='ad-banner']",
    "[id*='ad-container']",

    # 弹窗/模态框
    "[class*='popup']",
    "[class*='modal-backdrop']",
    "[class*='overlay-ad']",

    # 推广/赞助
    "[class*='sponsored']",
    "[class*='promotion']",
    "[data-ad]",

    # 社交分享浮动栏
    "[class*='social-share']",
    "[class*='share-bar']",
    "[class*='floating-share']",

    # Newsletter 订阅弹窗
    "[class*='newsletter-popup']",
    "[id*='newsletter-popup']",

    # 相关推荐（非内容主体）
    "[class*='related-videos']",
    "[class*='recommendation']",

    # 评论区广告
    "[class*='ad-comment']",

    # 百度推广
    "[class*='baidu_ad']",
    "[id*='baidu_ad']",

    # 懒加载占位符
    "[class*='loading-placeholder']",
    "[class*='skeleton-loader']",
]

# 广告脚本域名特征
AD_SCRIPT_DOMAINS = [
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.net",
    "connect.facebook",
    "amazon-adsystem",
    "adnxs.com",
    "taboola.com",
    "outbrain.com",
    "criteo.com",
    "scorecardresearch.com",
    "quantserve.com",
    "hotjar.com",
]


class AdRemover:
    """DOM 广告移除器

    用法:
        remover = AdRemover()
        cleaned_html = remover.remove(html)
        # 或对 BeautifulSoup 对象操作
        soup = remover.clean_soup(soup)
    """

    def __init__(self, custom_selectors: Optional[List[str]] = None):
        self.selectors = list(AD_SELECTORS)
        if custom_selectors:
            self.selectors.extend(custom_selectors)

    def remove(self, html: str) -> str:
        """移除广告后返回清理过的 HTML

        Args:
            html: 原始 HTML

        Returns:
            清理后的 HTML
        """
        if not html:
            return html

        soup = BeautifulSoup(html, "html.parser")
        removed_count = self.clean_soup(soup)
        logger.debug(f"[AdRemover] 移除了 {removed_count} 个广告元素")
        return str(soup)

    def clean_soup(self, soup: BeautifulSoup) -> int:
        """直接操作 BeautifulSoup 对象，移除广告元素

        Args:
            soup: BeautifulSoup 对象

        Returns:
            移除的元素数量
        """
        removed = 0

        # 1. CSS 选择器匹配
        for selector in self.selectors:
            for el in soup.select(selector):
                el.decompose()
                removed += 1

        # 2. 移除广告 script 标签
        for script in soup.find_all("script"):
            src = script.get("src", "")
            if src:
                for domain in AD_SCRIPT_DOMAINS:
                    if domain in src.lower():
                        script.decompose()
                        removed += 1
                        break

        # 3. 移除内联广告脚本（检查内容）
        for script in soup.find_all("script"):
            text = script.string or ""
            if text and any(kw in text.lower() for kw in ["adsbygoogle", "googletag", "_taboola"]):
                script.decompose()
                removed += 1

        # 4. 移除广告 iframe
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if any(domain in src.lower() for domain in AD_SCRIPT_DOMAINS):
                iframe.decompose()
                removed += 1

        # 5. 移除空 div（广告移除后可能残留空容器）
        for div in soup.find_all("div"):
            if not div.get_text(strip=True) and not div.find(["img", "video", "iframe"]):
                # 只移除没有任何子元素的空 div
                if len(div.find_all()) == 0:
                    div.decompose()
                    removed += 1

        # 6. 移除 noscript 中的广告
        for noscript in soup.find_all("noscript"):
            noscript_text = noscript.get_text()
            if any(kw in noscript_text.lower() for kw in ["ad", "advertisement", "doubleclick"]):
                noscript.decompose()
                removed += 1

        return removed

    def add_selector(self, selector: str) -> None:
        """添加自定义广告选择器"""
        if selector not in self.selectors:
            self.selectors.append(selector)

    def remove_selectors(self, selectors: List[str]) -> None:
        """移除指定的选择器（取消某些规则）"""
        self.selectors = [s for s in self.selectors if s not in selectors]
