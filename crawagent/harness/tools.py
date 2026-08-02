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

import asyncio
import csv as _csv
import hashlib as _hashlib
import json as _json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field, create_model, ConfigDict

from loguru import logger

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
            "limit": {"type": "number", "default": 10, "description": "返回结果数（多引擎时按顺序合并去重后的总数）"},
            "engine": {
                "type": "string",
                "enum": ["duckduckgo", "bing"],
                "default": "duckduckgo",
                "description": "互联网搜索引擎（scope=web 时生效）",
            },
            "engines": {
                "type": "array",
                "items": {"type": "string", "enum": ["duckduckgo", "bing"]},
                "description": "多个搜索引擎并存，并行查询后合并去重（scope=web 时生效，优先于 engine）",
            },
        },
        "required": ["query"],
    },
    exec_mode=ToolExecMode.PARALLEL,
    replay_safe=True,
)

MONITOR_TOOL = CrawlToolDef(
    name="monitor",
    description="监控目标 URL 的变化。设置监控频率和告警条件，变化时通过 Webhook 通知。"
                "支持字段级监控（price/stock）和整体内容变化检测。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "监控目标 URL"},
            "name": {"type": "string", "description": "监控任务名称（便于识别）"},
            "interval_minutes": {"type": "number", "default": 360, "description": "监控间隔（分钟），默认 6 小时"},
            "watch_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "监控字段列表（如 [\"price\", \"stock\"]），从 JSON-LD/Meta 提取",
            },
            "css_selector": {"type": "string", "description": "监控特定元素的 CSS 选择器（如 .price-tag）"},
            "webhook": {"type": "string", "description": "变化时的 Webhook 通知 URL"},
            "alert_cooldown_minutes": {"type": "number", "default": 60, "description": "告警冷却（分钟），同 URL 冷却内不重复告警"},
        },
        "required": ["url"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=False,
)

CHECK_CHANGE_TOOL = CrawlToolDef(
    name="check_change",
    description="立即检查监控目标是否有变化。返回变化详情（字段对比 + diff 摘要）。"
                "用于手动触发监控检查，不依赖调度器。",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "监控任务 ID"},
            "url": {"type": "string", "description": "直接指定 URL（无需 task_id，临时检查）"},
            "watch_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "监控字段列表（临时检查时使用）",
            },
            "css_selector": {"type": "string", "description": "CSS 选择器（临时检查时使用）"},
        },
    },
    exec_mode=ToolExecMode.PARALLEL,
    replay_safe=True,
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

FIX_ISSUE_TOOL = CrawlToolDef(
    name="fix_issue",
    description="根据漏洞扫描结果生成修复补丁。输入漏洞 ID 列表或扫描任务 ID，"
                "输出 unified diff 格式的补丁文件，可保存到指定路径。",
    parameters={
        "type": "object",
        "properties": {
            "scan_task_id": {"type": "string", "description": "扫描任务 ID（生成该任务所有漏洞的补丁）"},
            "vulnerability_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "指定漏洞 ID 列表（仅生成这些漏洞的补丁）",
            },
            "output_dir": {
                "type": "string",
                "default": "./output/security_patches",
                "description": "补丁文件保存目录",
            },
        },
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
        "当用户要求深度爬取/整站抓取/前 N 页时，自动切换到 deep_crawl 引擎。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "instruction": {
                "type": "string",
                "default": "",
                "description": "用户指令（可选，例如: '提取所有文章标题和链接'，含'深度爬取/整站/前 N 页'时自动深爬）",
            },
            "max_pages": {
                "type": "integer",
                "default": 0,
                "description": "深爬页数上限（>1 时启用 deep_crawl 引擎；0 表示按指令意图判断）",
            },
            "max_depth": {
                "type": "integer",
                "default": 3,
                "description": "深爬最大深度（层）",
            },
        },
        "required": ["url"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=False,
)

HARVEST_API_TOOL = CrawlToolDef(
    name="harvest_api",
    description=(
        "浏览器 API 捕获工具（强风控/JS 签名站点专用）：在浏览器中打开页面，"
        "拦截站点前端发出的所有 XHR/fetch 数据接口响应（签名参数由浏览器 JS 自动计算，"
        "无需人工逆向签名算法）。滚动页面触发分页懒加载，自动从 JSON 响应中提取"
        "结构化数据列表与媒体直链（视频/音频）。"
        "当常规 crawl/extract 被风控拦截（验证码、签名参数、壳页）时使用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标页面 URL"},
            "api_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "额外的 API 路径特征（如 '/aweme/v1'、'feed'），可选",
            },
            "max_scrolls": {
                "type": "integer",
                "default": 5,
                "description": "滚动轮数（触发分页/懒加载 API），0 表示不滚动",
            },
        },
        "required": ["url"],
    },
    exec_mode=ToolExecMode.SEQUENTIAL,
    replay_safe=True,
)

