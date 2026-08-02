"""P0 缓存稳定前缀测试：system prompt 字节级一致，动态内容不改前缀。

对齐 Reasonix cache_shape.go 思路：
- system prompt 字节级固定（工具 schema 排序规范化）
- 动态内容（task_notes）走 transient turn-injection，不触碰前缀
"""

import asyncio

from crawagent.harness.system_prompt import SystemPromptAssembler
from crawagent.harness.session import SessionManager
from crawagent.harness.types import CrawlToolDef, ToolExecMode


def _make_tool(name: str, desc: str) -> CrawlToolDef:
    return CrawlToolDef(
        name=name,
        description=desc,
        parameters={"type": "object", "properties": {}, "required": []},
        exec_mode=ToolExecMode.SEQUENTIAL,
        replay_safe=True,
    )


def test_assembler_sorts_tools():
    """工具乱序传入 → 组装结果一致（前缀字节稳定）。"""
    t1 = _make_tool("zebra", "Z 工具")
    t2 = _make_tool("apple", "A 工具")
    t3 = _make_tool("mango", "M 工具")

    a = SystemPromptAssembler().assemble(tools=[t1, t2, t3])
    b = SystemPromptAssembler().assemble(tools=[t3, t1, t2])
    assert a == b
    # 描述按 name 排序出现
    assert a.index("apple:") < a.index("mango:") < a.index("zebra:")


def test_assembler_without_tools_stable():
    """无工具传入时结果一致且不含工具描述。"""
    a = SystemPromptAssembler().assemble(tools=None)
    b = SystemPromptAssembler().assemble(tools=[])
    assert a == b
    assert "apple" not in a


def _make_sqlite_session_manager() -> SessionManager:
    """构造内存 SQLite 驱动的 SessionManager（绕过 MySQL pool_size 参数）。"""
    from sqlalchemy.ext.asyncio import create_async_engine
    mgr = SessionManager.__new__(SessionManager)
    mgr._engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    return mgr


def test_loop_system_prompt_byte_stable():
    """同一工具集构造的两个 loop：system prompt 字节一致，task_notes 不进入前缀。"""
    from crawagent.harness.hooks import CrawlHooks
    from crawagent.harness.loop import CrawlLoop
    from crawagent.harness.tools import create_default_tools

    async def _run():
        mgr = _make_sqlite_session_manager()
        await mgr.initialize()
        registry = create_default_tools(output_dir="./output")
        tools = registry.list_tools()

        s1 = await mgr.create_session("s1")
        s2 = await mgr.create_session("s2")

        loop1 = CrawlLoop(s1, CrawlHooks(), tools, task_notes="预算A：最多抓 5 页")
        loop2 = CrawlLoop(s2, CrawlHooks(), tools, task_notes="预算B：最多抓 9 页")

        assert loop1._system_prompt == loop2._system_prompt
        # task_notes 走消息尾部注入，绝不改写前缀
        assert "预算" not in loop1._system_prompt
        assert "预算" not in loop2._system_prompt
        # 新接入的 deep_crawl 工具已进入缓存前缀
        assert "deep_crawl" in loop1._system_prompt
        await mgr._engine.dispose()

    asyncio.run(_run())


def test_loop_system_prompt_sorted_across_registries():
    """不同注册顺序的 tools → 同一 loop 前缀仍字节一致（排序兜底）。"""
    from crawagent.harness.loop import CrawlLoop
    from crawagent.harness.hooks import CrawlHooks

    t1 = _make_tool("b_tool", "B 描述")
    t2 = _make_tool("a_tool", "A 描述")

    async def _run():
        mgr = _make_sqlite_session_manager()
        await mgr.initialize()
        s1 = await mgr.create_session("s1")
        s2 = await mgr.create_session("s2")

        loop1 = CrawlLoop(s1, CrawlHooks(), [t1, t2])
        loop2 = CrawlLoop(s2, CrawlHooks(), [t2, t1])
        assert loop1._system_prompt == loop2._system_prompt
        await mgr._engine.dispose()

    asyncio.run(_run())
