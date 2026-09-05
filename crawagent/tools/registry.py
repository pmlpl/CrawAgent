"""工具注册中心 — ToolSpec + @register_tool + 自动扫描 + prompt 生成

Phase 2 插件化的基础设施。核心原则：

  registry.py 是单向依赖的基础设施 — 任何工具模块都可以 import 它注册自己，
  但 registry 绝对不能 import agent.py / server.py 等上层模块（循环依赖杀手）。

用法：
  # 方式 A — 新插件，用 @register_tool 装饰器自动带 category/deps
  from crawagent.tools.registry import register_tool

  @register_tool(category="site", deps=["requests"])
  def my_tool(url: str) -> str:
      return "ok"

  # 方式 B — 现有 @tool 工具不动，discover_tools() 扫描 tools/ 目录自动发现
  from langchain_core.tools import tool

  @tool
  def existing_tool(x: int) -> int:
      return x + 1

  # Agent 构建
  from crawagent.tools.registry import build_all_tools
  tools = build_all_tools()  # ← 现在 agent._build_tools() 就调这个

迁移路径：
  现有 19 个 @tool 工具 → discover_tools() 自动扫描（零改动）
  新插件 → @register_tool 装饰器（声明 category/deps）
"""
from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langchain_core.tools import BaseTool

from langchain_core.tools import tool as _lc_tool


# ---------------------------------------------------------------------------
# ToolSpec — 所有工具的统一描述
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """单个工具的规格描述。所有工具（内置 + 外部插件）统一成这个结构。"""
    name: str
    description: str
    source_file: str = ""          # 相对路径，如 "crawagent/tools/file_tool.py"
    dependencies: list[str] = field(default_factory=list)
    category: str = "general"
    build_fn: Callable[[], BaseTool] | None = None
    instance: BaseTool | None = None

    @property
    def is_ready(self) -> bool:
        return self.instance is not None or self.build_fn is not None


# ---------------------------------------------------------------------------
# @register_tool — 新插件注册入口（现有 @tool 不用改）
# ---------------------------------------------------------------------------

# 模块级注册表：每个 @register_tool 装饰的函数会在这里登记
# key = tool name, value = ToolSpec
_REGISTERED: dict[str, ToolSpec] = {}


def register_tool(
    category: str = "general",
    deps: list[str] | None = None,
    description_override: str = "",
):
    """装饰器：把一个函数注册为 CrawAgent 工具。

    内部等价于 langchain 的 @tool，但额外自动登记 category/deps 元数据到 registry。
    现有工具已经用 @tool 注册 — 不需要改，discover_tools() 会扫出来。
    这个装饰器主要给**新插件**用，方便一次性声明 category/deps。

    用法（伪代码示例）：
        @register_tool(category="site", deps=["requests"])
        def scrape_feed(url: str) -> str:
            # 抓取 RSS feed
            ...

    Args:
        category: 分类标签 (crawl / extract / save / site / script / mcp / skill / general)
        deps: 可选的运行时依赖列表，如 ["requests", "playwright"]
        description_override: 可选的描述覆盖（默认用函数 docstring）
    """
    def decorator(func):
        # 先让 langchain 把 func 变成 StructuredTool
        lc_tool = _lc_tool(func)

        # 再注册进我们的 registry
        spec = ToolSpec(
            name=lc_tool.name,
            description=description_override or lc_tool.description[:500],
            category=category,
            dependencies=list(deps) if deps else [],
            instance=lc_tool,
        )
        # 尝试填源文件
        try:
            mod = inspect.getmodule(func)
            if mod and mod.__file__:
                spec.source_file = str(Path(mod.__file__).resolve())
        except Exception:
            pass

        _REGISTERED[spec.name] = spec
        return lc_tool

    return decorator


# ---------------------------------------------------------------------------
# discover_tools — 扫描 tools/*.py + 外部模块，收集所有 @tool 实例
# ---------------------------------------------------------------------------

