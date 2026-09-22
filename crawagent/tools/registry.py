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
import json
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
        """工具已构建：``instance`` 直接可用，或 ``build_fn`` 可即时构建。

        Returns:
            True 表示这个 ToolSpec 当前可被 agent 调用。
        """
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
        """``@register_tool`` 内部包装器：包 langchain ``@tool`` 再登记元数据。

        Args:
            func: 用户写的工具函数。

        Returns:
            与 ``func`` 行为等价的 ``BaseTool`` 实例，同时登记到 ``_REGISTERED``。
        """
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
    "extract_list_paged":     {"category": "extract", "deps": ["requests", "bs4"]},
    "save_record":            {"category": "save",    "deps": ["sqlite3"]},
    "list_crawled_resources": {"category": "save",    "deps": ["sqlite3"]},
    "search_knowledge":       {"category": "save",    "deps": ["sqlite3"]},
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
    "ask_user":               {"category": "general", "deps": []},
    "video_site_expert":      {"category": "site",    "deps": ["langgraph"]},
    "read_skill":             {"category": "skill",   "deps": ["yaml"]},
    "list_mcp_servers":       {"category": "eco",     "deps": []},
    "add_mcp_server":         {"category": "eco",     "deps": []},
    "remove_mcp_server":      {"category": "eco",     "deps": []},
    "disable_mcp_server":     {"category": "eco",     "deps": []},
    "markitdown_convert":     {"category": "advanced", "deps": ["markitdown"]},
    "crawl4ai_deep_crawl":    {"category": "advanced", "deps": ["crawl4ai", "playwright"]},
    "browser_use_navigate":  {"category": "advanced", "deps": ["browser_use", "playwright"]},
    "add_proxy":              {"category": "general", "deps": ["requests"]},
    "remove_proxy":           {"category": "general", "deps": ["json"]},
    "mark_proxy_failed":      {"category": "general", "deps": ["json"]},
    "get_proxy":              {"category": "general", "deps": ["requests"]},
    "list_proxies":           {"category": "general", "deps": ["json"]},
    "login_site":             {"category": "site",    "deps": ["playwright"]},
    "check_login_status":     {"category": "site",    "deps": ["requests"]},
    # ---- Android 逆向（frida hook）----
    "list_adb_devices":        {"category": "android", "deps": ["subprocess"]},
    "install_apk":             {"category": "android", "deps": ["subprocess"]},
    "push_file":               {"category": "android", "deps": ["subprocess"]},
    "frida_hook_function":     {"category": "android", "deps": ["subprocess"]},
    "frida_dump_so":           {"category": "android", "deps": ["subprocess"]},
    "frida_bypass_ssl_pinning":{"category": "android", "deps": ["subprocess"]},
}

