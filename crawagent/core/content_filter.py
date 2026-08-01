"""内容清洗：三档内容过滤器（P2-1）

参考 Crawl4AI content_filter_strategy.py，去除外部重依赖（BM25 自实现）：
- PruningContentFilter : 规则去噪（删导航/广告/脚本/低文本节点）
- BM25ContentFilter    : 按查询相关性打分，保留最相关内容块
- LLMContentFilter     : LLM 语义过滤（异步）

统一入口：create_content_filter(name, **kwargs)
"""
from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Comment
from loguru import logger


# ---------------------------------------------------------------------------
# 轻量分词 + BM25（无外部依赖）
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    """轻量分词：英文/数字 token + 中文二元组。"""
    tokens: List[str] = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    for cjk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(cjk) == 1:
            tokens.append(cjk)
        else:
            tokens.extend(cjk[i : i + 2] for i in range(len(cjk) - 1))
    return tokens


class BM25Scorer:
    """BM25Okapi 轻量实现，用于文档相关性打分。"""

    def __init__(self, docs: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = docs
        self.doc_len = [len(d) for d in docs]
        self.avgdl = sum(self.doc_len) / len(docs) if docs else 0.0
        n_docs = len(docs)
        doc_freq: Dict[str, int] = {}
        for d in docs:
            for t in set(d):
                doc_freq[t] = doc_freq.get(t, 0) + 1
        self.doc_freq = doc_freq
        self.idf = {
            t: math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
            for t, df in doc_freq.items()
        }

    def score(self, query: List[str]) -> List[float]:
        scores: List[float] = []
        for dl, doc in zip(self.doc_len, self.docs):
            tf: Dict[str, int] = {}
            for t in doc:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in query:
                if t in tf:
                    denominator = tf[t] + self.k1 * (
                        1 - self.b + self.b * dl / self.avgdl
                    ) if self.avgdl else tf[t]
                    s += self.idf.get(t, 0.0) * (tf[t] * (self.k1 + 1)) / denominator
            scores.append(s)
        return scores


# ---------------------------------------------------------------------------
# 过滤器基类
# ---------------------------------------------------------------------------

class RelevantContentFilter(ABC):
    """内容过滤器基类。"""

    def __init__(self, user_query: str = "", verbose: bool = False):
        self.user_query = user_query
        self.verbose = verbose
        self.excluded_tags = {
            "nav", "footer", "header", "aside", "script", "style",
            "form", "iframe", "noscript", "svg",
        }
        self.header_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}
        # 注意：ads? 必须带词边界，否则会误伤 "upgrade/header/load" 等含 "ad" 的类名
        self.negative_patterns = re.compile(
            r"\b(nav|footer|header|sidebar|ads?|comment|promo|advert|social|"
            r"share|recommend|copyright|login|register|cookie|breadcrumb)\b",
            re.I,
        )

    @abstractmethod
    def filter_content(self, html: str) -> str:
        """过滤 HTML，返回清洗后的 HTML。"""
        raise NotImplementedError

    def extract_page_query(self, soup: BeautifulSoup) -> str:
        """从页面元数据构造查询（title/h1/keywords/description）。"""
        if self.user_query:
            return self.user_query
        parts: List[str] = []
        try:
            if soup.title and soup.title.string:
                parts.append(soup.title.string.strip())
        except Exception:
            pass
        h1 = soup.find("h1")
        if h1:
            parts.append(h1.get_text(strip=True))
        for name in ("keywords", "description"):
            meta = soup.find("meta", attrs={"name": name})
            if meta and meta.get("content"):
                parts.append(str(meta["content"]).strip())
        return " ".join(p for p in parts if p)

    def _remove_excluded(self, soup: BeautifulSoup) -> None:
        """删除排除标签 + HTML 注释。"""
        for tag in soup(self.excluded_tags):
            tag.decompose()
        for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()

    def _soup_from(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# Pruning：规则去噪
# ---------------------------------------------------------------------------

class PruningContentFilter(RelevantContentFilter):
    """规则去噪：删导航/广告/脚本 + 低文本节点 + 负模式匹配节点。"""

    def __init__(
        self,
        user_query: str = "",
        min_word_count: int = 2,
        negative_max_text: int = 400,
        verbose: bool = False,
    ):
        super().__init__(user_query=user_query, verbose=verbose)
        self.min_word_count = min_word_count
        # 匹配负模式的节点，仅当文本量小于该阈值时才删除（避免误删正文容器）
        self.negative_max_text = negative_max_text

    def filter_content(self, html: str) -> str:
        if not html or not html.strip():
            return ""
        soup = self._soup_from(html)
        self._remove_excluded(soup)

        # 1. 负模式节点（class/id 命中 nav/ads/comment 等）
        for el in soup.find_all(True):
            if getattr(el, "attrs", None) is None:
                continue  # 已在上一步 decompose 的残留节点
            if el.name in ("html", "body"):
                continue  # 骨架节点永不动
            classes = el.get("class") or []
            if isinstance(classes, str):
                classes = [classes]
            ident = el.get("id", "") or ""
            haystack = f"{' '.join(classes)} {ident}"
            if self.negative_patterns.search(haystack):
                text_len = len(el.get_text("", strip=True))
                # 含正文容器标签（article/main/section）不删；文本过大的容器不删
                if el.find(["article", "main"]):
                    continue
                if text_len <= self.negative_max_text:
                    el.decompose()

        # 2. 低内容节点：无文本、无图片/表格/代码的叶子容器
        for el in soup.find_all(True):
            if getattr(el, "attrs", None) is None:
                continue
            if el.name in ("html", "body"):
                continue
            text = el.get_text("", strip=True)
            meaningful = el.find(["img", "table", "pre", "code", "video", "audio"])
            if not text and not meaningful:
                el.decompose()
                continue
            # 极短文本（如单个空白/符号）且无子内容 → 删除
            if text and len(text) < self.min_word_count and not meaningful:
                el.decompose()

        # 3. 去空白
        for el in soup.find_all(string=True):
            if isinstance(el, Comment):
                continue
            if el.parent and el.parent.name in ("pre", "code", "textarea"):
                continue
            el.replace_with(re.sub(r"\s+", " ", el.string or ""))

        return str(soup)


# ---------------------------------------------------------------------------
# BM25：按查询相关性过滤
# ---------------------------------------------------------------------------

class BM25ContentFilter(RelevantContentFilter):
    """BM25 相关性过滤：保留与查询最相关的文本块。"""

    def __init__(
        self,
        user_query: str = "",
        threshold: float = 0.5,
        top_k: Optional[int] = None,
        min_score: float = 0.0,
        verbose: bool = False,
    ):
        super().__init__(user_query=user_query, verbose=verbose)
        self.threshold = threshold
        self.top_k = top_k
        self.min_score = min_score

    def filter_content(self, html: str) -> str:
        if not html or not html.strip():
            return ""
        soup = self._soup_from(html)
        self._remove_excluded(soup)
        body = soup.body or soup

        query = self.extract_page_query(soup)
        if not query:
            return str(soup)

        chunks = self._extract_text_chunks(body)
        if not chunks:
            return str(soup)

        docs = [_tokenize(text) for text, _tag in chunks]
        scorer = BM25Scorer(docs)
        scores = scorer.score(_tokenize(query))

        max_score = max(scores) if scores else 0.0
        keep_idx: List[int] = []
        for i, score in enumerate(scores):
            if score >= max_score * self.threshold and score >= self.min_score:
                keep_idx.append(i)

        if self.top_k:
            # 按分数降序取前 top_k，再按原顺序输出
            ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[: self.top_k]
            keep_idx = sorted(ranked)

        if not keep_idx:
            logger.warning("[BM25] 没有块超过阈值，返回全部内容")
            return str(soup)

        kept_html = "".join(f"<{chunks[i][1]}>{chunks[i][0]}</{chunks[i][1]}>" for i in keep_idx)
        return f"<body>{kept_html}</body>"

    def _extract_text_chunks(self, body) -> List[Tuple[str, str]]:
        """按块容器提取 (text, tag)，保持文档顺序。"""
        chunks: List[Tuple[str, str]] = []
        block_tags = {"p", "li", "blockquote", "pre", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}
        for el in body.find_all(block_tags):
            text = el.get_text(" ", strip=True)
            if text:
                chunks.append((text, el.name))
        return chunks


# ---------------------------------------------------------------------------
# LLM：语义过滤（异步）
# ---------------------------------------------------------------------------

class LLMContentFilter(RelevantContentFilter):
    """LLM 语义过滤：把正文切块交给 LLM 判断保留/丢弃。

    注意：LLM 过滤器需要真实 API key，超时/失败时回退为不过滤。
    """

    def __init__(
        self,
        user_query: str = "",
        chunk_chars: int = 1500,
        max_chunks: int = 60,
        verbose: bool = False,
    ):
        super().__init__(user_query=user_query, verbose=verbose)
        self.chunk_chars = chunk_chars
        self.max_chunks = max_chunks

    def filter_content(self, html: str) -> str:
        raise NotImplementedError("LLMContentFilter 请使用异步 afilter_content()")

    async def afilter_content(self, html: str) -> str:
        if not html or not html.strip():
            return ""
        # 先规则去噪，减少 LLM 输入
        pruned = PruningContentFilter().filter_content(html)
        soup = self._soup_from(pruned)
        body = soup.body or soup

        from crawagent.core.chunking import FixedSizeChunking
        from crawagent.llm.factory import get_llm
        from crawagent.llm.prompts import PROMPT_FILTER_CONTENT

        full_text = body.get_text("\n", strip=True)
        chunks = FixedSizeChunking(chunk_size=self.chunk_chars).chunk(full_text)[: self.max_chunks]
        if not chunks:
            return str(soup)

        query = self.extract_page_query(soup)
        prompt = PROMPT_FILTER_CONTENT.format(query=query, content="\n\n".join(
            f"[{i}] {chunk}" for i, chunk in enumerate(chunks)
        ))

        try:
            llm = get_llm()
            resp = await llm.ainvoke([
                {"role": "system", "content": "你是网页内容过滤器，输出严格 JSON。"},
                {"role": "user", "content": prompt},
            ])
            import json as _json
            m = re.search(r"\{.*\}", resp.content, re.DOTALL)
            data = _json.loads(m.group()) if m else _json.loads(resp.content)
            kept_idx = {int(item["index"]) for item in data.get("kept", []) if "index" in item}
        except Exception as e:
            logger.warning(f"[LLMFilter] LLM 过滤失败，回退不过滤: {e}")
            return str(soup)

        kept = [chunks[i] for i in sorted(kept_idx) if 0 <= i < len(chunks)]
        if not kept:
            return str(soup)
        return "<body>" + "\n\n".join(f"<section>{c}</section>" for c in kept) + "</body>"


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def create_content_filter(name: str = "prune", **kwargs) -> RelevantContentFilter:
    """创建内容过滤器。

    Args:
        name: "prune" | "bm25" | "llm"
    """
    name = (name or "prune").lower()
    if name not in ("prune", "bm25", "llm"):
        raise ValueError(f"未知内容过滤器: {name}，可选: prune / bm25 / llm")
    if name == "bm25":
        return BM25ContentFilter(**kwargs)
    if name == "llm":
        return LLMContentFilter(**kwargs)
    return PruningContentFilter(**kwargs)
