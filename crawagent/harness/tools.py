"""CrawAgent 内置爬虫工具集定义。

参考 pi Agent Harness 的 tool 系统设计，定义了爬虫场景下的核心工具：
- crawl:    抓取页面内容（HTTP / 浏览器模式）
- extract:  从 HTML 中提取结构化数据
- analyze:  分析网站结构与反爬检测
- save:     保存数据到文件
- search:   搜索本地已爬数据或互联网
- monitor:  监控目标 URL 变化
- scan_vuln: 扫描安全漏洞

ToolRegistry 负责管理工具定义与执行器的注册和查询。
"""

from __future__ import annotations

import csv as _csv
import hashlib as _hashlib
import json as _json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field, create_model, ConfigDict

from crawagent.harness.types import CrawlToolDef, ToolExecMode


# ==================== 内置爬虫工具定义 ====================

CRAWL_TOOL = CrawlToolDef(
    name="crawl",
    description="抓取指定 URL 的页面内容。支持 HTTP 和浏览器两种模式。"
                "当 httpx 失败时自动尝试 curl_cffi 和 Playwright。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "method": {"type": "string", "enum": ["GET", "POST"], "default": "GET"},
            "headers": {"type": "object", "description": "自定义请求头"},
            "use_browser": {"type": "boolean", "default": False, "description": "是否使用浏览器渲染"},
            "timeout": {"type": "number", "default": 30, "description": "超时秒数"},
        },
        "required": ["url"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=True,
)

EXTRACT_TOOL = CrawlToolDef(
    name="extract",
    description="从 HTML 内容中提取结构化数据。支持 CSS 选择器、XPath、JSON-LD 和 LLM 提取。",
    parameters={
        "type": "object",
        "properties": {
            "html": {"type": "string", "description": "HTML 内容"},
            "selectors": {"type": "object", "description": "CSS 选择器配置"},
            "schema": {"type": "object", "description": "目标 JSON Schema（LLM 提取用）"},
            "method": {
                "type": "string",
                "enum": ["css", "xpath", "jsonld", "llm"],
                "default": "css",
                "description": "提取方法",
            },
        },
        "required": ["html"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=True,
)

ANALYZE_TOOL = CrawlToolDef(
    name="analyze",
    description="分析网站结构：页面类型、数据源（DOM/API）、分页方式、反爬检测。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "deep": {"type": "boolean", "default": False, "description": "是否深度分析（含网络捕获）"},
        },
        "required": ["url"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=True,
)

SAVE_TOOL = CrawlToolDef(
    name="save",
    description="将数据保存到指定文件路径。支持 Markdown、JSON、CSV 格式。"
                "支持路径模板占位符：{domain}/{date}/{year}/{month}/{day}/{title}/{slug}/{ext}。"
                "Agent 可自主决定保存路径和格式。",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要保存的内容"},
            "data": {
                "type": "array",
                "description": "结构化数据记录（与 content 二选一）",
                "items": {"type": "object"},
            },
            "path": {
                "type": "string",
                "description": "保存路径。可含模板占位符：{domain}/{date}/{title} 等（如 ~/crawagent/articles/{domain}/{date}/{title}.md）",
            },
            "url": {
                "type": "string",
                "description": "源 URL（用于从模板提取 domain；与 path 模板配合使用）",
            },
            "title": {
                "type": "string",
                "description": "文件标题（用于模板渲染 {title}/{slug}；自动转义非法字符）",
            },
            "format": {
                "type": "string",
                "enum": ["markdown", "json", "csv"],
                "description": "保存格式（留空自动推断：扁平→CSV/嵌套→JSON/富文本→MD）",
            },
            "append": {"type": "boolean", "default": False, "description": "是否追加写入"},
        },
        "required": ["path"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=False,
)

SEARCH_TOOL = CrawlToolDef(
    name="search",
    description="搜索已爬取的内容（全文搜索），或搜索互联网获取信息。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "scope": {
                "type": "string",
                "enum": ["local", "web"],
                "default": "local",
                "description": "搜索范围：local=本地已爬数据，web=互联网",
            },
            "limit": {"type": "number", "default": 10, "description": "返回结果数"},
        },
        "required": ["query"],
    },
    exec_mode=ToolExecMode.PARALLEL,
    replay_safe=True,
)

MONITOR_TOOL = CrawlToolDef(
    name="monitor",
    description="监控目标 URL 的变化。设置监控频率和告警条件。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "监控目标 URL"},
            "interval_minutes": {"type": "number", "default": 360, "description": "监控间隔（分钟）"},
            "selector": {"type": "string", "description": "监控的 CSS 选择器"},
            "condition": {
                "type": "string",
                "enum": ["any_change", "text_change", "price_drop", "available"],
                "default": "any_change",
            },
            "webhook": {"type": "string", "description": "变化时的 Webhook URL"},
        },
        "required": ["url"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=False,
)

SCAN_VULN_TOOL = CrawlToolDef(
    name="scan_vuln",
    description="扫描目标 URL 的安全漏洞（OWASP Top 10）。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要扫描的漏洞类别",
            },
            "depth": {"type": "number", "default": 1, "description": "扫描深度"},
        },
        "required": ["url"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=False,
)

