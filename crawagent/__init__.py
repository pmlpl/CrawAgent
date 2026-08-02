"""
CrawAgent - 基于 LLM Agent 的智能爬虫系统

核心模块：
- crawagent.core: 核心组件 (Fetcher, Frontier, Extractor, Retriever)
- crawagent.graph: LangGraph Agent 工作流
- crawagent.llm: 多提供商 LLM 工厂
- crawagent.api: FastAPI 服务
- crawagent.cli: 命令行界面
"""

__version__ = "1.0.0"
__author__ = "海鸥"
__description__ = "基于 LLM Agent 的智能爬虫系统"

# 核心组件导出
from crawagent.core.fetcher import Fetcher
from crawagent.core.frontier import SQLiteFrontier
from crawagent.core.extractor import DEFAULT_EXTRACTOR, CompositeExtractor
from crawagent.core.retriever import SQLiteFTS5Retriever, create_retriever
from crawagent.core.models import (
    SiteAnalysis, CrawlPlan, CrawlJob, CrawlResult, 
    ExtractedItem, PageType, CrawlJobStatus
)
from crawagent.config.settings import get_settings

# Agent 运行器（双轨合一的唯一执行路径：crawagent.harness.CrawlHarness）
from crawagent.config.settings import get_settings

__all__ = [
    "Fetcher",
    "SQLiteFrontier",
    "DEFAULT_EXTRACTOR",
    "CompositeExtractor",
    "SQLiteFTS5Retriever",
    "create_retriever",
    "SiteAnalysis",
    "CrawlPlan",
    "CrawlJob",
    "CrawlResult",
    "ExtractedItem",
    "PageType",
    "CrawlJobStatus",
    "get_settings",
]