# 内部模块黑名单：这些模块里的 BaseTool 不应该被 discover_tools 收集
# （要么是辅助工具不进 Agent，要么是被其他模块内部调用）
_SKIP_TOOL_MODULES = {
    "bilibili_tool",      # 老的签名辅助，没 @tool
    "douyin_tool",        # 同上
    "confidence",         # 内部评分函数，没 @tool
    "font_decrypt",       # 字体解密辅助
    "social_utils",       # 公共 helper 模块（无 BaseTool，避免被自动收集）
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
    if any(k in name for k in ("frida", "adb", "apk", "ssl_pinning")):
        return "android"
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

    # 2. 扫 tools/*.py（单文件模块）和 tools/*/（包目录）
    tools_dir = Path(__file__).resolve().parent
    root = Path(__file__).resolve().parent.parent.parent
    mod_names: list[str] = []
    for entry in sorted(tools_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".py":
            mod_names.append(entry.stem)
        elif entry.is_dir() and (entry / "__init__.py").exists():
            mod_names.append(entry.name)
    for mod_name in mod_names:
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

    # 4. 扫第三方插件（plugins/*/tools/*.py，plugin.json manifest 约定）
    for spec in _plugin_tool_specs(seen):
        if spec.name in seen:
            continue  # 双保险：正常不会走到（_plugin_tool_specs 内部已按 seen 过滤）
        seen.add(spec.name)
        specs.append(spec)

    # 按 category → name 排序，保证每次 discovery 顺序稳定（prompt 缓存稳定）
    specs.sort(key=lambda s: (s.category, s.name))
    return specs


# ---------------------------------------------------------------------------
# 插件发现（P2-9 插件规范）— plugins/ 目录 + plugin.json manifest
# ---------------------------------------------------------------------------

def plugins_root() -> Path:
    """第三方插件根目录：项目根/plugins/。"""
    return Path(__file__).resolve().parent.parent.parent / "plugins"


def list_plugins() -> list[dict]:
    """扫描 plugins/ 下的插件（不 import，只读 manifest）。

    返回 [{name, version, description, author, dependencies, dir, valid, error}]。
    valid=False 的插件保留在列表里（doctor / CLI 展示用），但不会提供工具。
    """
    root = plugins_root()
    results: list[dict] = []
    if not root.is_dir():
        return results
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        manifest_path = d / "plugin.json"
        if not manifest_path.exists():
            results.append({
                "name": d.name, "version": "", "description": "", "author": "",
                "dependencies": [], "dir": str(d), "valid": False,
                "error": "缺少 plugin.json manifest",
            })
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            results.append({
                "name": d.name, "version": "", "description": "", "author": "",
                "dependencies": [], "dir": str(d), "valid": False,
                "error": f"plugin.json 解析失败: {e}",
            })
            continue
        if not isinstance(manifest, dict) or not manifest.get("name"):
            results.append({
                "name": d.name, "version": "", "description": "", "author": "",
                "dependencies": [], "dir": str(d), "valid": False,
                "error": "manifest 缺少 name 字段",
            })
            continue
        results.append({
            "name": str(manifest.get("name") or d.name),
            "version": str(manifest.get("version", "")),
            "description": str(manifest.get("description", "")),
            "author": str(manifest.get("author", "")),
            "dependencies": [str(x) for x in manifest.get("dependencies", []) if x],
            "dir": str(d),
            "valid": True,
            "error": "",
        })
    return results


def plugin_skill_dirs() -> list[Path]:
    """所有插件自带的 skills/ 子目录（有效的插件才计入）。"""
    return [Path(p["dir"]) / "skills" for p in list_plugins() if p["valid"]]


def _plugin_tool_specs(seen: set[str] | None = None) -> list[ToolSpec]:
    """import 每个有效插件的 tools/*.py，收集其中的 BaseTool 实例。

    插件模块不在 sys.path 上，用 spec_from_file_location 以唯一模块名导入；
    已导入过的模块直接复用 sys.modules 缓存（discover 重入时不重复执行）。
    单个插件/模块失败只打警告，不阻断 Agent 构建。
    seen 里的名字（内置/已注册工具）直接跳过，避免同一名字两条路重复收录。
    """
    import sys as _sys
    import importlib.util

    skip = seen or set()
    specs: list[ToolSpec] = []
    for plugin in list_plugins():
        if not plugin["valid"]:
            continue
        pdir = Path(plugin["dir"])
        tools_dir = pdir / "tools"
        if not tools_dir.is_dir():
            continue
        for py in sorted(tools_dir.glob("*.py")):
            if py.stem.startswith("_"):
                continue
            mod_name = f"crawagent_plugin_{plugin['name']}_{py.stem}"
            try:
                if mod_name in _sys.modules:
                    mod = _sys.modules[mod_name]
                else:
                    spec = importlib.util.spec_from_file_location(mod_name, py)
                    if spec is None or spec.loader is None:
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    _sys.modules[mod_name] = mod  # 先注册防装饰器自引用递归
                    spec.loader.exec_module(mod)
            except Exception as e:
                print(f"[PLUGIN] '{plugin['name']}' 模块 {py.stem} 导入失败（跳过）: "
                      f"{type(e).__name__}: {e}")
                continue
            for attr_name in dir(mod):
                if attr_name.startswith("_"):
                    continue
                obj = getattr(mod, attr_name, None)
                if not isinstance(obj, BaseTool) or obj.name in _SKIP_TOOL_NAMES:
                    continue
                if obj.name in skip:
                    continue  # 内置/step1 已收（重入 discover 时 _REGISTERED 先行），不重复收
                specs.append(ToolSpec(
                    name=obj.name,
                    description=obj.description[:500] if obj.description else "",
                    source_file=f"plugins/{plugin['name']}/tools/{py.name}",
                    dependencies=list(plugin["dependencies"]),
                    category=_guess_category(obj.name),
                    instance=obj,
                ))
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
            "eco": "🔌 Ecosystem",
            "advanced": "🚀 Advanced",
            "android": "📱 Android",
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
