"""P2-5 提示词 + P2-6 Token 用量测试。"""

from crawagent.llm.prompts import (
    PROMPT_EXTRACT_JSON,
    PROMPT_FILTER_CONTENT,
    build_json_schema,
    pydantic_to_json_schema,
)
from crawagent.llm.usage import TokenUsageTracker
from crawagent.harness.types import TokenUsage
from pydantic import BaseModel


def test_build_json_schema_simple():
    schema = build_json_schema({"title": "标题", "price": "float"})
    assert schema["type"] == "object"
    assert schema["properties"]["title"]["type"] == "string"
    assert schema["properties"]["price"]["type"] == "number"
    assert schema["required"] == ["title", "price"]


def test_build_json_schema_optional():
    schema = build_json_schema({
        "title": {"type": "str", "description": "标题", "required": True},
        "note": {"type": "optional_str", "description": "备注"},
    })
    assert schema["required"] == ["title"]
    assert schema["properties"]["note"]["type"] == "string"


def test_pydantic_to_json_schema():
    class Item(BaseModel):
        title: str
        price: float = 0.0

    schema = pydantic_to_json_schema(Item)
    assert schema["title"] == "Item"
    assert "title" in schema["properties"]


def test_prompts_formattable():
    p1 = PROMPT_FILTER_CONTENT.format(query="q", content="c")
    assert "q" in p1
    p3 = PROMPT_EXTRACT_JSON.format(url="u", schema="{}", html="h")
    assert "u" in p3


def test_token_usage_tracker():
    tracker = TokenUsageTracker()
    tracker.record("gpt-4o-mini", 100, 20)
    tracker.record("gpt-4o-mini", 50, 10)
    snapshot = tracker.snapshot()
    assert snapshot["gpt-4o-mini"]["prompt_tokens"] == 150
    assert snapshot["gpt-4o-mini"]["completion_tokens"] == 30
    total = tracker.totals()
    assert total.total_tokens == 180
    tracker.reset()
    assert tracker.totals().total_tokens == 0


def test_token_usage_from_metadata():
    tracker = TokenUsageTracker()

    class FakeResponse:
        usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        response_metadata = {"model_name": "mock-model"}

    tracker.record_response(FakeResponse())
    assert tracker.totals().total_tokens == 15
    assert tracker.snapshot()["mock-model"]["prompt_tokens"] == 10


def test_usage_harness_type_compat():
    u = TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3, model="m")
    assert u.total_tokens == 3


def test_token_usage_cache_fields():
    """TokenUsage 缓存字段 + 命中率 + 费用。"""
    u = TokenUsage(prompt_tokens=100, completion_tokens=50, cache_hit_tokens=80, cache_miss_tokens=20)
    assert u.hit_rate == 0.8
    # 命中 80*0.02 + 未命中 20*1 + 输出 50*2 = 1.6+20+100 = 121.6 元 / 1e6
    assert u.cost_yuan() == round(121.6 / 1e6, 4)
    # 无缓存信息时 hit_rate=0，费用按 prompt_tokens 全算 miss
    u2 = TokenUsage(prompt_tokens=1000, completion_tokens=0)
    assert u2.hit_rate == 0.0
    assert u2.cost_yuan() == round(1000.0 / 1e6, 4)


def test_extract_usage_deepseek_top_level_cache():
    """DeepSeek 顶层 prompt_cache_* 字段提取。"""
    from crawagent.llm.usage import _extract_usage_from_response

    class FakeResponse:
        usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        response_metadata = {
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_cache_hit_tokens": 90,
                "prompt_cache_miss_tokens": 10,
            },
            "model_name": "deepseek-v4-flash",
        }

    u = _extract_usage_from_response(FakeResponse())
    assert u["prompt_tokens"] == 100
    assert u["cache_hit_tokens"] == 90
    assert u["cache_miss_tokens"] == 10


def test_extract_usage_openai_cached_tokens():
    """OpenAI 标准 prompt_tokens_details.cached_tokens 提取。"""
    from crawagent.llm.usage import _extract_usage_from_response

    class FakeResponse:
        usage_metadata = {}
        response_metadata = {
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 70},
            },
        }

    u = _extract_usage_from_response(FakeResponse())
    assert u["cache_hit_tokens"] == 70
    assert u["cache_miss_tokens"] == 30


def test_token_usage_tracker_cache_accumulate():
    """TokenUsageTracker 累计缓存字段。"""
    tracker = TokenUsageTracker()
    tracker.record("m", 100, 20, cache_hit_tokens=60, cache_miss_tokens=40)
    tracker.record("m", 50, 10, cache_hit_tokens=50, cache_miss_tokens=0)
    snap = tracker.snapshot()["m"]
    assert snap["cache_hit_tokens"] == 110
    assert snap["cache_miss_tokens"] == 40