SUPERVISOR_TOOL = CrawlToolDef(
    name="supervisor",
    description=(
        "智能爬取调度器（推荐入口）：编排 crawl→analyze→extract→save 的闭环协作。"
        "当用户要求爬取并提取某个网页数据时，优先调用此工具。"
        "它自动处理页面指纹缓存、蓝图生成、置信度反馈、自适应格式存储。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "instruction": {
                "type": "string",
                "default": "",
                "description": "用户指令（可选，例如: '提取所有文章标题和链接'）",
            },
        },
        "required": ["url"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=False,
)


# ==================== 工具集管理 ====================

class ToolRegistry:
    """工具注册表"""

    def __init__(self):
        self._tools: Dict[str, CrawlToolDef] = {}
        self._executors: Dict[str, Any] = {}  # tool_name → async callable

    def register(self, tool_def: CrawlToolDef, executor=None) -> None:
        """注册工具和可选的执行器"""
        self._tools[tool_def.name] = tool_def
        if executor:
            self._executors[tool_def.name] = executor

    def register_executor(self, name: str, executor) -> None:
        """注册工具执行器"""
        self._executors[name] = executor

    def get(self, name: str) -> Optional[CrawlToolDef]:
        """获取工具定义"""
        return self._tools.get(name)

    def get_executor(self, name: str) -> Optional[Any]:
        """获取工具执行器"""
        return self._executors.get(name)

    def list_tools(self) -> List[CrawlToolDef]:
        """列出所有工具"""
        return list(self._tools.values())

    def list_active_names(self, active_names: List[str] = None) -> List[str]:
        """列出活跃工具名"""
        if active_names:
            return [n for n in active_names if n in self._tools]
        return list(self._tools.keys())

    def get_all_executors(self) -> Dict[str, Any]:
        """获取所有已注册的执行器（name → callable）"""
        return dict(self._executors)


# ==================== 状态码 + 数据模型（Supervisor 协作用） ====================

# 工具间通信标准状态码（用整数而非异常，便于 LLM 理解）
STATUS_OK = 200               # 正常完成
STATUS_CACHED = 201           # 命中缓存
STATUS_LOW_CONFIDENCE = 422   # 置信度不足
STATUS_NEED_REANALYSIS = 410  # 需要重新分析页面结构
STATUS_FAILED = 500           # 执行失败

# 置信度阈值：低于此值触发 NEED_REANALYSIS
CONFIDENCE_THRESHOLD = 70


@dataclass
class ExtractionBlueprint:
    """提取蓝图：analyze 的产物，供 extract 复用

    缓存粒度：按 page_signature（页面布局指纹）缓存，避免重复 LLM 分析。
    """
    page_signature: str                       # 对应的页面指纹
    recommended_strategy: str = "css"         # 推荐策略: css / xpath / llm
    selectors: Dict[str, Any] = field(default_factory=dict)  # 候选选择器列表
    field_dependencies: Dict[str, str] = field(default_factory=dict)  # 字段间依赖
    confidence: float = 0.0                   # 蓝图自身置信度
    created_at: float = field(default_factory=time.time)
    # 失败字段记录（NEED_REANALYSIS 后重新分析时聚焦这些字段）
    failed_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_signature": self.page_signature,
            "recommended_strategy": self.recommended_strategy,
            "selectors": self.selectors,
            "field_dependencies": self.field_dependencies,
            "confidence": self.confidence,
            "failed_fields": self.failed_fields,
        }


class ExtractionBlueprintCache:
    """提取蓝图缓存（内存版，P6 再升级 MySQL 持久化）

    Key: page_signature (页面布局指纹)
    Value: ExtractionBlueprint

    设计：LRU 策略，默认容量 1000 个页面。
    """

    def __init__(self, max_size: int = 1000) -> None:
        from collections import OrderedDict
        self._cache: "OrderedDict[str, ExtractionBlueprint]" = OrderedDict()
        self._max_size = max_size

    def get(self, signature: str) -> Optional[ExtractionBlueprint]:
        """获取蓝图。命中时移到末尾（LRU）"""
        if signature in self._cache:
            self._cache.move_to_end(signature)
            return self._cache[signature]
        return None

    def set(self, blueprint: ExtractionBlueprint) -> None:
        """存入蓝图，超过容量淘汰最旧的"""
        sig = blueprint.page_signature
        if sig in self._cache:
            self._cache.move_to_end(sig)
        self._cache[sig] = blueprint
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)  # 淘汰最久未用

    def invalidate(self, signature: str) -> None:
        """使某个签名失效（页面结构变更时调用）"""
        self._cache.pop(signature, None)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


def compute_page_signature(html: str) -> str:
    """计算页面布局指纹

    提取关键 DOM 结构的哈希，忽略文本内容只看结构。
    相同布局的页面（如同一列表页的不同分页）指纹一致。
    """
    from bs4 import BeautifulSoup
    import hashlib as _hashlib

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""

    # 移除噪声标签
    for tag in soup(["script", "style", "noscript", "svg", "path"]):
        tag.decompose()

    # 提取结构骨架：tag.name + class（忽略 id/text/attrs）
    structure_parts: List[str] = []
    for tag in soup.find_all(["div", "section", "article", "ul", "ol", "li", "table", "tr", "td", "header", "footer", "main", "nav"]):
        classes = " ".join(tag.get("class", [])) if isinstance(tag.get("class"), list) else ""
        structure_parts.append(f"{tag.name}.{classes}")

    skeleton = "|".join(structure_parts)
    return _hashlib.md5(skeleton.encode("utf-8")).hexdigest()


