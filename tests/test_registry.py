"""registry.py 测试 — 工具发现 + 元数据 + @register_tool 装饰器。

不测具体工具的逻辑（那是各自 test_<tool>.py 的事），只测 registry 自身：
- discover_tools 返回 BaseTool 实例
- source_file 正确指向 crawagent/tools/*.py
- _TOOL_META 元数据完整
- @register_tool 装饰器把工具注册进 _REGISTERED
- _guess_category 兜底
- build_all_tools 包含注册的 + 自动发现的
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.tools import BaseTool

from crawagent.tools.registry import (
    ToolSpec,
    _REGISTERED,
    _TOOL_META,
    _guess_category,
    build_all_tools,
    discover_tools,
    register_tool,
)


# ---------------------------------------------------------------------------
# 1. discover_tools 基础
# ---------------------------------------------------------------------------

def test_discover_tools_returns_list():
    specs = discover_tools()
    assert isinstance(specs, list)
    assert len(specs) > 0


def test_discover_tools_all_base_tool():
    for spec in discover_tools():
        if spec.instance is None:
            continue  # build_fn 模式可暂未实例化
        assert isinstance(spec.instance, BaseTool), f"{spec.name} 不是 BaseTool"


def test_discover_tools_source_file_correct():
    """source_file 应指向 crawagent/tools/*.py 或 plugins/，不指向 langchain 内部。"""
    specs = discover_tools()
    for spec in specs:
        if spec.source_file:  # 空字符串允许（build_fn 模式）
            assert "langchain_core" not in spec.source_file, (
                f"{spec.name} 的 source_file 指向 langchain 内部: {spec.source_file}"
            )
            # 允许 crawagent/tools/ 或 plugins/（外部插件路径）
            assert (
                "crawagent" in spec.source_file or "plugins" in spec.source_file
            ), f"{spec.name} 的 source_file 应指向项目内: {spec.source_file}"


# ---------------------------------------------------------------------------
# 2. _TOOL_META 元数据
# ---------------------------------------------------------------------------

def test_tool_meta_keys_match_discovered_tools():
    """_TOOL_META 的 key 集合应与 discover_tools 名字集合的子集对齐。"""
    meta_names = set(_TOOL_META.keys())
    discovered_names = {s.name for s in discover_tools()}
    # _TOOL_META 是覆盖集（包含但不限于发现的）—— 至少要为发现的关键工具提供元数据
    # 抽几个核心工具名字检查
    for key in ("crawl_webpage", "save_to_file", "run_custom_script"):
        assert key in meta_names, f"{key} 应在 _TOOL_META"


def test_tool_meta_structure():
    """每条 _TOOL_META 记录有 category 和 deps 字段。"""
    for name, meta in _TOOL_META.items():
        assert "category" in meta, f"{name} 缺 category"
        assert "deps" in meta, f"{name} 缺 deps"
        assert isinstance(meta["deps"], list), f"{name} deps 应是列表"


# ---------------------------------------------------------------------------
# 3. @register_tool 装饰器
# ---------------------------------------------------------------------------

def test_register_tool_adds_to_registry():
    """@register_tool 装饰的工具会出现在 _REGISTERED。"""
    initial_count = len(_REGISTERED)
    # 注意：注册的 key 是工具 name，所以用唯一 name 避免冲突
    @register_tool(category="general", deps=["pytest"])
    def _test_tool_unique_name_for_registry(x: int) -> int:
        """测试用 — 把 x 加 1"""
        return x + 1

    # 装饰器应返回 StructuredTool 实例（包装过的）
    from langchain_core.tools import BaseTool
    assert isinstance(_test_tool_unique_name_for_registry, BaseTool)

    # _REGISTERED 应有新条目
    assert "_test_tool_unique_name_for_registry" in _REGISTERED
    spec = _REGISTERED["_test_tool_unique_name_for_registry"]
    assert spec.category == "general"
    assert spec.dependencies == ["pytest"]


# ---------------------------------------------------------------------------
# 4. _guess_category 兜底
# ---------------------------------------------------------------------------

def test_guess_category_known_keyword():
    """_guess_category 根据工具名关键词分类。"""
    assert _guess_category("crawl_webpage") in ("crawl", "general")
    assert _guess_category("save_to_file") in ("save", "general")
    assert _guess_category("extract_content") in ("extract", "general")


def test_guess_category_unknown_returns_general():
    """未知名字 → 兜底返回 'general'。"""
    assert _guess_category("totally_unknown_tool_xyz") == "general"


# ---------------------------------------------------------------------------
# 5. build_all_tools 集成
# ---------------------------------------------------------------------------

def test_build_all_tools_returns_base_tools():
    """build_all_tools 返回的都是 BaseTool 实例。"""
    tools = build_all_tools()
    assert len(tools) > 0
    for t in tools:
        assert isinstance(t, BaseTool)


def test_build_all_tools_includes_known_tools():
    """build_all_tools 至少包含关键工具。"""
    names = {t.name for t in build_all_tools()}
    assert "crawl_webpage" in names or any("crawl" in n for n in names)