# 现有工具的硬编码元数据（discover_tools 发现后用这个补 category/deps）
_TOOL_META: dict[str, dict[str, Any]] = {
    "crawl_webpage":          {"category": "crawl",   "deps": ["requests", "fake_useragent"]},
    "browse_and_crawl":       {"category": "crawl",   "deps": ["playwright", "bs4", "html2text"]},
    "extract_content":        {"category": "extract", "deps": ["bs4", "html2text"]},
    "extract_list":           {"category": "extract", "deps": ["bs4"]},
    "save_record":            {"category": "save",    "deps": ["sqlite3"]},
    "list_crawled_resources": {"category": "save",    "deps": ["sqlite3"]},
    "save_to_file":           {"category": "save",    "deps": ["pathlib"]},
    "download_images":        {"category": "save",    "deps": ["requests"]},
    "extract_social_media":   {"category": "site",    "deps": ["requests"]},
    "download_social_media":  {"category": "site",    "deps": ["requests", "yt-dlp"]},
    "extract_wallpaper_list": {"category": "site",    "deps": ["bs4", "requests"]},
    "wallpaper_detail":       {"category": "site",    "deps": ["bs4", "requests"]},
    "list_weread_chapters":   {"category": "site",    "deps": ["bs4", "requests"]},
    "get_weread_chapter":     {"category": "site",    "deps": ["bs4", "requests"]},
    "list_site_profiles":     {"category": "site",    "deps": ["json"]},
    "save_site_profile":      {"category": "site",    "deps": ["json"]},
    "recommend_scripts":      {"category": "site",    "deps": ["json", "re"]},
    "run_custom_script":      {"category": "script",  "deps": ["subprocess"]},
    "video_site_expert":      {"category": "site",    "deps": ["langgraph"]},
    "read_skill":             {"category": "skill",   "deps": ["yaml"]},
}

# 内部模块黑名单：这些模块里的 BaseTool 不应该被 discover_tools 收集
# （要么是辅助工具不进 Agent，要么是被其他模块内部调用）
_SKIP_TOOL_MODULES = {
    "bilibili_tool",      # 老的签名辅助，没 @tool
    "douyin_tool",        # 同上
    "confidence",         # 内部评分函数，没 @tool
    "font_decrypt",       # 字体解密辅助
    "_social_utils",      # 内部工具
}

# 工具名黑名单：即使 discover_tools 扫到也过滤掉（老版/废弃工具/动态装配工具）
_SKIP_TOOL_NAMES = {
    "analyze_site_structure",  # site_analyze_tool.py — 老版站点分析
    "probe_video_player",      # video_probe_tool.py — 老版视频探测
    "web_search",              # search_tool.py — 老版搜索
    # MCP 工具是动态装配的（build_mcp_tools 根据 Electron enabled 状态决定），
    # 不进 discover_tools，由 agent._build_tools() 显式 append
    "check_mcp_status",
    "wait_capture_ready",
}

# tools/ 目录外但有 @tool 的模块
_EXTRA_TOOL_MODULES: list[str] = [
    "crawagent.graph.skills",          # read_skill
    "crawagent.graph.subagents.video_finder",  # video_site_expert
]


def _guess_category(name: str) -> str:
    if any(k in name for k in ("crawl", "browse")):
        return "crawl"
    if any(k in name for k in ("extract", "list", "probe", "analyze")):
        return "extract"
    if any(k in name for k in ("save", "download", "query")):
        return "save"
    if any(k in name for k in ("script", "social", "wallpaper", "weread", "video")):
        return "site"
    if "mcp" in name or "capture" in name:
        return "mcp"
    return "general"