# ==================== 工具执行器（P1-A） ====================

async def crawl_executor(args: Dict[str, Any]) -> Dict[str, Any]:
    """crawl 工具执行器：调用 Fetcher 抓取页面内容

    支持 httpx → curl_cffi → Playwright 三级回退（由 Fetcher 内部处理）。
    """
    from crawagent.core.fetcher import Fetcher
    from crawagent.core.errors import classify_error, error_to_dict

    url = args.get("url", "")
    if not url:
        return {"content": "Error: missing 'url' argument", "error": True}

    use_browser = args.get("use_browser", False)
    timeout = args.get("timeout", 30)
    extra_headers = args.get("headers")

    async with Fetcher() as fetcher:
        result = await fetcher.fetch(
            url=url,
            extra_headers=extra_headers,
            timeout=timeout,
            use_curl_cffi=True,  # 默认启用 curl_cffi
            use_browser=use_browser,  # 浏览器模式（Playwright）
        )

    if result.success:
        # 计算页面布局指纹，供后续工具做缓存决策
        signature = compute_page_signature(result.html)
        return {
            "content": result.html[:20000] if len(result.html) > 20000 else result.html,
            "html": result.html,  # 完整 HTML 供 extract 使用
            "status": result.status_code,
            "status_code": STATUS_OK,
            "url": result.url,
            "title": result.title,
            "strategy": result.strategy,
            "links_count": len(result.links),
            "page_signature": signature,
        }
    else:
        category = classify_error(status_code=result.status_code or 0, exc=None)
        return {
            "content": f"抓取失败: {result.error or '未知错误'}",
            "status": result.status_code,
            "status_code": STATUS_FAILED,
            "url": result.url,
            "error": True,
            **error_to_dict(category, result.status_code, result.error or "", result.strategy),
        }


async def extract_executor(args: Dict[str, Any]) -> Dict[str, Any]:
    """extract 工具执行器：自适应提取器（核心）

    不同于机械的三级回退，而是：
    1. 优先读蓝图缓存：检查传入的 extraction_blueprint，有则按蓝图策略提取
    2. 置信度反馈：提取完成后输出 confidence（0-100）
    3. 若 confidence < 70%，不直接跳 LLM 兜底，而是返回 NEED_REANALYSIS 状态码
       供 Supervisor 决策是否重新分析
    """
    from crawagent.core.extractor import CompositeExtractor, create_dynamic_schema
    from crawagent.core.models import Selectors, ExtractedItem

    html = args.get("html", "")
    if not html:
        return {
            "content": "Error: missing 'html' argument",
            "error": True,
            "status_code": STATUS_FAILED,
        }

    url = args.get("url", "")
    schema_data = args.get("schema", {})

    # 1. 优先从蓝图读取策略和选择器
    blueprint_data = args.get("blueprint")
    blueprint: Optional[ExtractionBlueprint] = None
    if blueprint_data is not None:
        if isinstance(blueprint_data, ExtractionBlueprint):
            blueprint = blueprint_data
        elif isinstance(blueprint_data, dict):
            blueprint = ExtractionBlueprint(
                page_signature=blueprint_data.get("page_signature", ""),
                recommended_strategy=blueprint_data.get("recommended_strategy", "css"),
                selectors=blueprint_data.get("selectors", {}),
                field_dependencies=blueprint_data.get("field_dependencies", {}),
                confidence=blueprint_data.get("confidence", 0.0),
                failed_fields=blueprint_data.get("failed_fields", []),
            )

    if blueprint:
        # 按蓝图推荐策略执行
        selectors_data = blueprint.selectors
        method = blueprint.recommended_strategy
    else:
        # 无蓝图：使用传入的 selectors 或默认
        selectors_data = args.get("selectors", {})
        method = args.get("method", "css")

    # 构造 Selectors
    selectors = Selectors(
        list_container=selectors_data.get("list_container", ""),
        item=selectors_data.get("item", ""),
        title=selectors_data.get("title", ""),
        url=selectors_data.get("url", ""),
        extra=selectors_data.get("extra", {}),
    )

    # 构造动态 Schema
    if schema_data:
        target_schema = create_dynamic_schema(schema_data)
    else:
        target_schema = ExtractedItem

    extractor = CompositeExtractor()
    try:
        results = extractor.extract(
            url=url,
            html=html,
            selectors=selectors,
            target_schema=target_schema,
        )
    except Exception as e:
        return {
            "content": f"提取失败: {e}",
            "error": True,
            "status_code": STATUS_FAILED,
        }

    items = [r.model_dump() for r in results]

    # 2. 计算置信度
    # 基础分：有结果 50 分，每条结果 +5（上限 80）
    # 选择器命中率高 +20
    confidence = 0.0
    if items:
        confidence = 50.0 + min(len(items) * 5, 30)
        # 蓝图策略与实际命中一致时加分
        if blueprint and blueprint.selectors.get("item"):
            confidence += 20
        confidence = min(confidence, 100.0)

    # 3. 置信度不足 → 返回 NEED_REANALYSIS（不直接 LLM 兜底）
    if confidence < CONFIDENCE_THRESHOLD:
        # 收集失败字段（值为空或异常的字段）
        failed_fields: List[str] = []
        if items:
            for key, val in items[0].items():
                if not val and key not in ("metadata",):
                    failed_fields.append(key)
        else:
            failed_fields = list(schema_data.keys()) if schema_data else ["title", "url", "content"]

        return {
            "content": f"置信度不足 ({confidence:.0f}/100)，需重新分析页面结构",
            "status_code": STATUS_NEED_REANALYSIS,
            "confidence": confidence,
            "failed_fields": failed_fields,
            "items_count": 0,
        }

    return {
        "content": _json.dumps(items, ensure_ascii=False, default=str)[:20000],
        "status_code": STATUS_OK,
        "confidence": confidence,
        "count": len(items),
        "method": method,
        "items": items,  # 完整数据供 save 使用
    }


