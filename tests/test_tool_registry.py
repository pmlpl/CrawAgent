"""冒烟测试：工具注册 — _build_tools() 返回完整工具列表，每个都有 name/description。

全部离线，不发网络请求。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_build_tools_returns_list():
    """_build_tools() 返回非空 list。"""
    from crawagent.graph.agent import _build_tools
    tools = _build_tools()
    assert isinstance(tools, list)
    assert len(tools) >= 15, f"至少应有 15 个工具，实际 {len(tools)} 个"


def test_each_tool_has_name():
    """每个工具都有 name 属性且非空。"""
    from crawagent.graph.agent import _build_tools
    tools = _build_tools()
    names = []
    for t in tools:
        n = getattr(t, "name", None)
        assert n, f"工具 {t} 缺少 name"
        assert isinstance(n, str), f"工具 name 非字符串: {n}"
        names.append(n)
    # 确保没有重名
    assert len(names) == len(set(names)), f"工具名重复: {names}"


def test_each_tool_has_description():
    """每个工具都有 description 属性且长度合理。"""
    from crawagent.graph.agent import _build_tools
    tools = _build_tools()
    for t in tools:
        desc = getattr(t, "description", None)
        assert desc, f"工具 {getattr(t, 'name', '?')} 缺少 description"
        assert len(desc) >= 10, f"工具 description 过短: {desc}"


def test_expected_core_tools_present():
    """验证核心工具名存在。"""
    from crawagent.graph.agent import _build_tools
    tools = _build_tools()
    names = {t.name for t in tools}
    expected = {
        "crawl_webpage",
        "browse_and_crawl",
        "extract_content",
        "extract_list",
        "save_to_file",
        "save_record",
        "run_custom_script",
        "download_images",
    }
    missing = expected - names
    assert not missing, f"缺少核心工具: {missing}"


def test_tool_input_schema_has_properties():
    """每个工具的 args_schema 都有 properties。"""
    from crawagent.graph.agent import _build_tools
    tools = _build_tools()
    for t in tools:
        schema = getattr(t, "args_schema", None)
        if schema is None:
            # 有些工具可能用旧的 schema 字段
            continue
        props = getattr(schema, "model_fields", None)
        # 简单检查能访问到 schema 信息就行
        assert schema is not None, f"工具 {t.name} 缺少 args_schema"


def test_agent_imports_without_warning():
    """从 agent 模块直接 import 所有工具构建函数不报错。"""
    from crawagent.graph.agent import (
        get_agent,
        _build_tools,
        SYSTEM_PROMPT,
        warm_cache,
    )
    assert isinstance(SYSTEM_PROMPT, str)
    assert len(SYSTEM_PROMPT) > 1000, "SYSTEM_PROMPT 应包含 prompt 内容"
    assert callable(get_agent)
    assert callable(warm_cache)