def _source_file_for(obj) -> str:
    """找出 BaseTool 实例的源文件（StructuredTool.func 的 __module__ / __qualname__ 反推）。"""
    # StructuredTool 自带 func 属性
    func = getattr(obj, "func", None)
    if func is not None:
        # 跳过 langchain 内部的 wrapper（如 _to_structured_tool 的返回）
        func_module = getattr(func, "__module__", "")
        if func_module and "langchain" not in func_module:
            try:
                mod = importlib.import_module(func_module)
                mod_file = getattr(mod, "__file__", None)
                if mod_file:
                    root = Path(__file__).resolve().parent.parent.parent
                    p = Path(mod_file).resolve()
                    try:
                        return str(p.relative_to(root))
                    except ValueError:
                        return str(p)
            except Exception:
                pass
        # 兜底：尝试 func 本身的 __file__
        mod = inspect.getmodule(func)
        if mod and mod.__file__:
            root = Path(__file__).resolve().parent.parent.parent
            p = Path(mod.__file__).resolve()
            try:
                return str(p.relative_to(root))
            except ValueError:
                return str(p)
    # 再兜底：tool 自身有没有 attrs_schema 的位置信息
    try:
        mod_name = obj.__class__.__module__
        if mod_name and "langchain" not in mod_name:
            mod = importlib.import_module(mod_name)
            if mod.__file__:
                root = Path(__file__).resolve().parent.parent.parent
                p = Path(mod.__file__).resolve()
                try:
                    return str(p.relative_to(root))
                except ValueError:
                    return str(p)
    except Exception:
        pass
    return ""


def discover_tools() -> list[ToolSpec]:
    """扫描 tools/ 目录 + 外部模块，发现所有 BaseTool 实例。

    这是 Phase 2 的核心 — 不依赖 agent._build_tools()，反向打破循环。
    """
    specs: list[ToolSpec] = []
    seen: set[str] = set()

    # 1. 先收 @register_tool 显式注册的（优先级最高，category/deps 最准）
    for spec in _REGISTERED.values():
        specs.append(spec)
        seen.add(spec.name)

    # 2. 扫 tools/*.py
    tools_dir = Path(__file__).resolve().parent
    root = Path(__file__).resolve().parent.parent.parent
    for py in sorted(tools_dir.glob("*.py")):
        mod_name = py.stem
        if mod_name.startswith("_"):
            continue
        if mod_name in _SKIP_TOOL_MODULES:
            continue
        # 跳过 registry.py 自己
        if mod_name == "registry":
            continue
        full_name = f"crawagent.tools.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception:
            # 单个模块导入失败不阻断整体扫描（让 Agent 能构建起来）
            continue
        for attr_name in dir(mod):
            if attr_name.startswith("_"):
                continue
            if attr_name in seen:
                continue
            obj = getattr(mod, attr_name)
            if not isinstance(obj, BaseTool):
                continue
            if obj.name in _SKIP_TOOL_NAMES:
                continue
            seen.add(obj.name)
            meta = _TOOL_META.get(obj.name, {})
            specs.append(ToolSpec(
                name=obj.name,
                description=obj.description[:500] if obj.description else "",
                source_file=_source_file_for(obj),
                dependencies=meta.get("deps", []),
                category=meta.get("category", _guess_category(obj.name)),
                instance=obj,
            ))

    # 3. 扫外部模块
    for mod_path in _EXTRA_TOOL_MODULES:
        try:
            mod = importlib.import_module(mod_path)
        except Exception:
            continue
        for attr_name in dir(mod):
            if attr_name.startswith("_"):
                continue
            if attr_name in seen:
                continue
            obj = getattr(mod, attr_name)
            if not isinstance(obj, BaseTool):
                continue
            if obj.name in _SKIP_TOOL_NAMES:
                continue
            seen.add(obj.name)
            meta = _TOOL_META.get(obj.name, {})
            specs.append(ToolSpec(
                name=obj.name,
                description=obj.description[:500] if obj.description else "",
                source_file=_source_file_for(obj),
                dependencies=meta.get("deps", []),
                category=meta.get("category", _guess_category(obj.name)),
                instance=obj,
            ))

    # 按 category → name 排序，保证每次 discovery 顺序稳定（prompt 缓存稳定）
    specs.sort(key=lambda s: (s.category, s.name))
    return specs