async def analyze_executor(args: Dict[str, Any]) -> Dict[str, Any]:
    """analyze 工具执行器：预热分析器，输出 extraction_blueprint（提取蓝图）

    非每次调用：仅在项目启动或页面结构疑似变更时触发。
    产物 extraction_blueprint 包含：
    - 推荐策略（css / xpath / llm）
    - 候选选择器列表（带置信度）
    - 字段间依赖关系

    蓝图会被缓存，后续 extract 直接读取，避免重复"阅读理解"。
    """
    url = args.get("url", "")
    if not url:
        return {"content": "Error: missing 'url' argument", "error": True, "status_code": STATUS_FAILED}

    # 可选：传入已有签名，避免重复计算
    page_signature = args.get("page_signature", "")
    focus_fields = args.get("focus_fields", [])  # NEED_REANALYSIS 时聚焦的失败字段
    html = args.get("html", "")

    from crawagent.graph.site_analyzer import analyze_site_simple

    try:
        analysis = await analyze_site_simple(url)

        # 如果没有传入签名且有 HTML，计算签名
        if not page_signature and html:
            page_signature = compute_page_signature(html)

        # 构造提取蓝图（None → "" 避免 Selectors 校验失败）
        selectors_data = {
            "list_container": analysis.selectors.list_container or "",
            "item": analysis.selectors.item or "",
            "title": analysis.selectors.title or "",
            "url": analysis.selectors.url or "",
            "extra": analysis.selectors.extra or {},
        }

        # 字段间依赖关系（基于页面类型推断）
        field_deps: Dict[str, str] = {}
        if analysis.page_type.value == "product_list":
            field_deps["price"] = "currency_symbol"
            field_deps["url"] = "title"

        # 推荐策略：基于数据源
        strategy_map = {
            "dom": "css",
            "xhr_json": "css",  # JSON 仍可尝试 DOM 选择器
            "api": "llm",
            "graphql": "llm",
        }
        recommended = strategy_map.get(analysis.data_source.value, "css")

        blueprint = ExtractionBlueprint(
            page_signature=page_signature,
            recommended_strategy=recommended,
            selectors=selectors_data,
            field_dependencies=field_deps,
            confidence=analysis.confidence,
            failed_fields=list(focus_fields),
        )

        return {
            "content": _json.dumps(blueprint.to_dict(), ensure_ascii=False, default=str),
            "status_code": STATUS_OK,
            "blueprint": blueprint,
            "page_type": analysis.page_type.value,
            "data_source": analysis.data_source.value,
            "anti_bot_signs": [s.value for s in analysis.anti_bot_signs],
        }
    except Exception as e:
        return {
            "content": f"分析失败: {e}",
            "error": True,
            "status_code": STATUS_FAILED,
        }


