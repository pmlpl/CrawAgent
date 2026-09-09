"""Phase 2 插件化核心测试 — registry 基础设施 + agent 反向依赖保证。

覆盖：
  1. @register_tool 装饰器正确注册 category/deps
  2. discover_tools() 发现所有非动态工具（20 个）
  3. build_all_tools() 返回唯一 BaseTool 实例列表
  4. specs_to_prompt() 生成分类索引输出
  5. import agent 不触发循环依赖（先 registry 后 agent，单向）
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.tools import BaseTool


@pytest.fixture(autouse=True)
def _registry_isolation():
    """每个测试前后保存/恢复 _REGISTERED 和 _DISCOVERY_CACHE，避免交叉污染。"""
    import crawagent.tools.registry as reg
    saved_registered = dict(reg._REGISTERED)
    saved_cache = reg._DISCOVERY_CACHE
    reg._REGISTERED.clear()
    reg._DISCOVERY_CACHE = None
    yield
    reg._REGISTERED.clear()
    reg._REGISTERED.update(saved_registered)
    reg._DISCOVERY_CACHE = saved_cache

# —— 1. @register_tool 基础能力 ——

def test_register_tool_decorator_returns_base_tool():
    """@register_tool 装饰后能返回 BaseTool（和 langchain @tool 等价）。"""
    from crawagent.tools.registry import register_tool

    @register_tool(category="extract", deps=["requests"])
    def my_extractor(url: str) -> str:
        """Extract content from a URL using requests."""
        return f"extracted from {url}"

    assert isinstance(my_extractor, BaseTool)
    assert my_extractor.name == "my_extractor"


def test_register_tool_category_deps_logged():
    """@register_tool 的 category/deps 进了 _REGISTERED 注册表。"""
    from crawagent.tools.registry import register_tool, _REGISTERED

    @register_tool(category="crawl", deps=["playwright", "bs4"])
    def my_crawler(url: str) -> str:
        """Crawl a page with Playwright and BeautifulSoup."""
        return "ok"

    spec = _REGISTERED.get("my_crawler")
    assert spec is not None, "工具名应该在 _REGISTERED 里"
    assert spec.category == "crawl"
    assert spec.dependencies == ["playwright", "bs4"]
    assert spec.instance is not None


# —— 2. discover_tools 扫描能力 ——

def test_discover_tools_finds_core_count():
    """discover_tools 应发现 26 个内置工具；插件工具另计（P2-9 插件规范）。"""
    # 清缓存，强制重新扫描
    import crawagent.tools.registry as reg
    reg._DISCOVERY_CACHE = None
    specs = reg.discover_tools()
    names = [s.name for s in specs]

    # 内置（源码在 crawagent/ 下）= 26；插件（source_file 以 plugins/ 开头）另计
    builtin = [s for s in specs if not s.source_file.startswith("plugins")]
    assert len(builtin) == 26, f"内置工具应为 26 个，实际 {len(builtin)}: {[s.name for s in builtin]}"
    assert len(names) == len(set(names)), f"工具名有重复: {names}"


def test_discover_tools_includes_plugins():
    """plugins/ 下的插件工具应被发现（example-rss 示例插件带 1 个工具）。"""
    import crawagent.tools.registry as reg
    reg._DISCOVERY_CACHE = None
    specs = reg.discover_tools()
    names = {s.name for s in specs}
    assert "fetch_rss_feed" in names, "示例插件 example-rss 的 fetch_rss_feed 工具应被发现"
    plugin_spec = next(s for s in specs if s.name == "fetch_rss_feed")
    assert plugin_spec.source_file.startswith("plugins/"), (
        f"插件工具 source_file 应以 plugins/ 开头: {plugin_spec.source_file}"
    )


def test_discover_tools_no_mcp():
    """discover_tools 不能包含 MCP 工具 — 它们是动态装配的。"""
    from crawagent.tools.registry import discover_tools
    spec_names = {s.name for s in discover_tools()}
    assert "check_mcp_status" not in spec_names
    assert "wait_capture_ready" not in spec_names


def test_discover_tools_no_duplicates():
    """discover_tools 发现的工具名不能重复。"""
    from crawagent.tools.registry import discover_tools
    names = [s.name for s in discover_tools()]
    assert len(names) == len(set(names)), f"工具名重复: {names}"


def test_discover_tools_has_recommend_scripts():
    """discover_tools 应该包含 recommend_scripts（Phase 2 新纳入的跨站脚本推荐）。"""
    from crawagent.tools.registry import discover_tools
    names = {s.name for s in discover_tools()}
    assert "recommend_scripts" in names


def test_discover_tools_source_file_correct():
    """discover_tools 发现的工具都有正确的 source_file（不是 langchain 内部）。"""
    from crawagent.tools.registry import discover_tools
    for s in discover_tools():
        assert s.source_file, f"{s.name} 缺少 source_file"
        assert "langchain_core" not in s.source_file, f"{s.name} 的 source_file 指向 langchain 内部: {s.source_file}"


# —— 3. build_all_tools 集成 ——

def test_build_all_tools_returns_base_tools():
    """build_all_tools 返回的都是 BaseTool 实例。"""
    from crawagent.tools.registry import build_all_tools
    tools = build_all_tools()
    assert len(tools) > 0
    for t in tools:
        assert isinstance(t, BaseTool)


def test_build_all_tools_no_mcp():
    """build_all_tools 不包含 MCP 工具 — 它们由 agent._build_tools() 单独 append。"""
    from crawagent.tools.registry import build_all_tools
    names = {t.name for t in build_all_tools()}
    assert "check_mcp_status" not in names
    assert "wait_capture_ready" not in names


# —— 4. specs_to_prompt ——

def test_specs_to_prompt_produces_grouped_output():
    """specs_to_prompt 应该输出非空、带分类的工具索引。"""
    from crawagent.tools.registry import specs_to_prompt
    prompt = specs_to_prompt()
    assert prompt, "specs_to_prompt 应该返回非空字符串"
    # 检查包含核心分类标签
    for label in ("Crawl", "Extract", "Save", "Site", "Script"):
        assert label in prompt, f"prompt 应该包含 [{label}] 分类"
    # 检查包含实际工具名
    assert "crawl_webpage" in prompt
    assert "recommend_scripts" in prompt


# —— 5. 循环依赖保证 ——

def test_no_circular_import_agent_to_registry():
    """registry 不依赖 agent，agent 依赖 registry — 单向，无循环。"""
    # registry 能单独 import（不触发 agent import）
    from crawagent.tools import registry
    # registry 里不应该出现 crawagent.graph.agent 的 import 语句
    source = Path(registry.__file__).read_text(encoding="utf-8")
    assert "from crawagent.graph.agent" not in source, "registry 不能 import agent（循环依赖）"
    assert "import crawagent.graph.agent" not in source, "registry 不能 import agent（循环依赖）"


def test_agent_imports_from_registry():
    """agent.py 现在用 registry.build_all_tools() 拿工具，不是硬编码 import。"""
    from crawagent.graph import agent
    source = Path(agent.__file__).read_text(encoding="utf-8")
    # 应该有 registry.build_all_tools 的引用
    assert "build_all_tools" in source
    # 不应该有硬编码的工具 import（19 个 from crawagent.tools.xxx import yyy）
    # 这个检查比较松，只确保不再有 from crawagent.tools.crawl_tool import crawl_webpage 这种
    assert "from crawagent.tools.crawl_tool import" not in source
    assert "from crawagent.tools.extract_tool import" not in source
    assert "from crawagent.tools.browse_tool import" not in source


def test_agent_system_prompt_includes_tool_index():
    """agent.SYSTEM_PROMPT 应该包含 specs_to_prompt 生成的工具索引。"""
    from crawagent.graph.agent import SYSTEM_PROMPT
    assert "[TOOL INDEX" in SYSTEM_PROMPT, "SYSTEM_PROMPT 应该包含 auto-generated tool index"
    assert "recommend_scripts" in SYSTEM_PROMPT, "新纳入的 recommend_scripts 应该在 SYSTEM_PROMPT 里"
