from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage


class PageType(str, Enum):
    VIDEO_LIST = "video_list"
    ARTICLE = "article"
    SEARCH_RESULT = "search_result"
    PRODUCT_LIST = "product_list"
    UNKNOWN = "unknown"


class DataSource(str, Enum):
    DOM = "dom"
    XHR_JSON = "xhr_json"
    GRAPHQL = "graphql"
    API = "api"


class AntiBotSign(str, Enum):
    CLOUDFLARE = "cloudflare"
    SLIDER_CAPTCHA = "slider_captcha"
    RECAPTCHA = "recaptcha"
    WAF = "waf"
    RATE_LIMIT = "rate_limit"
    NONE = "none"


class PaginationType(str, Enum):
    PAGE_PARAM = "page_param"
    CURSOR = "cursor"
    OFFSET = "offset"
    INFINITE_SCROLL = "infinite_scroll"
    NONE = "none"


class Pagination(BaseModel):
    type: PaginationType = PaginationType.NONE
    param: Optional[str] = None
    max_page_selector: Optional[str] = None
    cursor_selector: Optional[str] = None
    has_next_selector: Optional[str] = None


class Selectors(BaseModel):
    list_container: str = ""
    item: str = ""
    title: str = ""
    url: str = ""
    extra: Dict[str, Optional[str]] = Field(default_factory=dict)


class SiteAnalysis(BaseModel):
    page_type: PageType = PageType.UNKNOWN
    data_source: DataSource = DataSource.DOM
    selectors: Selectors = Field(default_factory=Selectors)
    api_endpoint: Optional[str] = None
    api_method: Optional[str] = "GET"
    api_headers: Dict[str, str] = Field(default_factory=dict)
    api_params: Dict[str, Any] = Field(default_factory=dict)
    pagination: Pagination = Field(default_factory=Pagination)
    anti_bot_signs: List[AntiBotSign] = Field(default_factory=list)
    confidence: float = 0.0
    raw_evidence: Dict[str, Any] = Field(default_factory=dict)


class CrawlPlan(BaseModel):
    seed_urls: List[str] = Field(default_factory=list)
    target_schema: Dict[str, Any] = Field(default_factory=dict)
    max_pages: int = 100
    max_depth: int = 3
    per_domain_rate: float = 0.5
    require_login: bool = False
    cookies: Optional[Dict[str, str]] = None
    user_instructions: str = ""


class CrawlJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"


class CrawlJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    plan: CrawlPlan
    site_analysis: Optional[SiteAnalysis] = None
    status: Literal["pending", "running", "paused", "done", "failed"] = "pending"
    stats: Dict[str, Any] = Field(default_factory=lambda: {
        "pages_crawled": 0,
        "items_extracted": 0,
        "errors": 0,
        "start_time": None,
        "end_time": None,
    })
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str
    title: str = ""
    content: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    id: str
    title: str
    content: str
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CrawlResult(BaseModel):
    url: str
    status_code: int = 0
    headers: Dict[str, str] = Field(default_factory=dict)
    html: str = ""
    text: str = ""
    title: str = ""
    links: List[tuple[str, str]] = Field(default_factory=list)
    success: bool = False
    error: Optional[str] = None
    strategy: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


# LangGraph State
class AgentState(TypedDict):
    # 用户输入
    user_input: str
    seed_urls: List[str]
    thread_id: str
    max_pages: int
    max_depth: int

    # 当前任务
    current_job: Optional[CrawlJob]
    site_analysis: Optional[SiteAnalysis]
    page_type: str

    # 规划结果
    crawl_plan: Optional[CrawlPlan]
    next_action: str
    decision_reason: str

    # 执行结果
    crawl_results: List[CrawlResult]
    extracted_items: List[ExtractedItem]
    error_message: Optional[str]

    # 对话历史
    messages: List[BaseMessage]

    # 运行日志（用于前端实时展示）
    logs: List[Dict[str, str]]

    # 控制标志
    requires_human: bool
    is_complete: bool


# LLM 工具定义
TOOL_SCHEMAS = {
    "analyze_site": {
        "name": "analyze_site",
        "description": "分析网站结构，识别页面类型、数据来源、选择器、分页等",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标 URL"},
                "wait_for_network_idle": {"type": "boolean", "default": True},
            },
            "required": ["url"],
        },
    },
    "create_crawl_plan": {
        "name": "create_crawl_plan",
        "description": "根据用户需求和网站分析，制定爬取计划",
        "parameters": {
            "type": "object",
            "properties": {
                "seed_urls": {"type": "array", "items": {"type": "string"}},
                "target_fields": {"type": "array", "items": {"type": "string"}},
                "max_pages": {"type": "integer", "default": 50},
                "require_login": {"type": "boolean", "default": False},
            },
            "required": ["seed_urls", "target_fields"],
        },
    },
    "execute_crawl": {
        "name": "execute_crawl",
        "description": "执行爬取任务（内部工具，不直接暴露给 LLM）",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    "handle_anti_bot": {
        "name": "handle_anti_bot",
        "description": "处理反爬挑战，决定升级策略",
        "parameters": {
            "type": "object",
            "properties": {
                "challenge_type": {"type": "string", "enum": ["cloudflare", "captcha", "rate_limit", "waf", "unknown"]},
                "current_strategy": {"type": "string"},
                "retry_count": {"type": "integer"},
            },
            "required": ["challenge_type", "current_strategy"],
        },
    },
}


def serialize_job(job: CrawlJob) -> str:
    return job.model_dump_json()


def deserialize_job(data: str) -> CrawlJob:
    return CrawlJob.model_validate_json(data)