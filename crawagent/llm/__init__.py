"""
CrawAgent LLM - 多提供商 LLM 工厂
"""

from crawagent.llm.factory import (
    LLMFactory,
    LLMProvider,
    get_factory,
    get_llm,
    get_llm_with_tools,
)
from crawagent.llm.prompts import (
    SITE_ANALYZER_PROMPT,
    CLASSIFY_PROMPT,
    PLAN_PROMPT,
    DECIDE_PROMPT,
    EXTRACTION_SCHEMA_PROMPT,
    PROMPT_FILTER_CONTENT,
    PROMPT_EXTRACT_BLOCKS,
    PROMPT_EXTRACT_JSON,
    PROMPT_HTML_TO_MARKDOWN,
    PROMPT_CHUNK_SUMMARY,
    PROMPT_JSON_SCHEMA,
    build_json_schema,
    pydantic_to_json_schema,
)
from crawagent.llm.usage import TokenUsageTracker, UsageCallbackHandler, wrap_llm

__all__ = [
    "LLMFactory",
    "LLMProvider",
    "get_factory",
    "get_llm",
    "get_llm_with_tools",
    "SITE_ANALYZER_PROMPT",
    "CLASSIFY_PROMPT",
    "PLAN_PROMPT",
    "DECIDE_PROMPT",
    "EXTRACTION_SCHEMA_PROMPT",
    # P2 提示词
    "PROMPT_FILTER_CONTENT",
    "PROMPT_EXTRACT_BLOCKS",
    "PROMPT_EXTRACT_JSON",
    "PROMPT_HTML_TO_MARKDOWN",
    "PROMPT_CHUNK_SUMMARY",
    "PROMPT_JSON_SCHEMA",
    "build_json_schema",
    "pydantic_to_json_schema",
    # P2 Usage
    "TokenUsageTracker",
    "UsageCallbackHandler",
    "wrap_llm",
]
