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
    RegexExtractor, JsonLdExtractor, MetaExtractor, TableExtractor, JsonCssExtractor,
    CompositeExtractor, ExtractionContext, create_dynamic_schema, DEFAULT_EXTRACTOR,
)
from crawagent.core.content_filter import (
    RelevantContentFilter, PruningContentFilter, BM25ContentFilter,
    LLMContentFilter, create_content_filter,
)
from crawagent.core.chunking import (
    ChunkingStrategy, IdentityChunking, RegexChunking, SentenceChunking,
    FixedSizeChunking, SlidingWindowChunking, OverlappingWindowChunking,
    RecursiveChunking, create_chunker,
)
from crawagent.core.markdown_generator import MarkdownGenerator, html_to_clean_markdown
from crawagent.core.retriever import BaseRetriever, SQLiteFTS5Retriever, create_retriever, SearchResult
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
    "RegexExtractor", "JsonLdExtractor", "MetaExtractor", "TableExtractor", "JsonCssExtractor",
    "CompositeExtractor", "ExtractionContext", "create_dynamic_schema", "DEFAULT_EXTRACTOR",
    # Content Filter（P2）
    "RelevantContentFilter", "PruningContentFilter", "BM25ContentFilter",
    "LLMContentFilter", "create_content_filter",
    # Chunking（P2）
    "ChunkingStrategy", "IdentityChunking", "RegexChunking", "SentenceChunking",
    "FixedSizeChunking", "SlidingWindowChunking", "OverlappingWindowChunking",
    "RecursiveChunking", "create_chunker",
    # Markdown（P2）
    "MarkdownGenerator", "html_to_clean_markdown",
    # Retriever
    "BaseRetriever", "SQLiteFTS5Retriever", "create_retriever", "SearchResult",
    # Executor
    "CrawlExecutor", "get_executor",
]
