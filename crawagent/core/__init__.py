"""
CrawAgent Core - 核心组件
"""

from crawagent.core.models import (
    PageType, DataSource, AntiBotSign, PaginationType,
    Pagination, Selectors, SiteAnalysis, CrawlPlan, CrawlJob,
    CrawlResult, ExtractedItem, SearchResult, AgentState,
)
from crawagent.core.fetcher import Fetcher, DomainLimiter, ProxyRotator, UserAgentRotator
from crawagent.core.frontier import SQLiteFrontier, URLRecord, canonicalize_url, url_fingerprint, content_fingerprint
from crawagent.core.extractor import (
    BaseExtractor, SelectolaxExtractor, LxmlExtractor, LLMExtractor,
    CompositeExtractor, ExtractionContext, create_dynamic_schema, DEFAULT_EXTRACTOR
)
from crawagent.core.retriever import BaseRetriever, SQLiteFTS5Retriever, VectorRetriever, create_retriever, SearchResult
from crawagent.core.executor import CrawlExecutor, get_executor

__all__ = [
    # Models
    "PageType", "DataSource", "AntiBotSign", "PaginationType",
    "Pagination", "Selectors", "SiteAnalysis", "CrawlPlan", "CrawlJob",
    "CrawlResult", "ExtractedItem", "SearchResult", "AgentState",
    # Fetcher
    "Fetcher", "DomainLimiter", "ProxyRotator", "UserAgentRotator",
    # Frontier
    "SQLiteFrontier", "URLRecord", "canonicalize_url", "url_fingerprint", "content_fingerprint",
    # Extractor
    "BaseExtractor", "SelectolaxExtractor", "LxmlExtractor", "LLMExtractor",
    "CompositeExtractor", "ExtractionContext", "create_dynamic_schema", "DEFAULT_EXTRACTOR",
    # Retriever
    "BaseRetriever", "SQLiteFTS5Retriever", "VectorRetriever", "create_retriever", "SearchResult",
    # Executor
    "CrawlExecutor", "get_executor",
]