async def save_executor(args: Dict[str, Any]) -> Dict[str, Any]:
    """save 工具执行器：智能存储路由器（P3 接入 FileOrganizer）

    四个聪明之处：
    1. 路径模板引擎：path 含 {domain}/{date}/{title} 等占位符时用 FileOrganizer 渲染
    2. 自适应格式：检测数据 Schema——扁平键值对存 CSV，嵌套存 JSON，富文本存 MD
    3. 增量去重：基于 id/url 字段哈希，跳过已存记录
    4. 检查点机制：每 10 条记录进度，支持断点续传
    """
    data = args.get("data") or args.get("items")
    content = args.get("content", "")
    path = args.get("path", "")
    url = args.get("url", "")
    title = args.get("title", "")
    fmt = args.get("format", "")  # 留空则自动推断
    append = args.get("append", False)

    # 优先使用 data；否则用 content
    if not data and content:
        data = content
    if not data or not path:
        return {
            "content": "Error: missing 'data'/'content' or 'path' argument",
            "error": True,
            "status_code": STATUS_FAILED,
        }

    # 路径模板渲染：path 含 { 占位符时用 FileOrganizer 渲染
    # 支持占位符：{domain}/{date}/{year}/{month}/{day}/{title}/{slug}/{ext}
    if "{" in path and "}" in path:
        try:
            from crawagent.output.organizer import FileOrganizer
            # 自动推断扩展名（用于模板 {ext}）
            auto_ext = ""
            if path.endswith(".md") or (fmt == "markdown"):
                auto_ext = "md"
            elif path.endswith(".json") or (fmt == "json"):
                auto_ext = "json"
            elif path.endswith(".csv") or (fmt == "csv"):
                auto_ext = "csv"
            elif "." in os.path.basename(path):
                auto_ext = path.rsplit(".", 1)[-1]
            else:
                auto_ext = "md"
            org = FileOrganizer(base_dir="./output")
            path = org.render(path, url=url, title=title or "untitled", ext=auto_ext)
        except Exception as e:
            logger.debug(f"路径模板渲染失败，用原 path: {e}")

    # 展开 ~ 和环境变量
    path = os.path.expanduser(os.path.expandvars(path))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # 同名文件去重（避免重复标题覆盖已有文件）
    if not append:
        try:
            from crawagent.output.organizer import unique_path
            path = unique_path(path)
        except Exception:
            pass

    # 1. 自适应格式推断（若未指定）
    if not fmt:
        fmt = _infer_format(data)
        # 根据格式调整扩展名（仅当 path 没有扩展名时才加）
        if not path.endswith(f".{fmt}") and "." not in os.path.basename(path):
            path = f"{path}.{fmt}"

    # 2. 增量去重
    records = _normalize_records(data)
    deduped, skipped = _deduplicate(records, path)

    if not deduped:
        return {
            "content": f"全部 {skipped} 条记录已存在，跳过写入",
            "status_code": STATUS_OK,
            "path": path,
            "format": fmt,
            "written": 0,
            "skipped": skipped,
        }

    # 3. 写入（带检查点）
    try:
        written = 0
        if fmt == "json":
            with open(path, "a" if append else "w", encoding="utf-8") as f:
                # JSON 数组或 NDJSON
                existing = []
                if append and os.path.exists(path):
                    try:
                        with open(path, "r", encoding="utf-8") as rf:
                            existing = _json.load(rf)
                    except Exception:
                        existing = []
                merged = (existing if isinstance(existing, list) else []) + deduped
                _json.dump(merged, f, ensure_ascii=False, indent=2)
                written = len(deduped)

        elif fmt == "csv":
            file_exists = os.path.exists(path)
            with open(path, "a", encoding="utf-8", newline="") as f:
                if deduped:
                    writer = _csv.DictWriter(f, fieldnames=deduped[0].keys())
                    if not file_exists:
                        writer.writeheader()
                    for i, rec in enumerate(deduped):
                        writer.writerow({k: str(v) for k, v in rec.items()})
                        # 检查点：每 10 条记录
                        if (i + 1) % 10 == 0:
                            f.flush()
                    written = len(deduped)

        else:  # markdown
            with open(path, "a" if append else "w", encoding="utf-8") as f:
                for i, rec in enumerate(deduped):
                    f.write(f"## {rec.get('title', rec.get('url', f'记录{i+1}'))}\n\n")
                    for k, v in rec.items():
                        if k not in ("title",):
                            f.write(f"**{k}**: {v}\n")
                    f.write("\n---\n\n")
                    if (i + 1) % 10 == 0:
                        f.flush()
                written = len(deduped)

        return {
            "content": f"已保存 {written} 条记录到 {path}（跳过 {skipped} 条重复）",
            "status_code": STATUS_OK,
            "path": path,
            "format": fmt,
            "written": written,
            "skipped": skipped,
        }
    except Exception as e:
        return {
            "content": f"保存失败: {e}",
            "error": True,
            "status_code": STATUS_FAILED,
        }


def _infer_format(data: Any) -> str:
    """根据数据 Schema 推断存储格式

    优先级：富文本 > 嵌套结构 > 扁平键值对
    """
    records = _normalize_records(data)
    if not records:
        return "markdown"

    first = records[0]

    # 1. 富文本检测：含多个长字符串字段（>200 字符）→ markdown
    text_fields = sum(1 for v in first.values() if isinstance(v, str) and len(v) > 200)
    if text_fields >= 2:
        return "markdown"

    # 2. 嵌套结构检测：含 dict/list → json
    has_nested = any(isinstance(v, (dict, list)) for v in first.values())
    if has_nested:
        return "json"

    # 3. 扁平键值对 → csv
    if len(first) <= 10:
        return "csv"

    # 4. 兜底
    return "json"


def _normalize_records(data: Any) -> List[Dict[str, Any]]:
    """将各种数据形式统一为 List[Dict]"""
    if isinstance(data, str):
        try:
            parsed = _json.loads(data)
            return _normalize_records(parsed)
        except Exception:
            return [{"content": data}]
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"content": str(r)} for r in data]
    return [{"content": str(data)}]


def _deduplicate(records: List[Dict], path: str) -> tuple:
    """基于 id/url 字段哈希去重

    返回 (deduped_records, skipped_count)
    """
    if not os.path.exists(path):
        return records, 0

    # 读取已存记录的哈希集合
    existing_hashes = set()
    try:
        if path.endswith(".json"):
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
                if isinstance(data, list):
                    for rec in data:
                        existing_hashes.add(_record_hash(rec))
        elif path.endswith(".csv"):
            with open(path, "r", encoding="utf-8") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    existing_hashes.add(_record_hash(dict(row)))
        # markdown 较难去重，跳过
    except Exception:
        pass

    deduped = []
    skipped = 0
    for rec in records:
        h = _record_hash(rec)
        if h in existing_hashes:
            skipped += 1
        else:
            deduped.append(rec)
            existing_hashes.add(h)

    return deduped, skipped


