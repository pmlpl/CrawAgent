"""饱和度感知 / 自适应深爬（P6-4）

参考 Crawl4AI 设计：在 deep_crawl 的基础上，增加"主题一致性"判断：
- **饱和度**：当连续 N 页都与主题不相关，自动终止（或减小 max_pages）
- **路径相关性**：基于路径关键词匹配种子主题词
- **内容一致性**：基于 TF-IDF-like 的简化 bag-of-words overlap（无需 sklearn）
- **并发节流**：检测到 429/5xx/超时多时，主动降并发并退避

AdaptiveCrawler = DeepCrawler + 这些"监视器"。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse

from loguru import logger

from crawagent.core.deep_crawl import (
    DeepCrawler,
    DeepCrawlResult,
    CrawledPage,
    DeepCrawlStrategy,
)
from crawagent.core.filters import FilterChain


# ---------------------------------------------------------------------------
# 关键词提取（主题）
# ---------------------------------------------------------------------------

_STOPWORDS_EN = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "day", "get", "has", "him", "his",
    "how", "its", "let", "may", "new", "now", "old", "see", "two", "way",
    "who", "did", "his", "from", "that", "this", "with", "they", "have",
    "were", "been", "their", "what", "which", "will", "would", "there",
    "each", "about", "into", "than", "then", "them", "also", "such",
    "just", "very", "your", "over", "only", "after", "more", "some",
    "when", "where", "why", "being", "made", "most", "like", "between",
}

_STOPWORDS_ZH = {
    "的", "了", "和", "是", "在", "我", "有", "也", "与", "对", "将", "及",
    "为", "并", "被", "等", "从", "以", "这", "那", "他", "她", "们", "你",
    "就", "都", "个", "或", "但", "不", "而", "要", "去", "到", "一",
    "上", "下", "中", "下", "之", "于", "还", "可", "其", "与", "及",
    "若", "把", "呢", "啊", "吧", "吗", "里", "向", "前", "后", "左", "右",
}

_TOKEN_RE = re.compile(r"[a-zA-Z\u4e00-\u9fa5][\w\u4e00-\u9fa5]*")


def extract_keywords(text: str, top_k: int = 20, min_len: int = 2) -> Dict[str, int]:
    """简易关键词提取：词频 top_k（不含停用词）。"""
    if not text:
        return {}
    tokens = _TOKEN_RE.findall(text.lower())
    freq: Dict[str, int] = {}
    for t in tokens:
        if len(t) < min_len:
            continue
        if t in _STOPWORDS_EN or t in _STOPWORDS_ZH:
            continue
        freq[t] = freq.get(t, 0) + 1
    if not freq:
        return {}
    items = sorted(freq.items(), key=lambda x: -x[1])[:top_k]
    return {k: v for k, v in items}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


# ---------------------------------------------------------------------------
# 主题模型（从种子页/用户指定提取主题词）
# ---------------------------------------------------------------------------

@dataclass
class Topic:
    """主题描述（用户指定 / 种子页提取）。"""
    keywords: Set[str] = field(default_factory=set)
    path_keywords: Set[str] = field(default_factory=set)
    min_content_similarity: float = 0.05  # 内容关键词重合率下限
    min_path_similarity: float = 0.0      # 路径关键词重合率下限

    @classmethod
    def from_text(cls, seed_text: str, top_k: int = 20, min_sim: float = 0.03) -> "Topic":
        kw = extract_keywords(seed_text, top_k=top_k)
        return cls(keywords=set(kw.keys()), min_content_similarity=min_sim)

    @classmethod
    def from_keywords(cls, keywords: List[str], path_keywords: Optional[List[str]] = None) -> "Topic":
        return cls(
            keywords=set(k.lower() for k in keywords),
            path_keywords=set(k.lower() for k in (path_keywords or [])),
        )


# ---------------------------------------------------------------------------
# 饱和度感知
# ---------------------------------------------------------------------------

@dataclass
class SaturationConfig:
    """饱和度终止阈值配置。"""
    # 连续不相关页数 → 立即饱和退出
    consecutive_off_topic: int = 10
    # 全局不相关率 ≥ ratio 且已抓 ≥ min_pages → 饱和退出
    off_topic_ratio: float = 0.6
    off_topic_min_pages: int = 20
    # 硬上限（防死循环，即使全相关也退出）
    max_pages_hard: int = 200


@dataclass
class Saturation:
    """运行时饱和度状态。"""
    consecutive_off: int = 0
    on_topic_count: int = 0
    off_topic_count: int = 0
    saturated: bool = False
    reason: str = ""
    off_topic_urls: List[str] = field(default_factory=list)
    on_topic_urls: List[str] = field(default_factory=list)

    def record(self, page: CrawledPage, on_topic: bool) -> None:
        if on_topic:
            self.on_topic_count += 1
            self.consecutive_off = 0
            self.on_topic_urls.append(page.url)
        else:
            self.off_topic_count += 1
            self.consecutive_off += 1
            self.off_topic_urls.append(page.url)

    def check_saturated(self, cfg: SaturationConfig) -> bool:
        if self.saturated:
            return True
        if self.consecutive_off >= cfg.consecutive_off_topic:
            self.saturated = True
            self.reason = f"连续{cfg.consecutive_off_topic}页不相关（off_topic连续={self.consecutive_off}）"
            return True
        total = self.on_topic_count + self.off_topic_count
        if total >= cfg.off_topic_min_pages:
            ratio = self.off_topic_count / total if total else 0.0
            if ratio >= cfg.off_topic_ratio:
                self.saturated = True
                self.reason = f"不相关率 {ratio:.0%} ≥ {cfg.off_topic_ratio:.0%}（总 {total} 页）"
                return True
        if total >= cfg.max_pages_hard:
            self.saturated = True
            self.reason = f"达到硬上限 {cfg.max_pages_hard}"
            return True
        return False

    def to_summary(self) -> str:
        return (
            f"[Saturation] 相关={self.on_topic_count} 不相关={self.off_topic_count} "
            f"连续不相关={self.consecutive_off} 已饱和={self.saturated} {self.reason}".rstrip()
        )


# ---------------------------------------------------------------------------
# 节流器（降并发 / 退避）
# ---------------------------------------------------------------------------

@dataclass
class ThrottleConfig:
    base_concurrent: int = 5
    min_concurrent: int = 1
    error_rate_window: int = 20
    error_rate_trigger: float = 0.5  # window 内错误率超 50% 就降级
    backoff_seconds: float = 2.0


@dataclass
class Throttler:
    """根据错误率自适应并发。"""
    cfg: ThrottleConfig
    concurrent: int = 0
    recent_errors: List[bool] = field(default_factory=list)  # True=错 False=ok，window内
    backoff_until: float = 0.0

    def __post_init__(self) -> None:
        if self.concurrent <= 0:
            self.concurrent = self.cfg.base_concurrent

    def record(self, ok: bool) -> None:
        self.recent_errors.append(not ok)
        if len(self.recent_errors) > self.cfg.error_rate_window:
            self.recent_errors = self.recent_errors[-self.cfg.error_rate_window :]
        # 计算错误率
        if len(self.recent_errors) >= 4:
            rate = sum(1 for e in self.recent_errors if e) / len(self.recent_errors)
            if rate >= self.cfg.error_rate_trigger:
                # 降并发
                self.concurrent = max(self.cfg.min_concurrent, self.concurrent - 1)
                self.backoff_until = max(self.backoff_until, time.time() + self.cfg.backoff_seconds)
            elif rate <= 0.1 and self.concurrent < self.cfg.base_concurrent:
                # 恢复
                self.concurrent = min(self.cfg.base_concurrent, self.concurrent + 1)

    async def maybe_backoff(self) -> None:
        delay = self.backoff_until - time.time()
        if delay > 0:
            import asyncio
            await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# AdaptiveCrawler
# ---------------------------------------------------------------------------

class AdaptiveCrawler:
    """自适应深爬：在 DeepCrawler 上加饱和度+节流+主题一致性。

    用法：
        ac = AdaptiveCrawler(
            fetcher=my_fetcher,
            topic=Topic.from_keywords(["python", "文档", "guide"]),
        )
        result, saturation = await ac.crawl("https://docs.python.org/3/")
        print(result.to_summary())
        print(saturation.to_summary())
    """

    def __init__(
        self,
        fetcher: Any = None,
        strategy: DeepCrawlStrategy = DeepCrawlStrategy.BEST_FIRST,
        max_depth: int = 4,
        max_pages: int = 100,
        same_domain_only: bool = True,
        allow_subdomain: bool = True,
        request_timeout: float = 20.0,
        filter_chain: Optional[FilterChain] = None,
        topic: Optional[Topic] = None,
        saturation_cfg: Optional[SaturationConfig] = None,
        throttle_cfg: Optional[ThrottleConfig] = None,
        on_page_callback: Optional[Callable[[CrawledPage], Awaitable[None]]] = None,
    ) -> None:
        self._fetcher = fetcher
        self.strategy = strategy
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.same_domain_only = same_domain_only
        self.allow_subdomain = allow_subdomain
        self.request_timeout = request_timeout
        self._external_chain = filter_chain
        self.topic = topic or Topic()
        self.saturation_cfg = saturation_cfg or SaturationConfig()
        self.throttle_cfg = throttle_cfg or ThrottleConfig()
        self._on_page = on_page_callback

    async def _judge_on_topic(self, page: CrawledPage, seed_keywords: Set[str]) -> bool:
        """判断一页是否相关（基于 URL 路径关键词 + HTML 正文关键词 overlap）。"""
        # 1. 路径关键词（如果用户显式给 path_keywords）
        try:
            path = (urlparse(page.url).path or "/").lower()
        except Exception:
            path = ""
        path_tokens = set(_TOKEN_RE.findall(path))
        if self.topic.path_keywords:
            path_sim = jaccard(self.topic.path_keywords, path_tokens)
            if path_sim < self.topic.min_path_similarity:
                return False
            if path_sim >= 0.5:
                return True  # 路径强匹配直接过
        # 2. HTML 正文关键词
        # 只用前 20KB 文本做关键词提取，避免大页
        html_text = (page.html or "")[:20000]
        if html_text:
            # 快速去标签（比 bs4 更快的替代）
            stripped = re.sub(r"<[^>]+>", " ", html_text)
        else:
            stripped = ""
        page_kw = set(extract_keywords(stripped, top_k=30).keys())
        if seed_keywords:
            content_sim = jaccard(seed_keywords, page_kw)
            if content_sim < self.topic.min_content_similarity:
                return False
        return True

    async def crawl(self, seed_url: str) -> tuple[DeepCrawlResult, Saturation]:
        """执行自适应深爬，返回 (深爬结果, 饱和度状态)。"""
        saturation = Saturation()
        throttler = Throttler(self.throttle_cfg)

        # ---------- 步骤 1：抓种子页，提取关键词 ----------
        crawler = DeepCrawler(
            fetcher=self._fetcher,
            strategy=DeepCrawlStrategy.BFS,
            max_depth=0,  # 只要种子
            max_pages=1,
            same_domain_only=self.same_domain_only,
            allow_subdomain=self.allow_subdomain,
            max_concurrent=throttler.concurrent,
            request_timeout=self.request_timeout,
            filter_chain=self._external_chain,
            on_page_callback=None,
        )
        seed_result = await crawler.crawl(seed_url)
        seed_page = seed_result.pages[0] if seed_result.pages else None
        seed_keywords: Set[str] = set(self.topic.keywords)
        if seed_page and not seed_keywords:
            # 自动从种子页关键词提取主题
            stripped = re.sub(r"<[^>]+>", " ", seed_page.html or "")[:20000]
            kw = extract_keywords(stripped, top_k=25)
            seed_keywords = set(kw.keys())
        # 记录种子页到饱和度（默认是 on_topic）
        if seed_page:
            saturation.record(seed_page, True)

        # ---------- 步骤 2：真正深爬，带饱和度+节流 ----------
        # 包装一下 on_page_callback，加上饱和度判定和动态并发
        sem_after_stop = False

        async def _wrap_on_page(page: CrawledPage) -> None:
            nonlocal sem_after_stop
            # 节流反馈
            ok = not page.error and 200 <= page.status_code < 400
            throttler.record(ok)
            await throttler.maybe_backoff()

            # 主题判定
            on_topic = await self._judge_on_topic(page, seed_keywords)
            saturation.record(page, on_topic)

            # 用户回调
            if self._on_page is not None:
                try:
                    await self._on_page(page)
                except Exception as e:
                    logger.debug(f"[AdaptiveCrawl] on_page 回调异常: {e}")

            # 判定是否饱和
            if saturation.check_saturated(self.saturation_cfg) and not sem_after_stop:
                sem_after_stop = True
                # 通过设置 max_pages=当前数 让 DeepCrawler 尽快结束
                ac_crawler.max_pages = ac_crawler._stats.pages_crawled + throttler.concurrent * 2

        ac_crawler = DeepCrawler(
            fetcher=self._fetcher,
            strategy=self.strategy,
            max_depth=self.max_depth,
            max_pages=self.max_pages,
            same_domain_only=self.same_domain_only,
            allow_subdomain=self.allow_subdomain,
            max_concurrent=throttler.concurrent,
            request_timeout=self.request_timeout,
            filter_chain=self._external_chain,
            on_page_callback=_wrap_on_page,
        )

        result = await ac_crawler.crawl(seed_url)
        return result, saturation