DEEP_CRAWL_TOOL = CrawlToolDef(
    name="deep_crawl",
    description=(
        "深度爬取工具：对整站/多页进行 BFS/DFS/Best-First 遍历，收集指定页数内的页面产物并批量保存。"
        "当用户要求'深度爬取/整站抓取/抓取前 N 页/批量抓取/深爬'时使用。"
        "返回每页的 URL/标题/深度/状态码摘要，页面 Markdown 产物自动落盘到 output/deep_crawl/。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标种子 URL（起始页）"},
            "strategy": {
                "type": "string",
                "enum": ["bfs", "dfs", "best_first"],
                "default": "bfs",
                "description": "遍历策略：bfs 广度优先 / dfs 深度优先 / best_first 按价值打分优先",
            },
            "max_pages": {"type": "integer", "default": 20, "description": "最多抓取页数"},
            "max_depth": {"type": "integer", "default": 3, "description": "最大抓取深度（层）"},
            "same_domain_only": {"type": "boolean", "default": True, "description": "是否仅抓取同域名页面"},
            "instruction": {
                "type": "string",
                "default": "",
                "description": "用户指令（可选，用于保存路径组织）",
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

    # 0. 图片专用快路径：method=images 直接走 extract_images（不依赖蓝图，规则驱动）
    _method = (args.get("method") or "").lower()
    if _method in ("images", "image", "imgs", "img"):
        extractor = CompositeExtractor()
        try:
            imgs = extractor.extract_images(
                url=url,
                html=html,
                min_width=int(args.get("min_width") or 50),
                min_height=int(args.get("min_height") or 50),
                max_images=int(args.get("max_images") or 500),
            )
        except Exception as e:
            return {
                "content": f"图片提取失败: {e}",
                "error": True,
                "status_code": STATUS_FAILED,
            }
        items = imgs
        count = len(items)
        # 置信度：有懒加载属性/meta/jsonld 命中 → 高，纯背景图 → 低
        kinds = [i.get("kind", "") for i in items]
        hi_ratio = sum(1 for k in kinds if k in ("lazy", "srcset", "og", "twitter", "jsonld")) / max(count, 1)
        confidence = min(100.0, (30.0 + min(count * 5, 50) + int(hi_ratio * 20)))
        if count == 0:
            return {
                "content": "未从页面中提取到图片",
                "status_code": STATUS_NEED_REANALYSIS,
                "confidence": 0.0,
                "failed_fields": ["images"],
                "items_count": 0,
                "method": "images",
                "hints": ["请尝试 use_browser=True 重新抓取（图片可能是 JS 懒加载）"],
            }
        return {
            "content": f"提取 {count} 张图片（预览前 5 张）: "
            + _json.dumps(items[:5], ensure_ascii=False, default=str)[:2000],
            "status_code": STATUS_OK,
            "confidence": confidence,
            "count": count,
            "method": "images",
            "items": items,
        }

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
        # table 策略（analyze 表格检测推荐）走单策略提取；其余保持默认回退链
        extract_kwargs = {}
        if method == "table":
            extract_kwargs["strategy"] = "table"
        results = extractor.extract(
            url=url,
            html=html,
            selectors=selectors,
            target_schema=target_schema,
            **extract_kwargs,
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

        # 规则增强：表格结构检测（LLM 可能推荐 css，但 <table> 页面 css 提取只会取到碎片）
        if html and "</table>" in html.lower():
            from crawagent.core.extractor import CompositeExtractor
            if CompositeExtractor._has_table(html):
                recommended = "table"
                logger.info("[Analyze] 检测到表格结构 → recommended_strategy=table")

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

    # 3. 图片下载：检测 records 中的图片字段并下载到同级 images/ 子目录
    #    把本地相对路径回填到 record 的 local_images 字段
    image_dl_result = await _maybe_download_images(deduped, path)

    # 4. 写入（带检查点）
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

        img_info = ""
        if image_dl_result["downloaded"] > 0 or image_dl_result["failed"] > 0:
            img_info = f"，下载图片 {image_dl_result['downloaded']} 张"
            if image_dl_result["failed"] > 0:
                img_info += f"（失败 {image_dl_result['failed']}）"

        return {
            "content": f"已保存 {written} 条记录到 {path}（跳过 {skipped} 条重复{img_info}）",
            "status_code": STATUS_OK,
            "path": path,
            "format": fmt,
            "written": written,
            "skipped": skipped,
            "images_downloaded": image_dl_result["downloaded"],
            "images_failed": image_dl_result["failed"],
            "images_dir": image_dl_result["images_dir"],
        }
    except Exception as e:
        return {
            "content": f"保存失败: {e}",
            "error": True,
            "status_code": STATUS_FAILED,
        }


async def deep_crawl_executor(args: Dict[str, Any]) -> Dict[str, Any]:
    """deep_crawl 工具执行器：整站/多页深度爬取

    使用 DeepCrawler 引擎（BFS/DFS/Best-First）遍历指定页数内的页面，
    每页自动生成干净 Markdown 并批量落盘到 output/deep_crawl/{domain}/{date}/，
    返回统计 + 页面摘要（URL/标题/深度/状态码/本地文件路径）。

    Args:
        url: 目标种子 URL
        strategy: bfs / dfs / best_first
        max_pages: 最多抓取页数
        max_depth: 最大抓取深度
        same_domain_only: 是否仅抓同域名
        instruction: 用户指令（用于保存路径组织）
    """
    from crawagent.core.deep_crawl import DeepCrawler, DeepCrawlStrategy
    from crawagent.core.fetcher import Fetcher

    url = (args.get("url") or "").strip()
    if not url:
        return {"content": "Error: missing 'url' argument", "error": True, "status_code": STATUS_FAILED}

    strategy_name = str(args.get("strategy", "bfs")).lower()
    strategy = {
        "bfs": DeepCrawlStrategy.BFS,
        "dfs": DeepCrawlStrategy.DFS,
        "best_first": DeepCrawlStrategy.BEST_FIRST,
    }.get(strategy_name, DeepCrawlStrategy.BFS)

    max_pages = max(1, int(args.get("max_pages", 20)))
    max_depth = max(1, int(args.get("max_depth", 3)))
    same_domain_only = bool(args.get("same_domain_only", True))
    instruction = str(args.get("instruction", "") or "")
    output_dir = str(args.get("output_dir", "./output"))

    # Fetcher 懒初始化客户端；DeepCrawler 内部调用 fetcher.fetch(url)
    fetcher = Fetcher()
    crawler = DeepCrawler(
        fetcher=fetcher,
        strategy=strategy,
        max_pages=max_pages,
        max_depth=max_depth,
        same_domain_only=same_domain_only,
    )

    try:
        result = await crawler.crawl(url)
    except Exception as e:
        logger.exception(f"[DeepCrawl] 执行异常: {e}")
        return {"content": f"深爬执行失败: {e}", "error": True, "status_code": STATUS_FAILED}
    finally:
        try:
            await fetcher.close()
        except Exception:
            pass

    # ---- 批量保存页面产物（Markdown 落盘）----
    from pathlib import Path
    from urllib.parse import urlparse
    from datetime import date

    from crawagent.core.content_filter import PruningContentFilter
    from crawagent.core.markdown_generator import MarkdownGenerator

    domain = (urlparse(url).netloc or "site").replace("www.", "")
    out_root = Path(output_dir) / "deep_crawl" / domain
    date_str = date.today().isoformat()
    saved: List[Dict[str, Any]] = []
    _ILLEGAL = '\\/:*?"<>|'

    for idx, page in enumerate(result.pages[:max_pages]):
        entry: Dict[str, Any] = {
            "url": page.url,
            "title": page.title,
            "status_code": page.status_code,
            "depth": page.depth,
            "error": page.error or "",
            "parent_url": page.parent_url,
        }
        if page.html and 200 <= page.status_code < 300:
            try:
                clean_html = PruningContentFilter().filter_content(page.html)
                md = MarkdownGenerator(max_length=200_000).generate(
                    clean_html, url=page.url, title=page.title, clean=False
                )
                safe_title = "".join(c for c in (page.title or "").strip()[:60] if c not in _ILLEGAL).strip()
                safe_title = safe_title or f"page_{idx + 1}"
                file_path = out_root / date_str / f"{idx + 1:03d}_{safe_title}.md"
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(md, encoding="utf-8")
                entry["file"] = str(file_path)
            except Exception as e:
                entry["error"] = f"markdown 生成失败: {e}"
        saved.append(entry)

    stats = result.stats
    summary_lines = [
        result.to_summary(),
        f"已保存 {len(saved)} 个页面产物 → {out_root}",
        "",
        "页面列表（前 20 条）：",
    ]
    for p in saved[:20]:
        summary_lines.append(
            f"  [{p['status_code']}] d{p['depth']} {p['title'] or '(无标题)'} {p['url']}"
        )
    if len(saved) > 20:
        summary_lines.append(f"  ... 共 {len(saved)} 页")

    # 失败页面 URL 写入 frontier 重试队列（P1-4：断点续抓 / 失败重试），
    # 调用方可后续通过 /api/agent/resume 恢复
    retry_queue: List[str] = []
    try:
        from crawagent.core.frontier import SQLiteFrontier
        frontier = SQLiteFrontier()
        await frontier.initialize()
        failed_pages = [
            p for p in result.pages
            if p.error or not (200 <= p.status_code < 400)
        ]
        for p in failed_pages[:50]:  # 限制入队数量
            added = await frontier.add_url(
                url=p.url, depth=p.depth, parent_url=p.parent_url, priority=0
            )
            if added:
                retry_queue.append(p.url)
        await frontier.close()
        if retry_queue:
            summary_lines.append(f"已写入重试队列 {len(retry_queue)} 个失败 URL（可 /api/agent/resume 恢复）")
    except Exception as e:
        logger.debug(f"[DeepCrawl] 失败 URL 入队失败: {e}")

    return {
        "content": "\n".join(summary_lines),
        "status_code": STATUS_OK,
        "stats": {
            "strategy": strategy.value,
            "pages_crawled": stats.pages_crawled,
            "urls_enqueued": stats.urls_enqueued,
            "urls_filtered": stats.urls_filtered,
            "urls_deduped": stats.urls_deduped,
            "errors": stats.errors,
            "max_depth_reached": stats.max_depth_reached,
            "duration_seconds": round(stats.duration_seconds, 2),
        },
        "pages": saved,
        "saved_count": len(saved),
        "output_dir": str(out_root),
        "retry_queue": retry_queue,
        "retry_queue_count": len(retry_queue),
    }


async def harvest_api_executor(args: Dict[str, Any]) -> Dict[str, Any]:
    """harvest_api 工具执行器：浏览器拦截站点 API 响应（强风控/JS 签名站点）

    签名参数由浏览器 JS 自动计算，无需人工逆向；滚动触发分页；
    自动提取结构化列表与媒体直链。
    """
    from crawagent.core.api_harvester import BrowserAPIHarvester

    url = args.get("url", "")
    if not url:
        return {"content": "Error: missing 'url' argument", "error": True, "status_code": STATUS_FAILED}

    api_patterns = args.get("api_patterns") or []
    if isinstance(api_patterns, str):
        api_patterns = [p.strip() for p in api_patterns.split(",") if p.strip()]
    max_scrolls = int(args.get("max_scrolls", 5) or 5)

    try:
        harvester = BrowserAPIHarvester()
        result = await harvester.harvest(
            url,
            api_patterns=api_patterns,
            max_scrolls=max_scrolls,
        )
    except Exception as e:
        logger.exception(f"[HarvestAPI] 执行失败: {e}")
        return {
            "content": f"harvest_api 执行失败: {e}",
            "error": True,
            "status_code": STATUS_FAILED,
            "url": url,
        }

    lines = [
        f"API 捕获完成: {url}",
        f"  API 响应: {len(result.api_calls)} 条",
        f"  结构化数据: {len(result.items)} 条",
        f"  媒体直链: {len(result.media_urls)} 条",
        f"  风控特征: {'检测到' if result.risk_detected else '无'}",
    ]
    if result.media_urls:
        lines.append("  媒体直链（前 10 条）:")
        for m in result.media_urls[:10]:
            lines.append(f"    [{m['key']}] {m['url'][:120]}")
    if result.items:
        lines.append("  数据样例（前 3 条）:")
        import json as _json
        for it in result.items[:3]:
            lines.append("    " + _json.dumps(it, ensure_ascii=False)[:200])

    return {
        "content": "\n".join(lines),
        "status_code": STATUS_OK if result.success else STATUS_FAILED,
        "url": url,
        "api_calls": [
            {"url": c.url, "status": c.status} for c in result.api_calls[:50]
        ],
        "items": result.items[:200],
        "media_urls": result.media_urls[:100],
        "items_count": len(result.items),
        "media_count": len(result.media_urls),
        "risk_detected": result.risk_detected,
        "error": result.error,
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


# ==================== 图片下载集成（P3 收尾） ====================

# 图片字段命名约定（大小写不敏感匹配），兼容主流提取器输出
_IMAGE_LIST_FIELDS = (
    "images", "image_urls", "thumbnails", "imgs", "photos", "pictures", "pics",
    "srcs",
)
_IMAGE_SINGLE_FIELDS = (
    "image", "image_url", "img", "img_url",
    "thumbnail", "thumbnail_url", "cover", "logo", "avatar",
    "src",  # extract_images() 标准输出主字段
    "data_src", "data-src", "image_src", "href",
)


async def _maybe_download_images(
    records: List[Dict], main_path: str
) -> Dict[str, Any]:
    """检测 records 中的图片字段并下载到主文件同级 images/ 子目录。

    把下载后的本地相对路径回填到 record 的 local_images 字段。
    下载失败不阻塞主流程，仅 debug 日志记录。

    Returns:
        {"downloaded": int, "failed": int, "images_dir": str, "paths": List[str]}
    """
    empty = {"downloaded": 0, "failed": 0, "images_dir": "", "paths": []}

    # 1. 收集所有图片 URL（大小写不敏感匹配字段名）
    download_tasks: List[tuple] = []  # [(record, url, title_hint)]
    for rec in records:
        if not isinstance(rec, dict):
            continue
        title_hint = str(rec.get("title") or rec.get("name") or rec.get("id") or "")
        # list 字段：images/image_urls/thumbnails/...
        for field in _IMAGE_LIST_FIELDS:
            val = _get_case_insensitive(rec, field)
            if isinstance(val, list):
                for i, url in enumerate(val):
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        hint = f"{title_hint}_{i}" if title_hint else str(i)
                        download_tasks.append((rec, url, hint))
        # 单值字段：image/image_url/cover/...
        for field in _IMAGE_SINGLE_FIELDS:
            val = _get_case_insensitive(rec, field)
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                download_tasks.append((rec, val, title_hint or "image"))

    if not download_tasks:
        return empty

    # 2. 创建 images/ 子目录（与主文件同级）
    from loguru import logger
    base_dir = os.path.dirname(os.path.abspath(main_path))
    images_dir = os.path.join(base_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    # 3. 串行下载（避免对目标站压力过大）
    from crawagent.output.media_downloader import MediaDownloader
    dl = MediaDownloader(base_dir=images_dir)

    downloaded = 0
    failed = 0
    paths: List[str] = []
    for rec, url, hint in download_tasks:
        # 失败重试机制（P1-4）：每张图最多尝试 3 次（1 次 + 2 次重试），
        # 短暂退避后重试，容忍瞬时网络抖动/限流
        result: Dict[str, Any] = {"success": False, "error": "not attempted"}
        for attempt in range(3):
            try:
                result = await dl.download(url, title=hint)
                if result.get("success"):
                    break
            except Exception as e:
                result = {"success": False, "error": str(e)}
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
        if result.get("success"):
            downloaded += 1
            paths.append(result["path"])
            # 回填相对路径（便于跨机器使用）
            rel_path = os.path.relpath(result["path"], base_dir)
            rec.setdefault("local_images", []).append(rel_path)
        else:
            failed += 1
            logger.debug(f"图片下载失败（已重试） {url}: {result.get('error')}")

    return {
        "downloaded": downloaded,
        "failed": failed,
        "images_dir": images_dir,
        "paths": paths,
    }


def _get_case_insensitive(d: Dict, key: str) -> Any:
    """大小写不敏感获取 dict 字段值。"""
    if not isinstance(d, dict):
        return None
    if key in d:
        return d[key]
    for k in d.keys():
        if isinstance(k, str) and k.lower() == key.lower():
            return d[k]
    return None


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
        profile_store: Optional["SiteProfileStore"] = None,  # noqa: F821
    ) -> None:
        self.blueprint_cache = blueprint_cache or ExtractionBlueprintCache()
        self.output_dir = output_dir
        self._profile_store = profile_store

    async def run(self, url: str, instruction: str = "", use_browser: bool = False,
                  max_pages: int = 0, max_depth: int = 3) -> Dict[str, Any]:
        """主调度入口

        Args:
            url: 目标 URL
            instruction: 用户指令（可选，用于决定输出文件名）
            use_browser: 强制启用浏览器模式（AntiBot hook 注入时为 True）
            max_pages: 深爬页数上限（>1 时启用 deep_crawl 引擎；0 表示按指令意图判断）
            max_depth: 深爬最大深度（层）

        Returns:
            Dict 包含各阶段结果和最终状态
        """
        from loguru import logger

        result: Dict[str, Any] = {
            "url": url,
            "instruction": instruction,
            "stages": {},
        }

        # 智能意图识别：用户指令中明确提到"图/图片/壁纸/截图/海报/封面/背景图/image/wallpaper"时
        # 默认启用浏览器模式 + 专用图片提取方法（避免懒加载图拿不到）
        _instr_lower = (instruction or "").lower()
        IMAGE_KEYWORDS = (
            "图片", "图像", "壁纸", "图 ", "截图", "海报", "封面", "背景图", "插图", "相片", "照片",
            "image", "images", "img", "picture", "pic", "photo", "wallpaper", "thumbnail",
        )
        is_image_intent = any(k in _instr_lower for k in IMAGE_KEYWORDS)
        preferred_extract_method: Optional[str] = None
        if is_image_intent:
            use_browser = True
            preferred_extract_method = "images"
            logger.info("[Supervisor] 意图: 图片抓取 → 强制 use_browser=True, method=images")

        # ========== 0. 深爬意图识别（deep_crawl 引擎，P0-2） ==========
        DEEP_CRAWL_KEYWORDS = (
            "深度爬取", "深爬", "整站", "全站", "全站抓取", "批量抓取",
            "所有页面", "全部页面", "deep_crawl", "deep crawl", "多页",
        )
        want_deep = max_pages > 1
        # 指令中显式提到"前 N 页"
        import re as _re
        _m = _re.search(r"前\s*(\d+)\s*(个)?页", instruction or "")
        if _m:
            max_pages = int(_m.group(1))
            want_deep = True
        if not want_deep:
            want_deep = any(k in _instr_lower for k in DEEP_CRAWL_KEYWORDS)
        if want_deep:
            logger.info(f"[Supervisor] 深爬意图: url={url}, max_pages={max_pages or 20}, depth={max_depth}")
            deep_args: Dict[str, Any] = {
                "url": url,
                "max_pages": max_pages or 20,
                "max_depth": max_depth or 3,
                "instruction": instruction,
                "output_dir": self.output_dir,
            }
            deep_result = await deep_crawl_executor(deep_args)
            result["stages"]["deep_crawl"] = _summarize(
                deep_result, ["stats", "saved_count", "output_dir"]
            )
            if deep_result.get("status_code") == STATUS_OK:
                result["status_code"] = STATUS_OK
                result["content"] = deep_result.get("content", "")
                result["items"] = deep_result.get("pages", [])
                result["deep_crawl"] = deep_result
                return result
            # 深爬失败 → 降级走单页流程
            logger.warning(f"[Supervisor] 深爬失败，降级单页流程: {deep_result.get('content')}")

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

        # ========== 4. extract（带 NEED_REANALYSIS 重试 + 浏览器兜底） ==========
        retry_count = 0
        extract_result: Dict[str, Any] = {}
        browser_upgraded = False  # 是否已完成 HTTP→浏览器 升级兜底
        while retry_count <= self.MAX_REANALYSIS_RETRIES:
            logger.info(f"[Supervisor] 4/5 extract (attempt {retry_count + 1})")
            extract_args = {
                "html": clean_html,
                "url": url,
                "blueprint": blueprint.to_dict() if blueprint else None,
            }
            # 注入用户意图驱动的提取方法（例如图片站强制 images）
            if preferred_extract_method:
                extract_args["method"] = preferred_extract_method
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
            # —— 新增兜底：当前是 HTTP 抓取且疑似需要 JS 渲染/懒加载 → 自动 use_browser=True 重跑
            if not browser_upgraded and not use_browser:
                _hints = [str(extract_result.get("hints") or "")]
                _html_len = len(html or "")
                # 用 clean 后 markdown 长度判断正文稀疏度：原始 HTML 的 text_content()
                # 会混入导航/页脚/内嵌 JSON 等噪声（SPA 壳页剔除 script 后仍可能上千字符），
                # 导致 looks_spa 误判为 False，浏览器自动升级兜底失效
                _body_len = len(markdown or "")
                _looks_spa = (
                    _html_len > 0 and _body_len < 800
                ) or any("browser" in h.lower() or "懒加载" in h for h in _hints)
                _looks_images_empty = preferred_extract_method == "images" and extract_result.get(
                    "count", 0) == 0
                # 提取条数过少（<5）+ 页面疑似列表/表格页（含 <table> 或链接密集）
                # → 判定提取不完整，自动升级浏览器重抓（LLM 兜底常从壳页提取 1 条垃圾数据）
                _count = extract_result.get("count", 0) or 0
                _looks_list_page = (
                    ("<table" in (html or "").lower())
                    or (crawl_result.get("links_count", 0) or 0) >= 20
                )
                _count_too_low = _looks_list_page and 0 < _count < 5
                if _looks_spa or _looks_images_empty or is_image_intent or _count_too_low:
                    logger.warning(
                        f"[Supervisor] HTTP 提取失败（looks_spa={_looks_spa}, "
                        f"images_empty={_looks_images_empty}, count_too_low={_count_too_low}）"
                        f"→ 自动升级 use_browser=True 重抓"
                    )
                    browser_upgraded = True
                    result["stages"].setdefault("upgrade", {})["reason"] = (
                        "lazy-load/spa detected, upgrading to Playwright browser"
                    )
                    try:
                        crawl2 = await crawl_executor({"url": url, "use_browser": True})
                    except Exception as e:
                        logger.warning(f"[Supervisor] 升级浏览器抓取异常: {e}")
                        crawl2 = {}
                    if crawl2.get("status_code") == STATUS_OK:
                        html2 = crawl2.get("html", crawl2.get("content", ""))
                        if html2 and len(html2) > _html_len:
                            html = html2
                            page_signature2 = crawl2.get("page_signature", "")
                            if page_signature2:
                                page_signature = page_signature2
                            # 重新 clean
                            clean_html = PruningContentFilter().filter_content(html)
                            markdown = MarkdownGenerator(max_length=200_000).generate(
                                clean_html, url=url, title=crawl2.get("title", ""), clean=False
                            )
                            result["clean_html"] = clean_html
                            result["markdown"] = markdown
                            result["stages"]["crawl2"] = _summarize(
                                crawl2, ["status", "strategy", "page_signature", "title"]
                            )
                            # 图片意图 → 图片专用方法；否则也强制 images 若 images 字段失败
                            if not preferred_extract_method and "images" in (
                                extract_result.get("failed_fields") or []
                            ):
                                preferred_extract_method = "images"
                            # 升级后重置 retry 从 0 开始走一次新的 analyze + extract
                            retry_count = 0
                            blueprint = None  # 新 HTML 结构可能不同，让 analyze 重新生成蓝图
                            continue

            result["stages"]["extract"] = _summarize(
                extract_result, ["confidence", "failed_fields"]
            )
            result["status_code"] = status or STATUS_FAILED
            result["content"] = extract_result.get("content", "提取失败")
            result["items"] = extract_result.get("items", []) if isinstance(extract_result.get("items"), list) else []
            result["count"] = len(result["items"])
            return result

        # ========== 5. save ==========
        logger.info(f"[Supervisor] 5/5 save")
        items = extract_result.get("items", [])
        # 保存 Markdown 内容（若有）
        markdown_content = result.get("markdown", "")
        title = crawl_result.get("title", "") or ""

        # 判断是否为图片提取结果（extract_images/method=images 产物），用 JSON 保存便于后续解析
        _extract_method = str(extract_result.get("method") or "")
        _first = items[0] if items and isinstance(items, list) else None
        _is_images = (
            _extract_method in ("images", "image", "imgs", "img")
            or (isinstance(_first, dict) and "src" in _first and "kind" in _first)
        )
        if _is_images:
            # 图片条目：用 JSON 保存到 images/{domain}/{date}/{title}.json
            # （同级 images/ 子目录就是实际下载图片的位置，路径组织清晰）
            try:
                from crawagent.output.organizer import FileOrganizer
                org = FileOrganizer(base_dir=self.output_dir)
                safe_title = title or "images"
                output_path = org.render(
                    "images/{domain}/{date}/{title}.json",
                    url=url,
                    title=safe_title,
                    ext="json",
                )
            except Exception as e:
                logger.debug(f"FileOrganizer 渲染失败，回退原逻辑: {e}")
                output_path = self._decide_output_path(url, title=title or "images", ext="json")
            save_fmt = "json"
        else:
            output_path = self._decide_output_path(url, title=title, ext="md")
            save_fmt = "markdown"

        save_args: Dict[str, Any] = {
            "data": items,
            "path": output_path,
            "url": url,
            "title": title,
            "format": save_fmt,
        }
        # 若有 Markdown 内容且 items 为空，用 content 模式保存（非图片模式）
        if markdown_content and not items and not _is_images:
            save_args = {
                "content": markdown_content,
                "path": output_path,
                "url": url,
                "title": title,
                "format": "markdown",
            }
        save_result = await save_executor(save_args)
        result["stages"]["save"] = _summarize(
            save_result,
            ["path", "format", "written", "skipped",
             "images_downloaded", "images_failed", "images_dir"],
        )
        # 回填结构化数据：save 成功后 items 供 API/前端直接消费
        result["items"] = items if isinstance(items, list) else []
        result["count"] = len(items) if isinstance(items, list) else 0
        result["status_code"] = save_result.get("status_code", STATUS_OK)
        result["content"] = save_result.get("content", "")
        result["output_path"] = save_result.get("path", "")
        if save_result.get("images_downloaded") or save_result.get("images_dir"):
            result["images_dir"] = save_result.get("images_dir", "")
            result["images_downloaded"] = save_result.get("images_downloaded", 0)
            result["images_failed"] = save_result.get("images_failed", 0)

        # 图片抓取意图完成后，给 LLM 一个明确的中文总结，避免反复 search/supervisor 死循环
        if result["status_code"] == STATUS_OK and (is_image_intent or _is_images):
            cnt = len(items) if isinstance(items, list) else 0
            dl = result.get("images_downloaded", 0) or 0
            dl_dir = result.get("images_dir", "") or save_result.get("images_dir", "") or ""
            out_path = result.get("output_path", "")
            previews = []
            for it in (items if isinstance(items, list) else [])[:3]:
                if isinstance(it, dict) and it.get("src"):
                    previews.append(f"- {it.get('alt') or it.get('title') or '（无标题）'}: {str(it.get('src'))[:80]}")
            preview_block = "\n".join(previews) if previews else "(无预览)"
            summary_lines = [
                f"✅ 图片抓取完成（{cnt} 条记录，已下载 {dl} 张到本地）。",
                f"数据 JSON：{out_path}",
            ]
            if dl_dir:
                summary_lines.append(f"图片目录：{dl_dir}")
            summary_lines.append("预览样例：")
            summary_lines.append(preview_block)
            summary_lines.append("\n（如需爬取更多分页，可指定具体分类/列表页 URL。当前页面已完成抓取）")
            result["content"] = "\n".join(summary_lines)
            result.setdefault("data", {})
            if isinstance(result["data"], dict):
                result["data"]["summary"] = result["content"]
                result["data"]["items_count"] = cnt
                result["data"]["images_downloaded"] = dl
                result["data"]["output_path"] = out_path
                result["data"]["images_dir"] = dl_dir

        # ========== 6. 网站画像自动发现（静默进行，不阻塞主流程） ==========
        try:
            from crawagent.core.site_profile import SiteProfile, get_profile_store
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower().replace("www.", "")
            store = self._profile_store or get_profile_store()
            profile = await store.async_get_or_discover(domain, url=url, html=html)
            # 更新已知路由
            if crawl_result.get("page_signature"):
                path = urlparse(url).path or "/"
                store.add_route(domain, path)
            result["site_profile"] = {
                "domain": profile.domain,
                "spa_type": profile.spa_type,
                "image_loading": profile.image_loading,
                "anti_bot_level": profile.anti_bot_level,
                "nav_links_count": len(profile.nav_links),
                "known_routes": profile.known_routes,
            }
        except Exception:
            pass

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
# supervisor/search 为基础；monitor/check_change/scan_vuln/fix_issue 为 P4/P5 自主能力
# （危险调用由 SecurityHook 拦截/确认）
LLM_CALLABLE_TOOLS = {
    "supervisor",
    "deep_crawl",
    "search",
    "monitor",
    "check_change",
    "scan_vuln",
    "fix_issue",
}


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

    # 工具 schema 顺序规范化（对齐 Reasonix normalizeToolSchemas）：
    # provider 可见的工具顺序影响 prompt-cache shape，按 name 排序保证稳定。
    sorted_defs = sorted(tool_defs, key=lambda td: td.name)

    tools: List[Any] = []
    for td in sorted_defs:
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


# ==================== 互联网搜索 ====================

_WEB_SEARCH_ENDPOINTS = {
    "duckduckgo": "https://html.duckduckgo.com/html/",
    "bing": "https://www.bing.com/search",
}

_WEB_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _parse_search_results(html: str, engine: str, limit: int) -> List[Dict[str, Any]]:
    """解析搜索引擎 HTML 结果页 → 结构化结果列表（纯函数，便于单测）。

    - duckduckgo: `a.result__a` 标题链接 + `a.result__snippet` 摘要；
      结果链接为 uddg 跳转包装，自动还原真实 URL。
    - bing: `li.b_algo h2 a` 标题链接 + `.b_caption p` 摘要。
    """
    from bs4 import BeautifulSoup
    from urllib.parse import parse_qs, urlparse

    soup = BeautifulSoup(html, "lxml")
    results: List[Dict[str, Any]] = []

    if engine == "duckduckgo":
        for a in soup.select("a.result__a"):
            if len(results) >= limit:
                break
            url = a.get("href", "")
            if "uddg=" in url:  # DDG 跳转包装，取真实目标 URL
                qs = parse_qs(urlparse(url).query)
                real = qs.get("uddg", [""])[0]
                if real:
                    url = real
            snippet = ""
            parent = a.find_parent("div", class_="result")
            if parent:
                sn = parent.select_one("a.result__snippet")
                snippet = sn.get_text(" ", strip=True) if sn else ""
            results.append({
                "title": a.get_text(" ", strip=True),
                "url": url,
                "snippet": snippet,
            })
    elif engine == "bing":
        for li in soup.select("li.b_algo"):
            if len(results) >= limit:
                break
            h2 = li.select_one("h2 a")
            if not h2:
                continue
            cap = li.select_one(".b_caption p")
            snippet = cap.get_text(" ", strip=True) if cap else ""
            results.append({
                "title": h2.get_text(" ", strip=True),
                "url": h2.get("href", ""),
                "snippet": snippet,
            })
    return results


async def _web_search(
    query: str,
    engine: str = "duckduckgo",
    limit: int = 10,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """执行单个搜索引擎查询（禁用系统代理，按项目约定 trust_env=False）。"""
    import httpx

    if engine not in _WEB_SEARCH_ENDPOINTS:
        return {"ok": False, "error": f"未知搜索引擎: {engine}", "results": []}

    params: Dict[str, str] = {"q": query}
    if engine == "duckduckgo":
        params["kl"] = "cn-zh"

    try:
        async with httpx.AsyncClient(
            trust_env=False,
            timeout=timeout,
            follow_redirects=True,
            headers=_WEB_SEARCH_HEADERS,
        ) as client:
            resp = await client.get(_WEB_SEARCH_ENDPOINTS[engine], params=params)
            resp.raise_for_status()
        results = _parse_search_results(resp.text, engine, limit)
        return {"ok": True, "engine": engine, "query": query, "results": results}
    except Exception as e:
        logger.warning(f"互联网搜索失败 ({engine}, query={query[:50]}): {e}")
        return {"ok": False, "engine": engine, "query": query, "error": str(e), "results": []}


def _normalize_url_for_dedup(url: str) -> str:
    """URL 归一化用于跨引擎去重：小写主机 + 去 www 前缀 + 去尾部斜杠。"""
    from urllib.parse import urlparse

    try:
        p = urlparse(url)
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = p.path.rstrip("/")
        return f"{p.scheme}://{host}{path}"
    except Exception:
        return url.strip().lower().rstrip("/")


async def _web_search_multi(
    query: str,
    engines: List[str],
    limit: int = 10,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """多个搜索引擎并存：并行查询 → 按引擎顺序合并 → URL 去重 → 截断到 limit。

    任一引擎成功即视为整体成功；失败引擎降级忽略（结果带 engine 标记便于溯源）。
    """
    engines = [e for e in engines if e in _WEB_SEARCH_ENDPOINTS]
    if not engines:
        return {"ok": False, "query": query, "engines": [], "error": "未指定有效的搜索引擎", "results": []}

    raw_results = await asyncio.gather(
        *[_web_search(query, e, limit=limit, timeout=timeout) for e in engines]
    )

    merged: List[Dict[str, Any]] = []
    seen: set = set()
    failed: List[str] = []
    ok_any = False

    for engine, res in zip(engines, raw_results):
        if not res["ok"]:
            failed.append(engine)
            continue
        ok_any = True
        for r in res["results"]:
            key = _normalize_url_for_dedup(r.get("url", ""))
            if key and key in seen:
                continue
            seen.add(key)
            merged.append({**r, "engine": engine})
            if len(merged) >= limit:
                break
        if len(merged) >= limit:
            break

    if not ok_any:
        detail = "; ".join(failed) or "全部搜索引擎失败"
        return {
            "ok": False, "query": query, "engines": engines,
            "error": detail, "results": [],
        }

    return {
        "ok": True,
        "query": query,
        "engines": engines,
        "failed": failed,
        "results": merged,
    }


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
    # 深爬引擎工具（P0-2：暴露给 LLM，supervisor 内部也会调用）
    registry.register(DEEP_CRAWL_TOOL, executor=deep_crawl_executor)
    # 浏览器 API 捕获工具（P9：强风控 / JS 签名站点，签名由浏览器自动计算）
    registry.register(HARVEST_API_TOOL, executor=harvest_api_executor)

    # 注册 Supervisor（外部唯一入口，暴露给 LLM）
    from crawagent.core.site_profile import get_profile_store
    _profile_store = get_profile_store()
    supervisor = CrawlSupervisor(output_dir=output_dir, profile_store=_profile_store)

    async def supervisor_executor(args: Dict[str, Any]) -> Dict[str, Any]:
        """Supervisor 执行器：调用 CrawlSupervisor.run"""
        url = args.get("url", "")
        instruction = args.get("instruction", "")
        use_browser = bool(args.get("use_browser", False))
        max_pages = int(args.get("max_pages", 0) or 0)
        max_depth = int(args.get("max_depth", 3) or 3)
        if not url:
            return {"content": "Error: missing 'url' argument", "error": True, "status_code": STATUS_FAILED}
        return await supervisor.run(
            url=url, instruction=instruction, use_browser=use_browser,
            max_pages=max_pages, max_depth=max_depth,
        )

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
            # 多引擎并存：优先 engines 数组；兼容单值 engine 与逗号分隔字符串
            engines = args.get("engines") or [args.get("engine", "duckduckgo")]
            if isinstance(engines, str):
                engines = [e.strip() for e in engines.split(",") if e.strip()]
            elif not isinstance(engines, list):
                engines = [engines]
            search_res = await _web_search_multi(query, engines, limit=limit)
            if not search_res["ok"]:
                return {
                    "content": f"互联网搜索失败: {search_res.get('error', '未知错误')}",
                    "status_code": STATUS_FAILED,
                    "query": query,
                    "scope": "web",
                    "engines": search_res["engines"],
                    "results": [],
                }
            results = search_res["results"]
            engine_label = "+".join(search_res["engines"])
            lines = [f"互联网搜索 '{query}'（{engine_label}）：找到 {len(results)} 条结果", ""]
            multi = len(search_res["engines"]) > 1
            for i, r in enumerate(results, 1):
                tag = f"[{r.get('engine', '')}] " if multi else ""
                lines.append(f"{i}. {tag}{r['title']}")
                lines.append(f"   {r['url']}")
                if r.get("snippet"):
                    lines.append(f"   {r['snippet']}")
            return {
                "content": "\n".join(lines),
                "status_code": STATUS_OK,
                "query": query,
                "scope": "web",
                "engines": search_res["engines"],
                "failed": search_res.get("failed", []),
                "results": results,
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

    # monitor 执行器：创建监控任务（P4-6）
    async def monitor_executor(args: Dict[str, Any]) -> Dict[str, Any]:
        """monitor 工具执行器：创建监控任务并加入调度

        Agent 可通过此工具自主创建监控任务：
        - 设置 URL + 监控频率 + 字段
        - 配置 Webhook 通知
        - 立即触发首次检查
        """
        from crawagent.monitor import MonitorTask, ScheduleType, get_monitor_store

        url = args.get("url", "").strip()
        name = args.get("name", "") or f"监控-{url[:30]}"
        interval_minutes = int(args.get("interval_minutes", 360))
        watch_fields = args.get("watch_fields", [])
        css_selector = args.get("css_selector", "")
        webhook = args.get("webhook", "")
        cooldown_minutes = int(args.get("alert_cooldown_minutes", 60))

        if not url:
            return {"content": "Error: missing 'url' argument", "error": True, "status_code": STATUS_FAILED}

        task = MonitorTask(
            name=name,
            url=url,
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=interval_minutes * 60,
            watch_fields=watch_fields,
            css_selector=css_selector,
            alert_webhook=webhook,
            alert_cooldown=cooldown_minutes * 60,
        )
        store = get_monitor_store()
        store.create_task(task)

        # 立即触发首次检查（建立基线）
        result = None
        try:
            from crawagent.monitor import MonitorScheduler
            scheduler = MonitorScheduler(store=store)
            result = await scheduler.run_task_once(task.id)
            summary = result.get("summary", "首次检查完成")
        except Exception as e:
            summary = f"首次检查失败: {e}"

        return {
            "content": f"监控任务已创建：{name}\nURL: {url}\n间隔: {interval_minutes} 分钟\n"
                       f"监控字段: {watch_fields or '整体内容'}\n首次检查: {summary}",
            "status_code": STATUS_OK,
            "task_id": task.id,
            "name": name,
            "url": url,
            "interval_minutes": interval_minutes,
            "first_check": result,
        }

    # check_change 执行器：立即检查变化（P4-6）
    async def check_change_executor(args: Dict[str, Any]) -> Dict[str, Any]:
        """check_change 工具执行器：立即检查监控目标是否有变化

        支持两种模式：
        1. 传入 task_id → 检查已有任务
        2. 传入 url + watch_fields → 临时检查（自动创建一次性任务）
        """
        from crawagent.monitor import MonitorTask, ScheduleType, get_monitor_store, MonitorScheduler

        task_id = args.get("task_id", "")
        url = args.get("url", "")

        if not task_id and not url:
            return {"content": "Error: 需提供 task_id 或 url", "error": True, "status_code": STATUS_FAILED}

        store = get_monitor_store()
        scheduler = MonitorScheduler(store=store)

        if task_id:
            # 模式 1：检查已有任务
            result = await scheduler.run_task_once(task_id)
        else:
            # 模式 2：临时检查（创建一次性任务）
            task = MonitorTask(
                name="临时检查",
                url=url,
                schedule_type=ScheduleType.ONCE,
                interval_seconds=0,
                watch_fields=args.get("watch_fields", []),
                css_selector=args.get("css_selector", ""),
                alert_webhook="",
            )
            store.create_task(task)
            result = await scheduler.run_task_once(task.id)

        return {
            "content": result.get("summary", "检查完成"),
            "status_code": STATUS_OK if result.get("success") else STATUS_FAILED,
            "changed": result.get("changed", False),
            "summary": result.get("summary", ""),
            "fields": result.get("fields", {}),
            "status_code_http": result.get("status_code"),
        }

    # ==================== P5 安全扫描工具执行器 ====================

    async def scan_vuln_executor(args: Dict[str, Any]) -> Dict[str, Any]:
        """scan_vuln 工具执行器：运行 OWASP Top 10 漏洞扫描

        流程：创建扫描任务 → 运行 VulnScanner → 持久化漏洞 → 返回结构化报告
        """
        from crawagent.security import (
            SecurityLane, VulnCategory, get_security_store,
        )

        url = args.get("url", "")
        if not url:
            return {"content": "Error: missing 'url' argument", "error": True}

        # 类别映射（字符串 → VulnCategory 枚举）
        category_names = args.get("categories", [])
        categories: List[VulnCategory] = []
        for name in category_names:
            if isinstance(name, str):
                # 尝试匹配枚举值
                for cat in VulnCategory:
                    if cat.value.lower() == name.lower() or cat.name.lower() == name.lower():
                        categories.append(cat)
                        break

        depth = int(args.get("depth", 1))

        try:
            lane = SecurityLane(store=get_security_store())
            result = await lane.run_scan(
                url=url,
                categories=categories or None,
                depth=depth,
            )
            scan_result = result["scan_result"]
            task = result["task"]
            patches = result.get("patches", [])

            # 生成摘要给 LLM
            vulns = scan_result.vulnerabilities
            severity_counts = scan_result.severity_counts
            summary_lines = [
                f"安全扫描完成：发现 {len(vulns)} 个漏洞",
                f"严重性分布：{severity_counts or '无'}",
                f"扫描页面 {scan_result.pages_scanned} 个，发送请求 {scan_result.requests_sent} 次",
                f"耗时 {scan_result.duration_seconds:.1f}s",
                "",
                "漏洞详情：",
            ]
            for v in vulns[:20]:  # 最多列 20 个
                summary_lines.append(f"  - {v.to_summary()}")
            if len(vulns) > 20:
                summary_lines.append(f"  ... 还有 {len(vulns) - 20} 个漏洞")

            if patches:
                summary_lines.append("")
                summary_lines.append(f"已生成 {len(patches)} 个修复补丁（可用 fix_issue 工具保存）")

            return {
                "content": "\n".join(summary_lines),
                "status_code": STATUS_OK,
                "scan_task_id": task.id,
                "vuln_count": len(vulns),
                "severity_counts": severity_counts,
                "vulnerabilities": [v.model_dump() for v in vulns],
                "patch_count": len(patches),
                "pages_scanned": scan_result.pages_scanned,
                "requests_sent": scan_result.requests_sent,
                "duration_seconds": scan_result.duration_seconds,
            }
        except Exception as e:
            logger.exception(f"scan_vuln 执行失败: {e}")
            return {
                "content": f"安全扫描失败: {e}",
                "error": True,
                "status_code": STATUS_FAILED,
            }

    async def fix_issue_executor(args: Dict[str, Any]) -> Dict[str, Any]:
        """fix_issue 工具执行器：根据漏洞生成修复补丁文件

        支持两种输入：
        - scan_task_id：生成该任务所有漏洞的补丁
        - vulnerability_ids：仅生成指定漏洞的补丁
        """
        from crawagent.security import (
            get_security_store, AutoFixer,
        )

        scan_task_id = args.get("scan_task_id", "")
        vuln_ids = args.get("vulnerability_ids", [])
        output_dir = args.get("output_dir", "./output/security_patches")

        store = get_security_store()

        # 收集漏洞
        if vuln_ids:
            # 按指定 ID 取
            all_vulns = store.list_vulnerabilities(task_id=scan_task_id, limit=1000)
            vulns = [v for v in all_vulns if v.id in vuln_ids]
        else:
            vulns = store.list_vulnerabilities(task_id=scan_task_id, limit=1000)

        if not vulns:
            return {
                "content": "未找到匹配的漏洞记录，无法生成补丁",
                "status_code": STATUS_OK,
                "patches_generated": 0,
            }

        # 生成补丁（按等级白名单：高危/严重仅报告，默认只自动处理低/中危）
        fixer = AutoFixer()
        patches, reported_only = await fixer.generate_patches(vulns, scan_task_id=scan_task_id)

        # 保存到文件
        patch_files = await fixer.save_patches(patches, output_dir=output_dir)

        # 生成摘要
        summary_lines = [
            f"已生成 {len(patches)} 个修复补丁",
            f"覆盖 {len(vulns) - len(reported_only)} 个白名单内漏洞（自动处理）",
            f"保存目录：{output_dir}",
            "",
            "补丁列表：",
        ]
        for p in patches:
            summary_lines.append(f"  - {p.to_summary()}")
        if reported_only:
            summary_lines.append("")
            summary_lines.append(
                f"⚠️ {len(reported_only)} 个高危/严重漏洞仅报告（需人工审阅，未自动 patch）："
            )
            for v in reported_only[:10]:
                summary_lines.append(f"  - {v.to_summary()}")
        summary_lines.append("")
        summary_lines.append("应用补丁：git apply <patch_file>")

        return {
            "content": "\n".join(summary_lines),
            "status_code": STATUS_OK,
            "patches_generated": len(patches),
            "patch_files": patch_files,
            "reported_only_count": len(reported_only),
            "reported_only": [v.model_dump() for v in reported_only],
            "patches": [
                {
                    "id": p.id,
                    "title": p.title,
                    "target_file": p.target_file,
                    "severity": p.severity.value,
                    "vulnerability_ids": p.vulnerability_ids,
                    "diff": p.diff,
                    "description": p.description,
                }
                for p in patches
            ],
        }

    registry.register(SEARCH_TOOL, executor=search_executor)
    registry.register(MONITOR_TOOL, executor=monitor_executor)
    registry.register(CHECK_CHANGE_TOOL, executor=check_change_executor)
    registry.register(SCAN_VULN_TOOL, executor=scan_vuln_executor)
    registry.register(FIX_ISSUE_TOOL, executor=fix_issue_executor)

    return registry