def _record_hash(rec: Dict) -> str:
    """计算记录的哈希，优先用 id/url 字段"""
    # 优先字段：id, url, guid
    key_fields = ("id", "url", "guid", "link", "_id")
    for kf in key_fields:
        if rec.get(kf):
            return _hashlib.md5(str(rec[kf]).encode()).hexdigest()
    # 无唯一字段：用全部内容哈希
    content = _json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
    return _hashlib.md5(content.encode()).hexdigest()


# ==================== CrawlSupervisor 调度器（工具间协作编排） ====================

class CrawlSupervisor:
    """监督者调度器：编排 crawl → analyze → extract → save 的闭环协作

    工具不再是平级的独立函数，而是通过 Supervisor 协调：
    1. crawl → 获得 page_signature + html
    2. 检查蓝图缓存
       - 命中 → 直接 extract
       - 未命中 → analyze 生成蓝图并缓存，再 extract
    3. extract
       - confidence >= 70% → save
       - NEED_REANALYSIS → 重新 analyze（传入失败字段），更新蓝图，重试（最多 2 次）
    4. save → 自适应格式 + 去重 + 检查点

    设计原则：
    - 尽量减少 LLM 调用，优先使用规则和缓存
    - 工具间通信使用标准状态码，而非抛出异常
    - Supervisor 是外部唯一入口，4 个工具不直接暴露给智能体
    """

    MAX_REANALYSIS_RETRIES = 2  # NEED_REANALYSIS 最多重试次数

    def __init__(
        self,
        blueprint_cache: Optional[ExtractionBlueprintCache] = None,
        output_dir: str = "./output",
    ) -> None:
        self.blueprint_cache = blueprint_cache or ExtractionBlueprintCache()
        self.output_dir = output_dir

    async def run(self, url: str, instruction: str = "", use_browser: bool = False) -> Dict[str, Any]:
        """主调度入口

        Args:
            url: 目标 URL
            instruction: 用户指令（可选，用于决定输出文件名）
            use_browser: 强制启用浏览器模式（AntiBot hook 注入时为 True）

        Returns:
            Dict 包含各阶段结果和最终状态
        """
        from loguru import logger

        result: Dict[str, Any] = {
            "url": url,
            "instruction": instruction,
            "stages": {},
        }

        # ========== 1. crawl ==========
        logger.info(f"[Supervisor] 1/5 crawl: {url} (use_browser={use_browser})")
        crawl_args: Dict[str, Any] = {"url": url}
        if use_browser:
            crawl_args["use_browser"] = True
        crawl_result = await crawl_executor(crawl_args)
        result["stages"]["crawl"] = _summarize(crawl_result, ["status", "strategy", "page_signature", "title"])

        if crawl_result.get("status_code") != STATUS_OK:
            result["status_code"] = STATUS_FAILED
            result["content"] = f"抓取失败: {crawl_result.get('content', '')}"
            return result

        html = crawl_result.get("html", crawl_result.get("content", ""))
        page_signature = crawl_result.get("page_signature", "")

        # ========== 2. clean：去噪 + 生成干净 Markdown（P2） ==========
        logger.info(f"[Supervisor] 2/5 clean: {url}")
        from crawagent.core.content_filter import PruningContentFilter
        from crawagent.core.markdown_generator import MarkdownGenerator

        clean_html = PruningContentFilter().filter_content(html)
        markdown = MarkdownGenerator(max_length=200_000).generate(
            clean_html, url=url, title=crawl_result.get("title", ""), clean=False
        )
        result["stages"]["clean"] = {
            "html_length": len(clean_html),
            "markdown_length": len(markdown),
        }
        result["clean_html"] = clean_html
        result["markdown"] = markdown

        # ========== 3. 检查蓝图缓存 ==========
        blueprint = self.blueprint_cache.get(page_signature) if page_signature else None
        if blueprint:
            logger.info(f"[Supervisor] 3/5 蓝图缓存命中 (signature={page_signature[:8]}...)")
            result["stages"]["blueprint"] = {"status": "cached", "confidence": blueprint.confidence}
        else:
            logger.info(f"[Supervisor] 3/5 蓝图未命中，调用 analyze 生成蓝图")
            analyze_result = await analyze_executor({
                "url": url,
                "html": clean_html,
                "page_signature": page_signature,
            })
            if analyze_result.get("status_code") == STATUS_OK:
                blueprint = analyze_result.get("blueprint")
                if blueprint:
                    self.blueprint_cache.set(blueprint)
                    result["stages"]["blueprint"] = {
                        "status": "generated",
                        "confidence": blueprint.confidence,
                        "strategy": blueprint.recommended_strategy,
                    }
                else:
                    result["stages"]["blueprint"] = {"status": "failed", "error": "未生成蓝图"}
            else:
                result["stages"]["blueprint"] = {"status": "failed", "error": analyze_result.get("content", "")}

        # ========== 4. extract（带 NEED_REANALYSIS 重试） ==========
        retry_count = 0
        extract_result: Dict[str, Any] = {}
        while retry_count <= self.MAX_REANALYSIS_RETRIES:
            logger.info(f"[Supervisor] 4/5 extract (attempt {retry_count + 1})")
            extract_args = {
                "html": clean_html,
                "url": url,
                "blueprint": blueprint.to_dict() if blueprint else None,
            }
            extract_result = await extract_executor(extract_args)
            status = extract_result.get("status_code")

            if status == STATUS_OK:
                # 置信度足够，进入 save
                result["stages"]["extract"] = _summarize(
                    extract_result, ["confidence", "count", "method"]
                )
                logger.info(
                    f"[Supervisor] extract 成功 (confidence={extract_result.get('confidence', 0):.0f})"
                )
                break

            if status == STATUS_NEED_REANALYSIS and retry_count < self.MAX_REANALYSIS_RETRIES:
                # 因果推理：自动触发 analyze 重新扫描失败字段
                failed_fields = extract_result.get("failed_fields", [])
                logger.warning(
                    f"[Supervisor] NEED_REANALYSIS (confidence={extract_result.get('confidence', 0):.0f}), "
                    f"重新分析失败字段: {failed_fields}"
                )
                reanalyze_result = await analyze_executor({
                    "url": url,
                    "html": clean_html,
                    "page_signature": page_signature,
                    "focus_fields": failed_fields,
                })
                if reanalyze_result.get("status_code") == STATUS_OK:
                    new_bp = reanalyze_result.get("blueprint")
                    if new_bp:
                        # 合并失败字段信息到新蓝图
                        new_bp.failed_fields = failed_fields
                        self.blueprint_cache.set(new_bp)
                        blueprint = new_bp
                        logger.info("[Supervisor] 蓝图已更新，重试 extract")
                retry_count += 1
                continue

            # 重试次数耗尽或非 NEED_REANALYSIS 错误
            result["stages"]["extract"] = _summarize(
                extract_result, ["confidence", "failed_fields"]
            )
            result["status_code"] = status or STATUS_FAILED
            result["content"] = extract_result.get("content", "提取失败")
            return result

        # ========== 5. save ==========
        logger.info(f"[Supervisor] 5/5 save")
        items = extract_result.get("items", [])
        # 保存 Markdown 内容（若有）
        markdown_content = result.get("markdown", "")
        title = crawl_result.get("title", "") or ""
        # 推断输出路径：用 FileOrganizer 模板引擎（按 domain/date/title 组织）
        output_path = self._decide_output_path(url, title=title, ext="md")
        save_args = {
            "data": items,
            "path": output_path,
            "url": url,
            "title": title,
            "format": "markdown",
        }
        # 若有 Markdown 内容且 items 为空，用 content 模式保存
        if markdown_content and not items:
            save_args = {
                "content": markdown_content,
                "path": output_path,
                "url": url,
                "title": title,
                "format": "markdown",
            }
        save_result = await save_executor(save_args)
        result["stages"]["save"] = _summarize(
            save_result, ["path", "format", "written", "skipped"]
        )
        result["status_code"] = save_result.get("status_code", STATUS_OK)
        result["content"] = save_result.get("content", "")
        result["output_path"] = save_result.get("path", "")
        return result

    def _decide_output_path(self, url: str, title: str = "", ext: str = "md") -> str:
        """根据 URL 推断输出文件路径（P3：用 FileOrganizer 模板引擎）

        默认模板：articles/{domain}/{date}/{title}.{ext}
        - 同站点文章归到同一 domain 目录
        - 按日期分桶
        - 标题自动转义非法字符
        """
        try:
            from crawagent.output.organizer import FileOrganizer
            org = FileOrganizer(base_dir=self.output_dir)
            safe_title = title or "untitled"
            path = org.render(
                "articles/{domain}/{date}/{title}.{ext}",
                url=url,
                title=safe_title,
                ext=ext,
            )
            return path
        except Exception as e:
            logger.debug(f"FileOrganizer 渲染失败，回退原逻辑: {e}")
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace("www.", "")
            path_part = parsed.path.strip("/").replace("/", "_") or "index"
            filename = f"{domain}_{path_part}"[:60]
            return os.path.join(self.output_dir, filename)


