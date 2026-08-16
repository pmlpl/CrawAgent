"""冒烟测试：核心模块可导入、Agent 可构建、关键纯函数正确。

全部离线，不发网络请求；防止重构后再把「构建失败 / 工具签名破坏」这类低级错误放过去。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_settings_load():
    from crawagent.config.settings import get_settings
    s = get_settings()
    assert s.openai_base_url.startswith("http")
    assert s.default_model


def test_agent_builds():
    """get_agent() 成功 = LLM + 12 个工具装配正确（构造期无网络请求）。"""
    from crawagent.graph.agent import get_agent
    agent = get_agent()
    assert hasattr(agent, "stream") and hasattr(agent, "invoke")


def test_social_parsers():
    from crawagent.tools.social_tool import _extract_bvid, _extract_aweme_id, _mixin_key
    assert _extract_bvid("https://www.bilibili.com/video/BV1xx411c7mD?p=1") == "BV1xx411c7mD"
    assert _extract_aweme_id("https://www.douyin.com/video/7123456789012345678") == "7123456789012345678"
    assert _mixin_key(
        "7cd084941338484aae1ad9425b84077c",
        "4932caff0ff746eab6f01bf08b70ac45",
    ) == "ea1db124af3c7062474693fa704f4ff8"


def test_confidence_low_when_short():
    from crawagent.tools.confidence import evaluate_confidence
    r = evaluate_confidence(content="x" * 50, title="")
    assert r.should_upgrade is True


def test_usage_from_message():
    from crawagent.observability.metrics import usage_from_message
    from langchain_core.messages import AIMessage
    m = AIMessage(content="hi", usage_metadata={
        "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
        "input_token_details": {"cache_read": 4},
    })
    u = usage_from_message(m)
    assert u["prompt_tokens"] == 10
    assert u["prompt_cache_hit_tokens"] == 4
