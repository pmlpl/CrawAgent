"""harness 全链路集成测试（MockLLM 驱动，无真实 API 调用）。

覆盖：
- 创建 session → prompt → 消息树 + usage 字段
- 同一 session 多次 prompt → usage_total 累计
- task_notes 注入为消息尾部 system 消息（不改缓存前缀）
- loop._record_usage 对带 usage_metadata 的响应做累计（含缓存字段）
"""

import asyncio

import pytest

from crawagent.harness import (
    CrawlHarness, SessionManager, CrawlHooks, create_default_tools,
)
from crawagent.harness.types import TokenUsage


@pytest.fixture()
def mock_llm_env(monkeypatch):
    """强制 mock 模式，避免测试请求真实 LLM API。"""
    from crawagent.config.settings import get_settings
    from crawagent.llm.factory import get_factory
    monkeypatch.setattr(get_settings(), "mock_mode", True)
    get_factory().reload()
    yield


def _build_harness() -> CrawlHarness:
    """内存 SQLite 驱动的 CrawlHarness（不连 MySQL，不写数据库）。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    mgr = SessionManager.__new__(SessionManager)
    mgr._engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    registry = create_default_tools(output_dir="./output")
    return CrawlHarness(
        session_manager=mgr,
        hooks=CrawlHooks(),
        tools=registry.list_tools(),
        tool_executors=registry.get_all_executors(),
    )


def test_harness_prompt_basic_flow(mock_llm_env):
    """基础链路：创建 session → prompt → 消息树 + usage 字段。"""
    async def _run():
        harness = _build_harness()
        try:
            await harness._session_manager.initialize()
            session = await harness.create_session(name="e2e")

            result = await harness.prompt(session.session_id, "你好，请介绍一下你自己")

            assert result.kind == "completed", result.error
            assert result.data is not None
            assert "usage" in result.data  # 本次 token 用量
            assert isinstance(result.data["usage"], dict)

            # 消息树：user + assistant 都存在
            entries = await session.get_entries(limit=100, order="asc")
            roles = [e.role for e in entries]
            assert "user" in roles
            assert "assistant" in roles
        finally:
            await harness.close()

    asyncio.run(_run())


def test_harness_usage_total_accumulates(mock_llm_env):
    """同一 session 多次 prompt → usage_total 存在且字段单调不减。"""
    async def _run():
        harness = _build_harness()
        try:
            await harness._session_manager.initialize()
            session = await harness.create_session(name="e2e-usage")

            r1 = await harness.prompt(session.session_id, "第一轮对话")
            r2 = await harness.prompt(session.session_id, "第二轮对话")

            u1 = r1.data["usage_total"]
            u2 = r2.data["usage_total"]
            assert "prompt_tokens" in u1 and "completion_tokens" in u1
            # 累计值单调不减（MockLLM 无 usage 时都为 0，仍保持键结构）
            assert u2["total_tokens"] >= u1["total_tokens"]
            assert u2["prompt_tokens"] >= u1["prompt_tokens"]

            # get_session_usage 与 usage_total 一致
            usage = harness.get_session_usage(session.session_id)
            assert usage.total_tokens == u2["total_tokens"]
        finally:
            await harness.close()

    asyncio.run(_run())


def test_harness_task_notes_tail_system_message(mock_llm_env):
    """task_notes 注入为消息尾部 system 消息（transient turn-injection）。"""
    from crawagent.harness.types import HookEvent

    captured: dict = {}

    async def _on_before_request(ctx):
        captured["messages"] = ctx.get("messages", [])
        return None

    async def _run():
        harness = _build_harness()
        try:
            await harness._session_manager.initialize()
            harness.hooks.on(HookEvent.BEFORE_REQUEST, _on_before_request)
            session = await harness.create_session(name="e2e-notes")

            notes = "本次爬取任务的资源预算（严格遵守）：\n- 最多抓取 5 个页面（不要超过）"
            result = await harness.prompt(session.session_id, "爬取这个网站", task_notes=notes)
            assert result.kind == "completed", result.error

            # 发给 LLM 的消息中：首条为稳定 system 前缀，末条为 task_notes
            msgs = captured.get("messages", [])
            assert msgs, "BEFORE_REQUEST hook 未捕获到消息"
            first, last = msgs[0], msgs[-1]
            assert first.type == "system"
            assert "CrawAgent" in first.content  # 前缀不受 task_notes 影响
            assert last.type == "system"
            assert "最多抓取 5 个页面" in last.content
            assert first.content != last.content  # 前缀与 task_notes 分离
        finally:
            await harness.close()

    asyncio.run(_run())


def test_loop_record_usage_accumulates():
    """loop._record_usage 对带 usage_metadata 的响应做累计（含缓存命中/未命中）。"""
    from crawagent.harness.loop import CrawlLoop

    loop = CrawlLoop.__new__(CrawlLoop)
    loop._token_usage = TokenUsage()

    class FakeResp:
        usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        response_metadata = {
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_cache_hit_tokens": 90,
                "prompt_cache_miss_tokens": 10,
            }
        }

    class FakeLLM:
        model_name = "mock-model"

    loop._record_usage(FakeResp(), FakeLLM())
    loop._record_usage(FakeResp(), FakeLLM())

    assert loop._token_usage.prompt_tokens == 200
    assert loop._token_usage.completion_tokens == 100
    assert loop._token_usage.cache_hit_tokens == 180
    assert loop._token_usage.cache_miss_tokens == 20
    assert round(loop._token_usage.hit_rate, 2) == 0.9
    assert loop._token_usage.model == "mock-model"