def _summarize(result: Dict, keys: List[str]) -> Dict[str, Any]:
    """从结果字典中提取关键字段，避免完整 HTML 进入日志"""
    return {k: result.get(k) for k in keys if k in result}


# ==================== LangChain 工具转换（P1-B） ====================

# 暴露给 LLM 的工具名
# 注意：crawl/extract/analyze/save 不直接暴露给 LLM，只通过 Supervisor 编排
# LLM 只能看到 "supervisor" 这一个入口工具
LLM_CALLABLE_TOOLS = {"supervisor", "search"}


_JSON_TYPE_MAP = {
    "string": str,
    "str": str,
    "integer": int,
    "int": int,
    "number": float,
    "float": float,
    "boolean": bool,
    "bool": bool,
    "array": list,
    "object": dict,
}


def _build_args_schema(tool_def: CrawlToolDef) -> Type[BaseModel]:
    """从 CrawlToolDef.parameters 构造 Pydantic args schema"""
    params = tool_def.parameters or {}
    properties = params.get("properties", {})
    required = set(params.get("required", []))

    fields: Dict[str, Any] = {}
    for name, prop in properties.items():
        json_type = prop.get("type", "string") if isinstance(prop, dict) else "string"
        py_type = _JSON_TYPE_MAP.get(json_type, str)
        desc = prop.get("description", "") if isinstance(prop, dict) else ""
        if name in required:
            fields[name] = (py_type, Field(..., description=desc))
        else:
            default = prop.get("default") if isinstance(prop, dict) else None
            fields[name] = (Optional[py_type], Field(default=default, description=desc))

    return create_model(
        f"{tool_def.name}_Args",
        **fields,
        __config__=ConfigDict(extra="allow"),
    )


