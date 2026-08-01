"""深度爬取引擎（P6-3）

参考 Crawl4AI deep_crawling/ 系列设计：
- BFS（广度优先）：按层级爬，先抓所有一层链接 → 再抓二层
- DFS（深度优先）：顺着一条链爬到底再回退
- Best-First：按打分（路径相关性、URL 特征等）优先爬"最有价值"的页面

核心组件：
- DeepCrawlStrategy（枚举）
- DeepCrawler（调度器）：集成 Fetcher + FilterChain + extract 链接提取
- CrawledPage（单页产物，供后续提取）

深爬不直接依赖 Agent LLM，只是高效遍历并保存原始产物。
下游 extractor / 上层 supervisor / Agent 再决定如何消费。
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from heapq import heappop, heappush
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from crawagent.core.models import CrawlResult
from crawagent.core.filters import FilterChain, normalize_url, SameDomainFilter, MaxDepthFilter, ExtensionFilter


class DeepCrawlStrategy(str, Enum):
    """深度爬取策略。"""
    BFS = "bfs"
    DFS = "dfs"
    BEST_FIRST = "best_first"


@dataclass
class CrawledPage:
    """单页爬取产物。"""
    url: str
    status_code: int
    depth: int
    html: str = ""
    content_type: str = ""
    content_length: int = 0
    title: str = ""
    out_links: List[str] = field(default_factory=list)
    error: str = ""
    crawled_at: float = field(default_factory=time.time)
    parent_url: str = ""


@dataclass
class DeepCrawlStats:
    """深爬统计。"""
    started_at: float = 0.0
    finished_at: float = 0.0
    pages_crawled: int = 0
    urls_enqueued: int = 0
    urls_filtered: int = 0
    urls_deduped: int = 0
    errors: int = 0
    max_depth_reached: int = 0
    filter_stats: Dict[str, int] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.finished_at - self.started_at) if self.finished_at else max(0.0, time.time() - self.started_at)


@dataclass
class DeepCrawlResult:
    """深爬整体结果。"""
    strategy: DeepCrawlStrategy
    seed_url: str
    pages: List[CrawledPage]
    stats: DeepCrawlStats

    def to_summary(self) -> str:
        s = self.stats
        lines = [
            f"[DeepCrawl] {self.strategy.value} 种子: {self.seed_url}",
            f"  爬取 {s.pages_crawled} 页 / 排队 {s.urls_enqueued} / 过滤 {s.urls_filtered} / 去重 {s.urls_deduped} / 错误 {s.errors}",
            f"  最大深度: {s.max_depth_reached}, 耗时 {s.duration_seconds:.1f}s",
        ]
        if s.filter_stats:
            lines.append("  过滤器拒绝分布: " + ", ".join(f"{k}={v}" for k, v in s.filter_stats.items()))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 打分函数（Best-First 用）
# ---------------------------------------------------------------------------

def default_url_score(url: str, depth: int = 0, parent_url: str = "") -> float:
    """默认 URL 打分（越高越优先）。

    规则：
    1. 深度浅的优先（-depth*10）
    2. 路径包含关键词加分：docs/doc/article/blog/guide/tutorial → +10
    3. 纯主页（path=/""/"") 额外加 5（稳定入口页）
    4. 带 query 参数减 3（参数页通常是列表过滤页，价值低）
    5. 锚点页（fragment 已被 normalize 去掉，这里基本不触发）
    """
    score = 0.0
    score -= depth * 10.0
    try:
        path = (urlparse(url).path or "/").lower()
    except Exception:
        path = "/"
    if path in ("/", ""):
        score += 5.0
    keywords = ["docs", "doc", "documentation", "article", "blog", "guide", "tutorial", "learn", "manual", "reference"]
    for kw in keywords:
        if f"/{kw}/" in f"{path}/":
            score += 10.0
            break
    try:
        if urlparse(url).query:
            score -= 3.0
    except Exception:
        pass
    return -score  # heapq 是最小堆，取负让大分优先


# ---------------------------------------------------------------------------
# 链接提取
# ---------------------------------------------------------------------------

def extract_links(base_url: str, html: str) -> List[str]:
    """从 HTML 中提取 <a href> 链接，转为绝对 URL。"""
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    links: List[str] = []
    seen: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or not isinstance(href, str):
            continue
        if href.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "about:")):
            continue
        try:
            abs_url = urljoin(base_url, href.strip())
        except Exception:
            continue
        if not abs_url.startswith(("http://", "https://")):
            continue
        # 去重（相同页面内重复 href 只保留一个）
        if abs_url in seen:
            continue
        seen.add(abs_url)
        links.append(abs_url)
    return links


# ---------------------------------------------------------------------------
# DeepCrawler 主类
# ---------------------------------------------------------------------------

class DeepCrawler:
    """深度爬取调度器。

    核心：URL 队列（按策略）+ 去重 + 过滤 + 并发抓取。

    用法：
        from crawagent.core.fetcher import Fetcher
        from crawagent.core.deep_crawl import DeepCrawler, DeepCrawlStrategy

        crawler = DeepCrawler(
            fetcher=Fetcher(),
            strategy=DeepCrawlStrategy.BFS,
            max_depth=3,
            max_pages=50,
            same_domain_only=True,
        )
        result = await crawler.crawl("https://docs.python.org/3/")
        print(result.to_summary())
    """

    def __init__(
        self,
        fetcher: Any = None,
        strategy: DeepCrawlStrategy = DeepCrawlStrategy.BFS,
        max_depth: int = 3,
        max_pages: int = 50,
        max_queue: int = 500,
        same_domain_only: bool = True,
        allow_subdomain: bool = True,
        max_concurrent: int = 5,
        request_timeout: float = 20.0,
        filter_chain: Optional[FilterChain] = None,
        url_scorer: Optional[Callable[[str, int, str], float]] = None,
        on_page_callback: Optional[Callable[[CrawledPage], Awaitable[None]]] = None,
    ) -> None:
        self._fetcher = fetcher  # 允许延迟注入 Fetcher（内部会自动建临时 httpx 实例）
        self.strategy = strategy
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.max_queue = max_queue
        self.same_domain_only = same_domain_only
        self.allow_subdomain = allow_subdomain
        self.max_concurrent = max_concurrent
        self.request_timeout = request_timeout
        self._url_scorer = url_scorer or default_url_score
        self._on_page = on_page_callback

        # FilterChain（same_domain + max_depth + extension 默认已加，外部传入的会合并）
        self._external_chain = filter_chain or FilterChain([])
        # 内部默认 FilterChain（深度=0 时先放行，由 metadata 过滤）
        self._default_filters = FilterChain([])

        # 运行时状态
        self._stats = DeepCrawlStats()
        self._seen: Set[str] = set()

    # ---- 构造默认 FilterChain ----

    def _build_chain(self, seed_url: str) -> FilterChain:
        """组合 默认 Filter + 外部传入 Filter。"""
        chain = FilterChain()
        if self.same_domain_only:
            chain.add(SameDomainFilter(seed_url, allow_subdomain=self.allow_subdomain))
        chain.add(MaxDepthFilter(max_depth=self.max_depth))
        chain.add(ExtensionFilter())  # 过滤图片/视频/附件等非网页
        # 外部链（不覆盖默认，追加）
        for f in self._external_chain._filters:
            chain.add(f)
        return chain

    # ---- 抓取单页（内部调用 Fetcher 或内置 httpx）----

    async def _fetch_one(self, url: str) -> Tuple[Optional[str], int, str, str, int]:
        """返回 (html, status_code, content_type, error, content_length)"""
        # 优先走外部 Fetcher
        if self._fetcher is not None:
            try:
                import inspect
                # Fetcher.fetch 需要上下文管理器：用 _fetch_httpx 模式，走 Fetcher 的 fetch 方法
                fetcher = self._fetcher
                # 兼容两种调用：有 fetch(url) 方法直接调用，或自己实现
                if hasattr(fetcher, "fetch") and callable(fetcher.fetch):
                    result = await fetcher.fetch(url)  # type: ignore[attr-defined]
                    if isinstance(result, CrawlResult):
                        return result.html, result.status_code, "", result.error or "", len(result.html or "")
            except Exception as e:
                return None, 0, "", str(e), 0

        # 兜底：直接 httpx
        try:
            import httpx
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=self.request_timeout
            ) as client:
                resp = await client.get(url)
                html = resp.text
                status = resp.status_code
                ct = resp.headers.get("content-type", "")
                return html, status, ct, "", len(html)
        except Exception as e:
            return None, 0, "", str(e), 0

    # ---- 队列操作（根据策略）----

    def _queue_new(self) -> Any:
        if self.strategy == DeepCrawlStrategy.DFS:
            return []  # list as stack (append/pop)
        elif self.strategy == DeepCrawlStrategy.BEST_FIRST:
            return []  # heapq
        return deque()  # BFS

    def _queue_enqueue(self, queue: Any, item: Tuple[float, int, str, str]) -> None:
        # item = (priority, depth, url, parent)
        if self.strategy == DeepCrawlStrategy.DFS:
            queue.append(item)  # stack: LIFO → 最后加的先 pop
        elif self.strategy == DeepCrawlStrategy.BEST_FIRST:
            heappush(queue, item)
        else:  # BFS
            queue.append(item)

    def _queue_dequeue(self, queue: Any) -> Optional[Tuple[float, int, str, str]]:
        if self.strategy == DeepCrawlStrategy.DFS:
            return queue.pop() if queue else None
        elif self.strategy == DeepCrawlStrategy.BEST_FIRST:
            return heappop(queue) if queue else None
        else:  # BFS
            return queue.popleft() if queue else None

    def _queue_size(self, queue: Any) -> int:
        return len(queue)

    # ---- 主流程 ----

    async def crawl(self, seed_url: str) -> DeepCrawlResult:
        """执行深爬，返回结果。"""
        self._stats = DeepCrawlStats(started_at=time.time())
        self._seen.clear()

        chain = self._build_chain(seed_url)

        queue = self._queue_new()
        pages: List[CrawledPage] = []
        sem = asyncio.Semaphore(self.max_concurrent)

        # 入队种子
        seed_norm = normalize_url(seed_url)
        self._stats.urls_enqueued += 1
        self._seen.add(seed_norm)
        score = self._url_scorer(seed_norm, 0, "")
        self._queue_enqueue(queue, (score, 0, seed_norm, ""))

        # worker：并发从队列里取
        in_flight: Set[asyncio.Task] = set()
        # 标记队列是否可能在并发处理
        processed_count = 0
        pages_lock = asyncio.Lock()

        async def _worker(score_s: float, depth_s: int, url_s: str, parent_s: str) -> None:
            nonlocal processed_count
            if processed_count >= self.max_pages:
                return
            async with sem:
                html, status_code, ct, err, clen = await self._fetch_one(url_s)
            page = CrawledPage(
                url=url_s,
                status_code=status_code,
                depth=depth_s,
                html=html or "",
                content_type=ct,
                content_length=clen,
                title=_extract_title(html) if html else "",
                out_links=[],
                error=err,
                parent_url=parent_s,
            )
            if page.depth > self._stats.max_depth_reached:
                self._stats.max_depth_reached = page.depth
            if err or not 200 <= status_code < 400:
                self._stats.errors += 1

            # 提取子链接（仅当 2xx 且内容像 html）
            if html and 200 <= status_code < 300 and (not ct or "html" in ct.lower()):
                out_links = extract_links(url_s, html)
                page.out_links = out_links
                # 对子链接做：normalize → 去重 → filter → 入队
                for link in out_links:
                    if self._queue_size(queue) >= self.max_queue:
                        break
                    n_url = normalize_url(link)
                    if n_url in self._seen:
                        self._stats.urls_deduped += 1
                        continue
                    # 用过滤器判断（depth=depth+1）
                    fr = chain.accept_one(n_url, {"depth": depth_s + 1, "parent_url": url_s})
                    if not fr.accepted:
                        self._stats.urls_filtered += 1
                        continue
                    self._seen.add(n_url)
                    self._stats.urls_enqueued += 1
                    s_score = self._url_scorer(n_url, depth_s + 1, url_s)
                    self._queue_enqueue(queue, (s_score, depth_s + 1, n_url, url_s))

            async with pages_lock:
                pages.append(page)
                processed_count += 1

            if self._on_page is not None:
                try:
                    await self._on_page(page)
                except Exception as cb_e:
                    logger.debug(f"[DeepCrawl] on_page 回调异常: {cb_e}")

        # 主循环：不断从队列取，创建任务
        def _has_work() -> bool:
            return self._queue_size(queue) > 0 or len(in_flight) > 0

        while _has_work():
            # 先把 in_flight 中已完成的收掉
            done = {t for t in in_flight if t.done()}
            for t in done:
                try:
                    await t
                except Exception as e:
                    self._stats.errors += 1
                    logger.debug(f"[DeepCrawl] worker 异常: {e}")
                in_flight.discard(t)

            if processed_count >= self.max_pages:
                # 不再取新的
                if in_flight:
                    await asyncio.sleep(0.02)
                    continue
                break

            # 取到满 concurrency 为止
            while self._queue_size(queue) > 0 and len(in_flight) < self.max_concurrent and processed_count < self.max_pages:
                item = self._queue_dequeue(queue)
                if item is None:
                    break
                _p, depth_u, url_u, parent_u = item
                task = asyncio.create_task(_worker(_p, depth_u, url_u, parent_u))
                in_flight.add(task)

            if in_flight:
                # 等一小段，让事件循环推进
                await asyncio.sleep(0.01)
            elif self._queue_size(queue) == 0:
                break

        # 收尾：最后等待未完成的任务
        if in_flight:
            try:
                results = await asyncio.gather(*in_flight, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        self._stats.errors += 1
            except Exception as e:
                logger.debug(f"[DeepCrawl] gather 异常: {e}")

        # 记录过滤器统计
        self._stats.filter_stats = chain.stats
        self._stats.pages_crawled = len(pages)
        self._stats.finished_at = time.time()

        return DeepCrawlResult(
            strategy=self.strategy,
            seed_url=seed_url,
            pages=pages,
            stats=self._stats,
        )


def _extract_title(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
        if soup.title and soup.title.string:
            return soup.title.string.strip()[:200]
    except Exception:
        pass
    return ""
