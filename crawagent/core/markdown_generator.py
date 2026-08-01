"""Markdown 生成器（P2-2）

- html2text 定制转换（主），markdownify 兜底
- citation 引用格式：文末 `> 来源：[title](url)` / 行内引用
- fit_markdown：按段落边界截断（超大页面保护）

参考 Crawl4AI markdown_generation_strategy.py 与 citation 设计。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger


class MarkdownGenerator:
    """HTML → 干净 Markdown，带引用与截断能力。"""

    def __init__(
        self,
        body_width: int = 0,
        ignore_links: bool = False,
        ignore_images: bool = False,
        cite_style: str = "footer",
        max_length: Optional[int] = None,
    ):
        """
        Args:
            body_width: 正文折行宽度，0 = 不折行
            ignore_links: 丢弃链接（只保留文本）
            ignore_images: 丢弃图片
            cite_style: "footer" 文末引用 / "inline" 行内引用 / None 不引用
            max_length: 最大字符数，超出按段落截断
        """
        self.body_width = body_width
        self.ignore_links = ignore_links
        self.ignore_images = ignore_images
        self.cite_style = cite_style
        self.max_length = max_length

    def _preprocess_links(self, html: str, baseurl: str = "") -> str:
        """预处理 HTML：把所有相对链接转为绝对 URL，并提取 body 部分。

        修复 html2text 的 baseurl 参数 bug：
        - 绝对路径 href（如 /cn/shop/...）会被错误拼接为 baseurl 路径 + </href>
        - <html>/<head> 标签会被文本化为 "html " 前缀
        """
        if not html or not html.strip() or not baseurl:
            return html
        try:
            from urllib.parse import urljoin
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            # 把所有 <a href> 和 <img src> 的相对路径转为绝对 URL
            for tag in soup.find_all(["a", "img", "source", "link"]):
                attr = "href" if tag.name in ("a", "link") else "src"
                val = tag.get(attr)
                if val and not val.startswith(("http://", "https://", "#", "mailto:", "tel:", "javascript:", "data:")):
                    tag[attr] = urljoin(baseurl, val)
            # 只提取 body 部分，避免 <html>/<head> 被文本化
            body = soup.body or soup
            return str(body)
        except Exception as e:
            logger.debug(f"链接预处理失败，用原始 HTML: {e}")
            return html

    def html_to_markdown(self, html: str, baseurl: str = "") -> str:
        """html2text 转换，失败时回退 markdownify。"""
        if not html or not html.strip():
            return ""

        # 预处理：相对链接转绝对 + 提取 body，绕过 html2text 的 baseurl bug
        source = self._preprocess_links(html, baseurl) if baseurl else html

        try:
            import html2text

            h = html2text.HTML2Text()
            h.body_width = self.body_width
            h.ignore_links = self.ignore_links
            h.ignore_images = self.ignore_images
            h.protect_links = True
            h.unicode_snob = True
            h.single_line_break = False
            # 不再传 baseurl（已预处理为绝对链接），避免 html2text 的拼接 bug
            return h.handle(source)
        except Exception as e:
            logger.debug(f"html2text 失败，回退 markdownify: {e}")

        try:
            from markdownify import markdownify as md_convert

            options = {
                "heading_style": "ATX",
                "bullets": "-",
                "strip": ["script", "style", "nav", "aside", "footer", "form"],
            }
            if self.ignore_links:
                options["strip"] = options["strip"] + ["a"]
            return md_convert(source, **options)
        except Exception as e2:
            logger.warning(f"markdownify 也失败: {e2}")
            return ""

    def add_citation(self, markdown: str, url: str = "", title: str = "") -> str:
        """追加来源引用。"""
        if not url:
            return markdown
        label = title.strip() if title.strip() else url
        citation = f"[{label}]({url})"
        md = markdown.rstrip()
        if self.cite_style == "inline":
            return f"{md}\n\n（来源：{citation}）"
        # footer
        return f"{md}\n\n---\n\n> 来源：{citation}"

    def fit_markdown(self, markdown: str, max_length: Optional[int] = None) -> str:
        """按段落边界截断到 max_length。"""
        limit = max_length or self.max_length
        if not limit or len(markdown) <= limit:
            return markdown

        paragraphs = markdown.split("\n\n")
        result: list = []
        total = 0
        for para in paragraphs:
            if total + len(para) + 2 > limit:
                break
            result.append(para)
            total += len(para) + 2
        if not result:
            return markdown[:limit] + "\n\n...（内容过长已截断）"
        return "\n\n".join(result) + "\n\n...（内容过长已截断）"

    def generate(
        self,
        html: str,
        url: str = "",
        title: str = "",
        clean: bool = True,
        max_length: Optional[int] = None,
    ) -> str:
        """一步生成：可选去噪 → 转 Markdown → 引用 → 截断。"""
        if not html or not html.strip():
            return ""

        source = html
        if clean:
            from crawagent.core.content_filter import PruningContentFilter

            source = PruningContentFilter().filter_content(html)

        md = self.html_to_markdown(source, baseurl=url)
        md = self.fit_markdown(md, max_length)
        if url and self.cite_style:
            md = self.add_citation(md, url, title)
        return md


def html_to_clean_markdown(
    html: str,
    url: str = "",
    title: str = "",
    max_length: Optional[int] = None,
) -> str:
    """便捷函数：HTML → 去噪 → 干净 Markdown（带引用）。"""
    return MarkdownGenerator(max_length=max_length).generate(html, url=url, title=title)
