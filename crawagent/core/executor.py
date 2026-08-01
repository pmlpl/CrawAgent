from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from crawagent.core.fetcher import Fetcher
from crawagent.core.frontier import SQLiteFrontier
from crawagent.core.extractor import CompositeExtractor, DEFAULT_EXTRACTOR
from crawagent.core.retriever import SQLiteFTS5Retriever
from crawagent.core.models import (
    CrawlJob, CrawlPlan, CrawlResult, ExtractedItem, SiteAnalysis
)
from crawagent.config.settings import get_settings
from loguru import logger


class CrawlExecutor:
    """
    爬取执行器：纯代码实现，不调用 LLM
    负责：前沿调度、抓取、提取、存储、去重
    """

    def __init__(
        self,
        fetcher: Optional[Fetcher] = None,
        frontier: Optional[SQLiteFrontier] = None,
        extractor: Optional[CompositeExtractor] = None,
        retriever: Optional[SQLiteFTS5Retriever] = None,
    ):
        self.fetcher = fetcher
        self.frontier = frontier
        self.extractor = extractor or DEFAULT_EXTRACTOR
        self.retriever = retriever
        self._running = False
        self._stats = {
            "pages_crawled": 0,
            "items_extracted": 0,
            "errors": 0,
            "start_time": 0,
            "end_time": 0,
        }

    async def initialize(self) -> None:
        """初始化组件"""
        settings = get_settings()
        
        if not self.frontier:
            self.frontier = SQLiteFrontier(settings.frontier_db_path)
            await self.frontier.initialize()
        
        if not self.fetcher:
            self.fetcher = Fetcher(
                frontier=self.frontier,
                per_domain_rate=settings.per_domain_rate,
                max_concurrent=settings.max_concurrent,
                default_timeout=settings.request_timeout,
                max_retries=settings.max_retries,
                proxy_pool=settings.proxy_pool,
                custom_uas=settings.custom_user_agents,
                impersonate=settings.impersonate,
                use_curl_cffi=settings.use_curl_cffi,
            )
        
        if not self.retriever:
            self.retriever = SQLiteFTS5Retriever(settings.retriever_db_path)

    async def run(self, job: CrawlJob) -> Dict[str, Any]:
        """运行爬取任务"""
        if not self._running:
            await self.initialize()
            self._running = True

        self._stats = {
            "pages_crawled": 0,
            "items_extracted": 0,
            "errors": 0,
            "start_time": time.time(),
            "end_time": 0,
        }

        job.status = "running"
        job.stats = self._stats
        plan = job.plan
        site_analysis = job.site_analysis

        # 种子 URL 入队
        for url in plan.seed_urls:
            await self.frontier.add_url(
                url=url,
                depth=0,
                parent_url="",
                priority=10,
                metadata={"job_id": job.id},
            )

        # 主循环
        while self._running and self._stats["pages_crawled"] < plan.max_pages:
            # 弹出 URL
            records = await self.frontier.pop_next(limit=10)
            if not records:
                break

            # 并发抓取
            urls = [r.url for r in records]
            results = await self.fetcher.fetch_batch(urls)

            # 处理结果
            all_items = []
            for record, result in zip(records, results):
                await self._process_result(record, result, plan, site_analysis, job.id)
            
            # 检查是否完成
            if self._stats["pages_crawled"] >= plan.max_pages:
                break

        self._stats["end_time"] = time.time()
        job.stats = self._stats
        job.status = "done"

        return {
            "completed": True,
            "stats": self._stats,
            "items": await self._get_job_items(job.id),
        }

    async def _process_result(
        self,
        record,
        result: CrawlResult,
        plan: CrawlPlan,
        site_analysis: Optional[SiteAnalysis],
        job_id: str,
    ) -> None:
        """处理单个抓取结果"""
        url = record.url
        
        if result.success and result.html:
            self._stats["pages_crawled"] += 1
            
            # 内容去重
            content_hash = result.metadata.get("content_hash")
            if content_hash and await self.frontier.is_duplicate_content(content_hash):
                logger.debug(f"内容重复，跳过: {url}")
                await self.frontier.mark_done(url, success=True)
                return
            
            if content_hash:
                await self.frontier.add_content_hash(content_hash, url)
            
            # 提取结构化数据
            items = []
            if site_analysis and site_analysis.selectors.item:
                items = self.extractor.extract(
                    url=url,
                    html=result.html,
                    selectors=site_analysis.selectors,
                    target_schema=ExtractedItem,
                    base_url=result.url,
                )
            else:
                # 兜底提取
                items = self.extractor.extract(
                    url=url,
                    html=result.html,
                    selectors=site_analysis.selectors if site_analysis else None,
                    target_schema=ExtractedItem,
                    base_url=result.url,
                )
            
            # 保存提取结果
            if items:
                await self._save_items(job_id, items)
                self._stats["items_extracted"] += len(items)
            
            # 索引到检索器
            for item in items:
                await self.retriever.add([{
                    "id": item.url,
                    "url": item.url,
                    "title": item.title,
                    "content": item.content[:5000],
                    "metadata": item.metadata,
                }])
            
            # 发现新链接（仅深度 < max_depth）
            if record.depth < 3:
                new_urls = []
                for link_url, link_text in result.links[:50]:
                    if self._should_follow(link_url, plan):
                        new_urls.append({
                            "url": link_url,
                            "depth": record.depth + 1,
                            "parent_url": url,
                            "priority": record.priority - 1,
                            "metadata": {"job_id": job_id, "anchor_text": link_text},
                        })
                if new_urls:
                    await self.frontier.add_urls_batch(new_urls)
            
            # 标记完成
            await self.frontier.mark_done(url, success=True, new_urls=[])
            
        else:
            # 失败处理
            self._stats["errors"] += 1
            will_retry = record.retry_count < 3
            await self.frontier.mark_failed(url, result.error or "Unknown error", will_retry)

    def _should_follow(self, url: str, plan: CrawlPlan) -> bool:
        """判断是否跟进链接"""
        # 简单过滤
        if any(ext in url.lower() for ext in [".pdf", ".jpg", ".png", ".css", ".js", ".ico", ".woff"]):
            return False
        # 同域名优先
        return True

    async def _save_items(self, job_id: str, items: List[ExtractedItem]) -> int:
        """保存提取项"""
        return await self.frontier.save_items(job_id, items)

    async def _get_job_items(self, job_id: str) -> List[Dict]:
        """获取任务的所有提取项"""
        items = await self.frontier.get_items(job_id, limit=1000)
        return items

    async def stop(self) -> None:
        """停止执行"""
        self._running = False
        if self.fetcher:
            await self.fetcher.close()
        if self.frontier:
            await self.frontier.close()

    def get_stats(self) -> Dict:
        return self._stats.copy()


# 单例
_executor = None

def get_executor() -> CrawlExecutor:
    global _executor
    if _executor is None:
        _executor = CrawlExecutor()
    return _executor