def to_langchain_tools(tool_defs: List[CrawlToolDef]) -> List[Any]:
    """将 CrawlToolDef 列表转为 LangChain BaseTool 列表（用于 bind_tools）

    设计原则：Supervisor 是外部唯一入口，4 个工具不直接暴露给智能体。
    - supervisor: 编排 crawl → analyze → extract → save 的闭环协作
    - search: 独立工具（搜索能力，不依赖页面流程）
    """
    from langchain_core.tools import StructuredTool

    tools: List[Any] = []
    for td in tool_defs:
        if td.name not in LLM_CALLABLE_TOOLS:
            continue
        args_schema = _build_args_schema(td)

        # 占位函数：实际执行在 loop 中由 executor 完成
        def _placeholder(**_kwargs) -> str:
            return ""

        st = StructuredTool.from_function(
            func=_placeholder,
            name=td.name,
            description=td.description,
            args_schema=args_schema,
        )
        tools.append(st)
    return tools


# ==================== 默认工具注册表 ====================

def create_default_tools(output_dir: str = "./output") -> ToolRegistry:
    """创建默认工具注册表（含执行器）

    Args:
        output_dir: supervisor 输出目录
    """
    registry = ToolRegistry()

    # 注册内部工具定义 + 执行器（不直接暴露给 LLM）
    registry.register(CRAWL_TOOL, executor=crawl_executor)
    registry.register(EXTRACT_TOOL, executor=extract_executor)
    registry.register(ANALYZE_TOOL, executor=analyze_executor)
    registry.register(SAVE_TOOL, executor=save_executor)

    # 注册 Supervisor（外部唯一入口，暴露给 LLM）
    supervisor = CrawlSupervisor(output_dir=output_dir)

    async def supervisor_executor(args: Dict[str, Any]) -> Dict[str, Any]:
        """Supervisor 执行器：调用 CrawlSupervisor.run"""
        url = args.get("url", "")
        instruction = args.get("instruction", "")
        use_browser = bool(args.get("use_browser", False))
        if not url:
            return {"content": "Error: missing 'url' argument", "error": True, "status_code": STATUS_FAILED}
        return await supervisor.run(url=url, instruction=instruction, use_browser=use_browser)

    registry.register(SUPERVISOR_TOOL, executor=supervisor_executor)

    # search 执行器：搜索本地已保存数据（output_dir 下的 json/csv/md/jsonl 文件）
    async def search_executor(args: Dict[str, Any]) -> Dict[str, Any]:
        """search 工具执行器：本地全文搜索已爬取保存的数据"""
        query = (args.get("query") or "").strip()
        scope = args.get("scope", "local")
        limit = int(args.get("limit", 10))
        if not query:
            return {"content": "Error: missing 'query' argument", "error": True, "status_code": STATUS_FAILED}

        if scope == "web":
            return {
                "content": "互联网搜索暂未实现（计划在 P2 阶段接入）。",
                "status_code": STATUS_OK,
                "query": query,
                "scope": "web",
                "results": [],
            }

        # 本地搜索：扫描 output_dir 下所有数据文件
        import glob
        from pathlib import Path

        out_dir = Path(output_dir)
        matches: List[Dict[str, Any]] = []
        if out_dir.exists():
            patterns = ("*.json", "*.jsonl", "*.csv", "*.md")
            files: List[str] = []
            for pat in patterns:
                files.extend(glob.glob(str(out_dir / "**" / pat), recursive=True))
            q_lower = query.lower()
            for fp in files:
                try:
                    text = Path(fp).read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if q_lower in text.lower():
                    # 找到包含关键词的行/记录
                    for line in text.splitlines():
                        if q_lower in line.lower():
                            matches.append({"file": fp, "snippet": line[:200]})
                            if len(matches) >= limit:
                                break
                if len(matches) >= limit:
                    break

        return {
            "content": f"本地搜索 '{query}'：命中 {len(matches)} 条记录（扫描 {out_dir}）"
                       if matches else f"本地搜索 '{query}'：未找到匹配记录",
            "status_code": STATUS_OK,
            "query": query,
            "scope": "local",
            "results": matches,
        }

    registry.register(SEARCH_TOOL, executor=search_executor)
    registry.register(MONITOR_TOOL)   # monitor 暂无执行器
    registry.register(SCAN_VULN_TOOL) # scan_vuln 暂无执行器

    return registry