# ---------------------------------------------------------------------------
# 公共 API — agent.py 改用这些
# ---------------------------------------------------------------------------

_DISCOVERY_CACHE: list[ToolSpec] | None = None


def get_all_tool_specs() -> list[ToolSpec]:
    """返回完整 ToolSpec 列表（带进程级缓存，保证 SYSTEM_PROMPT 构建时稳定）。"""
    global _DISCOVERY_CACHE
    if _DISCOVERY_CACHE is None:
        _DISCOVERY_CACHE = discover_tools()
    return _DISCOVERY_CACHE


def build_all_tools() -> list[BaseTool]:
    """返回 Agent 可用的 BaseTool 实例列表（不含 MCP — MCP 动态 append）。

    agent._build_tools() 现在就调这个。
    """
    return [s.instance for s in get_all_tool_specs() if s.instance is not None]


def specs_to_prompt(specs: list[ToolSpec] | None = None, group_by_category: bool = True) -> str:
    """把 ToolSpec 列表渲染成补充性工具索引（进 system prompt）。

    注意：这是补充索引，不替代 system.md 里已有的详细工具说明。
    作用：新插件自动进索引、分类一目了然、方便 LLM 快速定位。
    """
    specs = specs or get_all_tool_specs()
    if not specs:
        return ""

    lines = [
        "",
        "[TOOL INDEX — auto-generated, keep system.md for full docs]",
        "Below is a one-line-per-tool index grouped by category. Detailed usage lives in system.md.",
    ]

    if group_by_category:
        by_cat: dict[str, list[ToolSpec]] = {}
        for s in specs:
            by_cat.setdefault(s.category, []).append(s)
        cat_labels = {
            "crawl": "🔍 Crawl",
            "extract": "📄 Extract",
            "save": "💾 Save",
            "site": "🎯 Site-specific",
            "script": "🔧 Script",
            "mcp": "🖥️ MCP",
            "skill": "📚 Skills",
            "general": "General",
        }
        for cat, items in by_cat.items():
            label = cat_labels.get(cat, cat)
            lines.append(f"\n  [{label}]")
            for s in items:
                desc = (s.description[:80] + "...") if len(s.description) > 80 else s.description
                lines.append(f"    {s.name}: {desc}")
    else:
        for s in specs:
            desc = (s.description[:100] + "...") if len(s.description) > 100 else s.description
            lines.append(f"  {s.name}: {desc}")

    return "\n".join(lines) + "\n"


def audit_tools() -> str:
    """审计入口：打印所有工具的分类/依赖/源文件。"""
    specs = get_all_tool_specs()
    lines = [f"Tool Registry Audit — {len(specs)} tools discovered", "=" * 60]
    by_cat: dict[str, list[ToolSpec]] = {}
    for s in specs:
        by_cat.setdefault(s.category, []).append(s)

    warnings = []
    for cat, items in by_cat.items():
        lines.append(f"\n[{cat.upper()}]  {len(items)} tools")
        for s in items:
            src = s.source_file or "<UNKNOWN>"
            deps = ", ".join(s.dependencies) or "-"
            lines.append(f"  {s.name:<25} deps=[{deps}]  src={src}")
            if not s.description:
                warnings.append(f"⚠  {s.name}: no description")
            if not s.source_file:
                warnings.append(f"⚠  {s.name}: source file unknown")

    if warnings:
        lines.append(f"\n⚠  Warnings ({len(warnings)}):")
        for w in warnings:
            lines.append(f"  {w}")
    else:
        lines.append(f"\n✅ All {len(specs)} tools have description + source file.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 兼容层 — 旧函数名保留（之前的 scan_existing_tools 已由 discover_tools 替代）
# ---------------------------------------------------------------------------

def scan_existing_tools() -> list[ToolSpec]:
    """旧入口保留，内部委托 discover_tools。"""
    return get_all_tool_specs()
