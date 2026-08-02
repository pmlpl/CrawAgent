from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type, Union, Callable
from urllib.parse import urljoin

from lxml import html as lxml_html
from lxml import etree
from pydantic import BaseModel, ConfigDict, create_model

from crawagent.core.models import CrawlResult, ExtractedItem, Selectors, SiteAnalysis
from crawagent.llm.factory import get_llm
from loguru import logger


@dataclass
class ExtractionContext:
    """提取上下文"""
    url: str
    html: str
    selectors: Selectors
    target_schema: Type[BaseModel]
    base_url: str = ""


class BaseExtractor(ABC):
    """提取器基类"""

    @abstractmethod
    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        pass

    def _normalize_text(self, text: str) -> str:
        """规范化文本"""
        if not text:
            return ""
        # 压缩空白字符
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _cssselect(node, selector: str) -> list:
        """防御性 cssselect：空/非法选择器返回空列表，避免提取链路崩溃。

        LLM 生成的蓝图选择器可能为空串或非法语法（如尾随逗号），
        lxml cssselect 会抛 CSSSyntaxError（Expected selector...），
        这里统一吞掉，让调用方走正常空结果分支。
        """
        if not selector:
            return []
        try:
            return node.cssselect(selector)
        except Exception:
            return []

    # selectolax 支持的标准伪类（LLM 生成选择器时可能混入这些，需保留）
    _KEEP_PSEUDO = {
        "first-child", "last-child", "nth-child", "nth-of-type", "nth-last-child",
        "nth-last-of-type", "not", "first-of-type", "last-of-type", "only-child",
        "only-of-type", "empty", "root", "hover", "focus", "active", "visited",
        "checked", "disabled", "enabled", "required", "optional", "selected",
    }

    def _convert_selector(self, selector: str) -> str:
        """
        转换 SiteAnalyzer 的 @attr 语法为标准 CSS 属性选择器
        例: video@title -> video[title]
            a@href -> a[href]
            img@src -> img[src]
        """
        if not selector or '@' not in selector:
            return selector
        # 处理 tag@attr 格式
        parts = selector.split('@')
        if len(parts) == 2:
            tag, attr = parts
            return f"{tag}[{attr}]"
        return selector

    @classmethod
    def sanitize_selector(cls, selector: str) -> str:
        """
        清洗 LLM 生成的 CSS 选择器，剥离 Tailwind 变体类 token（如 .sm:grid-cols-2 / .lg:grid-cols-3）。

        原因：Tailwind 响应式前缀（sm:/md:/lg:）不是合法 CSS 伪类，
        selectolax 会抛 Bad CSS Selectors，甚至对特定 HTML 触发原生崩溃（0xC0000005）。
        清洗规则：移除「.类名:非伪类名」形式的 token，保留标准伪类（:first-child 等）。
        """
        if not selector or ":" not in selector:
            return selector

        def _repl(m):
            pseudo = (m.group(2) or "").lower()
            if pseudo in cls._KEEP_PSEUDO:
                return m.group(0)
            return ""  # Tailwind 变体类 → 剥离

        cleaned = re.sub(r"\.([\w-]+):([\w-]+)(\([^)]*\))?", _repl, selector)
        # 清理残留的重复点号与首尾空白
        cleaned = re.sub(r"\.{2,}", ".", cleaned)
        return cleaned.strip(" .")


class SelectolaxExtractor(BaseExtractor):
    """Selectolax CSS 选择器提取器（快）"""

    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        if not ctx.html or not ctx.selectors.item:
            return []

        # 委托 lxml 实现（selectolax 在 Windows/Python 3.13 上偶发原生崩溃 0xC0000005，
        # 进程级 try/except 无法捕获；lxml 的 cssselect 同样支持 CSS 选择器，稳定无此问题）
        try:
            doc = lxml_html.fromstring(ctx.html)
        except Exception as e:
            logger.warning(f"lxml 解析 HTML 失败: {e}")
            return []

        container = None
        if ctx.selectors.list_container:
            try:
                containers = doc.cssselect(self.sanitize_selector(ctx.selectors.list_container))
            except Exception as e:
                logger.warning(f"lxml list_container 选择器无效: {e}")
                containers = []
            if not containers:
                return []
            container = containers[0]
        else:
            container = doc

        try:
            items = container.cssselect(self.sanitize_selector(ctx.selectors.item))
        except Exception as e:
            logger.warning(f"lxml item 选择器无效: {e}")
            return []
        results = []

        for idx, item in enumerate(items):
            data = self._extract_item(item, ctx, idx)
            if data:
                try:
                    model = ctx.target_schema(**data)
                    results.append(model)
                except Exception as e:
                    logger.debug(f"Schema validation failed for item {idx}: {e}")
                    continue

        return results

    def _extract_item(self, node, ctx: ExtractionContext, idx: int) -> Optional[Dict]:
        """从单个节点提取数据"""
        data = {}

        # 标题
        if ctx.selectors.title:
            sel = self.sanitize_selector(self._convert_selector(ctx.selectors.title))
            els = self._cssselect(node, sel)
            if els:
                data["title"] = self._normalize_text(els[0].text_content())

        # URL
        if ctx.selectors.url:
            sel = self.sanitize_selector(self._convert_selector(ctx.selectors.url))
            els = self._cssselect(node, sel)
            if els:
                href = els[0].get("href") or els[0].text_content()
                if href:
                    data["url"] = urljoin(ctx.base_url, href.strip())

        # 额外字段
        for field_name, selector in ctx.selectors.extra.items():
            sel = self.sanitize_selector(self._convert_selector(selector))
            els = self._cssselect(node, sel)
            if els:
                el = els[0]
                tag = el.tag.lower()
                if tag in ("a", "link"):
                    data[field_name] = urljoin(ctx.base_url, (el.get("href") or "").strip())
                elif tag in ("img", "image"):
                    data[field_name] = urljoin(ctx.base_url, (el.get("src") or "").strip())
                else:
                    data[field_name] = self._normalize_text(el.text_content())

        # 如果没有任何字段，返回 None
        if not data:
            return None

        # 兜底：无 url 字段时回填页面 URL（保证 ExtractedItem 必填字段通过校验）
        data.setdefault("url", ctx.url)

        data["_source_index"] = idx
        return data


class LxmlExtractor(BaseExtractor):
    """lxml XPath/CSS 提取器（强）"""

    def _convert_selector(self, selector: str) -> str:
        """转换 @attr 语法为标准 CSS"""
        return super()._convert_selector(selector)

    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        if not ctx.html or not ctx.selectors.item:
            return []

        try:
            doc = lxml_html.fromstring(ctx.html)
            doc.make_links_absolute(ctx.base_url)
        except Exception as e:
            logger.debug(f"lxml parse failed: {e}")
            return []

        # 容器
        if ctx.selectors.list_container:
            try:
                containers = doc.cssselect(self.sanitize_selector(ctx.selectors.list_container))
            except Exception as e:
                logger.debug(f"lxml list_container 选择器无效: {e}")
                containers = []
            if not containers:
                return []
            root = containers[0]
        else:
            root = doc

        try:
            items = root.cssselect(self.sanitize_selector(ctx.selectors.item))
        except Exception as e:
            logger.debug(f"lxml item 选择器无效: {e}")
            return []
        results = []

        for idx, item in enumerate(items):
            data = self._extract_item(item, ctx, idx)
            if data:
                try:
                    model = ctx.target_schema(**data)
                    results.append(model)
                except Exception as e:
                    logger.debug(f"Schema validation failed for item {idx}: {e}")

        return results

    def _extract_item(self, node, ctx: ExtractionContext, idx: int) -> Optional[Dict]:
        data = {}

        # 标题
        if ctx.selectors.title:
            sel = self.sanitize_selector(self._convert_selector(ctx.selectors.title))
            els = self._cssselect(node, sel)
            if els:
                data["title"] = self._normalize_text(els[0].text_content())

        # URL
        if ctx.selectors.url:
            sel = self.sanitize_selector(self._convert_selector(ctx.selectors.url))
            els = self._cssselect(node, sel)
            if els:
                href = els[0].get("href") or els[0].text_content()
                if href:
                    data["url"] = urljoin(ctx.base_url, href.strip())

        # 额外字段
        for field_name, selector in ctx.selectors.extra.items():
            sel = self.sanitize_selector(self._convert_selector(selector))
            els = self._cssselect(node, sel)
            if els:
                el = els[0]
                tag = el.tag.lower()
                if tag in ("a", "link"):
                    data[field_name] = urljoin(ctx.base_url, el.get("href", "").strip())
                elif tag in ("img", "image"):
                    data[field_name] = urljoin(ctx.base_url, el.get("src", "").strip())
                else:
                    data[field_name] = self._normalize_text(el.text_content())

        if not data:
            return None

        # 兜底：无 url 字段时回填页面 URL（保证 ExtractedItem 必填字段通过校验）
        data.setdefault("url", ctx.url)

        data["_source_index"] = idx
        return data


class LLMExtractor(BaseExtractor):
    """LLM 兜底提取器（当选择器失效时）"""

    def __init__(self):
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        if not ctx.html:
            return []

        # 截断 HTML（避免超长）
        truncated = ctx.html[:8000] if len(ctx.html) > 8000 else ctx.html

        # 构造 prompt
        schema_info = ctx.target_schema.model_json_schema()
        fields_desc = []
        for name, info in schema_info.get("properties", {}).items():
            desc = info.get("description", name)
            fields_desc.append(f"  - {name}: {desc}")

        prompt = f"""从以下 HTML 页面中提取结构化数据。

目标 URL: {ctx.url}
目标字段:
{chr(10).join(fields_desc)}

页面 HTML (截断):
{truncated}

请提取所有符合条目的数据，返回 JSON 数组。每个对象包含上述字段。
如果某字段在页面中找不到，设为 null。
只返回 JSON，不要任何解释。"""

        try:
            # 使用 function calling 强制结构化输出
            tool_schema = {
                "name": "extract_items",
                "description": "提取结构化数据",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": schema_info,
                        }
                    },
                    "required": ["items"],
                },
            }

            # 这里简化：直接调用 LLM，实际应用中应使用 function calling
            response = self.llm.invoke(prompt)
            # 防御：LLM 可能返回空 content（None），直接 re.search 会抛 TypeError
            content = response.content or ""

            # 尝试解析 JSON
            import json
            json_match = re.search(r"\[.*\]", content, re.DOTALL)
            if json_match:
                items = json.loads(json_match.group())
                results = []
                for item in items:
                    try:
                        model = ctx.target_schema(**item)
                        results.append(model)
                    except Exception:
                        continue
                return results

        except Exception as e:
            logger.warning(f"LLM extraction failed: {e}")

        return []

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        return text.strip()


class RegexExtractor(BaseExtractor):
    """正则提取器：按字段名 → 正则模式提取（P2-3）。"""

    def __init__(self, patterns: Optional[Dict[str, str]] = None, flags: int = re.I | re.M):
        self.patterns = patterns or {}
        self.flags = flags

    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        if not ctx.html:
            return []
        patterns = self.patterns
        if not patterns and isinstance(ctx.selectors.extra, dict):
            patterns = ctx.selectors.extra.get("patterns") or {}
        if not patterns:
            return []

        # 剥离标签后做纯文本正则
        text = re.sub(r"<[^>]+>", " ", ctx.html)
        text = re.sub(r"\s+", " ", text)

        item: Dict[str, Any] = {}
        for field, pattern in patterns.items():
            m = re.search(pattern, text, self.flags)
            if m:
                item[field] = m.group(1) if m.groups() else m.group(0)
            else:
                item[field] = ""

        try:
            return [ctx.target_schema(**item)]
        except Exception:
            return []


class JsonLdExtractor(BaseExtractor):
    """JSON-LD 提取器：解析 <script type="application/ld+json">（P2-3）。"""

    _FIELD_MAP = {
        "title": ("headline", "name", "title"),
        "url": ("url", "mainEntityOfPage", "sameAs"),
        "content": ("articleBody", "description", "abstract", "text"),
        "summary": ("description", "abstract"),
        "publish_time": ("datePublished", "dateCreated"),
        "author": ("author", "creator"),
        "image": ("image", "thumbnailUrl"),
    }

    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        if not ctx.html:
            return []
        from bs4 import BeautifulSoup
        import json

        soup = BeautifulSoup(ctx.html, "html.parser")
        objects: List[Dict] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            self._collect(data, objects)

        results: List[BaseModel] = []
        for obj in objects:
            item: Dict[str, Any] = {}
            for field, keys in self._FIELD_MAP.items():
                value = self._pick(obj, keys)
                if value is not None:
                    item[field] = value
            if ctx.url and not item.get("url"):
                item["url"] = ctx.url
            if not item:
                continue
            try:
                results.append(ctx.target_schema(**item))
            except Exception:
                continue
        return results

    def _collect(self, data: Any, out: List[Dict]) -> None:
        if isinstance(data, list):
            for d in data:
                self._collect(d, out)
        elif isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                for g in graph:
                    self._collect(g, out)
            elif graph is not None:
                self._collect(graph, out)
            main = data.get("mainEntity")
            if isinstance(main, (dict, list)):
                self._collect(main, out)
            if data.get("@type") or any(k in data for k in self._FIELD_MAP["title"]):
                out.append(data)

    def _pick(self, obj: Dict, keys) -> Any:
        for key in keys:
            value = obj.get(key)
            if value is None:
                continue
            if isinstance(value, dict):
                value = value.get("name") or value.get("@id") or value.get("url")
            elif isinstance(value, list):
                value = value[0] if value else None
                if isinstance(value, dict):
                    value = value.get("name") or value.get("url")
            if value and not isinstance(value, (dict, list)):
                return str(value)
        return None


class MetaExtractor(BaseExtractor):
    """Meta 提取器：og:title / og:url / og:image / description（P2-3）。"""

    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        if not ctx.html:
            return []
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(ctx.html, "html.parser")

        def meta_content(prop: str, attr: str = "property") -> str:
            el = soup.find("meta", attrs={attr: prop})
            return el.get("content", "").strip() if el else ""

        item: Dict[str, Any] = {}
        title = meta_content("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
        if title:
            item["title"] = title
        url = meta_content("og:url") or meta_content("canonical", "rel")
        if not url and ctx.url:
            url = ctx.url
        if url:
            item["url"] = url
        image = meta_content("og:image")
        if image:
            item["image"] = image
        desc = meta_content("og:description") or meta_content("description", "name")
        if desc:
            if "content" in ctx.target_schema.model_fields:
                item["content"] = desc
            else:
                item["summary"] = desc
        h1 = soup.find("h1")
        if not item.get("title") and h1:
            item["title"] = h1.get_text(strip=True)
        if not item:
            return []
        try:
            return [ctx.target_schema(**item)]
        except Exception:
            return []


class TableExtractor(BaseExtractor):
    """表格提取器：HTML <table> → 行字典列表（P2-3）。

    表头字段名与目标 schema 字段匹配时按名映射（原逻辑）；
    否则智能映射到通用字段（title/url/content），保证 ExtractedItem 也能用。
    url 缺省时回填页面 URL（与 _extract_item 兜底一致）。
    """

    # 通用字段映射关键词（表头匹配）
    _TITLE_KEYS = ("title", "名称", "标题", "产品", "商品", "name")
    _URL_KEYS = ("url", "链接", "link", "地址", "source")

    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        if not ctx.html:
            return []
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(ctx.html, "html.parser")
        results: List[BaseModel] = []
        for table in soup.find_all("table"):
            header: List[str] = []
            header_row = table.find("tr")
            if header_row:
                header = [th.get_text(strip=True) for th in header_row.find_all("th")]
            rows = table.find_all("tr")
            if not header and rows:
                # 首行当表头
                header = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
                header_row = rows[0]
                rows = rows[1:]
            elif header_row is not None:
                rows = [r for r in rows if r is not header_row]
            if not header:
                continue
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) < len(header):
                    cells += [""] * (len(header) - len(cells))
                item = self._build_item(header, cells, ctx)
                if not item:
                    continue
                try:
                    results.append(ctx.target_schema(**item))
                except Exception:
                    continue
        return results

    def _build_item(self, header: List[str], cells: List[str], ctx: ExtractionContext) -> Optional[Dict[str, str]]:
        """表头/单元格 → 目标 schema 字段字典。

        优先按表头字段名直配；否则通用映射到 title/url/content。
        """
        schema_fields = ctx.target_schema.model_fields.keys()
        direct = {header[i]: cells[i] for i in range(len(header))}

        if any(h in schema_fields for h in header):
            item = direct
        else:
            item = {}
            t_idx = self._match_idx(header, self._TITLE_KEYS)
            u_idx = self._match_idx(header, self._URL_KEYS)
            title = cells[t_idx] if t_idx is not None else (cells[0] if cells else "")
            if title:
                item["title"] = title
            if u_idx is not None and cells[u_idx]:
                item["url"] = cells[u_idx]
            # 其余列拼接进 content（保留列名）
            extra_parts = [
                f"{header[i]}: {cells[i]}"
                for i in range(len(header))
                if i not in (t_idx, u_idx) and cells[i] and header[i] != ""
            ]
            if extra_parts and "content" in schema_fields:
                item["content"] = "\n".join(extra_parts)

        # url 兜底：必填字段回填页面 URL
        if "url" in schema_fields and not item.get("url"):
            item["url"] = ctx.url
        return item or None

    @staticmethod
    def _match_idx(header: List[str], keys: tuple) -> Optional[int]:
        """返回表头中第一个包含任一关键词的列下标。"""
        for i, h in enumerate(header):
            hl = h.lower()
            if any(k in hl for k in keys):
                return i
        return None


class JsonCssExtractor(BaseExtractor):
    """嵌套 CSS 选择器提取（Crawl4AI JsonCss 风格，P2-3）。

    选择器树格式：
    {
      "items": ".item",
      "fields": {
        "title": "h3",
        "url": {"selector": "a", "attribute": "href"},
        "author": {"selector": ".author", "default": ""},
        "nested": {"fields": {...}, "items": "..."}
      }
    }
    """

    def __init__(self, selectors_tree: Optional[Dict[str, Any]] = None):
        self.selectors_tree = selectors_tree or {}

    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        if not ctx.html:
            return []
        tree = self.selectors_tree
        if not tree and isinstance(ctx.selectors.extra, dict):
            tree = ctx.selectors.extra.get("json_css") or {}
        if not tree:
            return []

        # 用 lxml 解析（selectolax 在 Windows/Python 3.13 上偶发原生崩溃 0xC0000005）
        try:
            doc = lxml_html.fromstring(ctx.html)
        except Exception as e:
            logger.warning(f"lxml 解析 HTML 失败: {e}")
            return []
        items_sel = tree.get("items")
        fields = tree.get("fields", {})
        if not items_sel:
            node = doc.body or doc
            item = self._extract_node(node, fields, ctx)
            try:
                return [ctx.target_schema(**item)] if item else []
            except Exception:
                return []

        try:
            nodes = doc.cssselect(self.sanitize_selector(items_sel))
        except Exception as e:
            logger.warning(f"lxml json_css items 选择器无效: {e}")
            return []
        results: List[BaseModel] = []
        for node in nodes:
            item = self._extract_node(node, fields, ctx)
            if item:
                try:
                    results.append(ctx.target_schema(**item))
                except Exception:
                    continue
        return results

    def _extract_node(self, node, fields: Dict, ctx: ExtractionContext) -> Dict[str, Any]:
        item: Dict[str, Any] = {}
        for name, conf in fields.items():
            if isinstance(conf, dict) and ("items" in conf or "fields" in conf):
                # 嵌套对象
                if "items" in conf:
                    try:
                        sub_nodes = node.cssselect(self.sanitize_selector(conf["items"]))
                    except Exception as e:
                        logger.debug(f"lxml 嵌套 items 选择器无效: {e}")
                        sub_nodes = []
                    item[name] = [self._extract_node(n, conf.get("fields", {}), ctx) for n in sub_nodes]
                else:
                    item[name] = self._extract_node(node, conf.get("fields", {}), ctx)
                continue

            selector = conf.get("selector") if isinstance(conf, dict) else conf
            attr = conf.get("attribute") if isinstance(conf, dict) else None
            default = conf.get("default") if isinstance(conf, dict) else None
            try:
                els = node.cssselect(self.sanitize_selector(selector)) if selector else []
            except Exception as e:
                logger.debug(f"lxml 字段选择器无效: {e}")
                els = []
            el = els[0] if els else None
            if el is None:
                if default is not None:
                    item[name] = default
                continue
            if attr:
                value = el.get(attr, "") or ""
                if attr == "href" and value and ctx.base_url:
                    from urllib.parse import urljoin
                    value = urljoin(ctx.base_url, value)
            else:
                value = (el.text_content() or "").strip()
            if value:
                item[name] = value
            elif default is not None:
                item[name] = default
        return item


class CompositeExtractor:
    """
    组合提取器：CSS -> XPath -> LLM 三级回退
    """

    def __init__(self):
        # CSS 策略改用 lxml 实现（cssselect 同样支持 CSS 选择器）：
        # selectolax 在 Windows/Python 3.13 上解析特定 HTML 时偶发原生崩溃
        # （0xC0000005，进程级，try/except 无法捕获），lxml 为纯 C 库稳定无此问题。
        self._css = LxmlExtractor()
        self._xpath = LxmlExtractor()
        self._llm = LLMExtractor()
        self._strategies = {
            "css": self._css,
            "selectolax": self._css,
            "xpath": self._xpath,
            "lxml": self._xpath,
            "llm": self._llm,
            "regex": RegexExtractor(),
            "jsonld": JsonLdExtractor(),
            "meta": MetaExtractor(),
            "table": TableExtractor(),
            "json_css": JsonCssExtractor(),
        }

    def get(self, strategy: str) -> Optional[BaseExtractor]:
        """按名称获取提取器。"""
        return self._strategies.get(strategy)

    def extract(
        self,
        url: str,
        html: str,
        selectors: Selectors,
        target_schema: Type[BaseModel],
        base_url: str = "",
        strategy: Optional[str] = None,
    ) -> List[BaseModel]:
        """提取；指定 strategy 时用单一策略，否则三级回退。"""
        # 调用方未提供 target_schema 时使用通用条目模型，
        # 避免各提取器执行 None(**data) 抛 TypeError 导致结果恒为空
        if target_schema is None:
            target_schema = ExtractedItem

        if not base_url:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

        ctx = ExtractionContext(
            url=url,
            html=html,
            selectors=selectors,
            target_schema=target_schema,
            base_url=base_url,
        )

        if strategy:
            extractor = self._strategies.get(strategy)
            if extractor is None:
                raise ValueError(f"未知提取策略: {strategy}，可选: {list(self._strategies)}")
            return extractor.extract(ctx)

        # 1. 尝试 CSS (lxml cssselect 实现，稳定无 selectolax 原生崩溃问题)
        if selectors.item:
            logger.debug("尝试 CSS 选择器提取")
            results = self._css.extract(ctx)
            if results:
                logger.debug(f"CSS 提取成功: {len(results)} 条")
                return results

        # 2. 尝试 XPath (lxml)
        logger.debug("CSS 失败，尝试 XPath 提取")
        results = self._xpath.extract(ctx)
        if results:
            logger.debug(f"XPath 提取成功: {len(results)} 条")
            return results

        # 2.5 表格回退：CSS/XPath 对 <table> 页面通常只取到碎片或为空，
        # 页面含表格结构时优先 table 策略（表头→title/url/content 通用映射）
        if self._has_table(ctx.html):
            logger.debug("CSS/XPath 未命中，尝试 Table 提取")
            results = self._strategies["table"].extract(ctx)
            if results:
                logger.debug(f"Table 提取成功: {len(results)} 条")
                return results

        # 3. LLM 兜底
        logger.debug("选择器均失效，启用 LLM 兜底提取")
        results = self._llm.extract(ctx)
        logger.debug(f"LLM 提取: {len(results)} 条")
        return results

    @staticmethod
    def _has_table(html: str) -> bool:
        """轻量表格结构检测：页面含 <table> 且至少 2 行非空数据。"""
        if not html or "<table" not in html.lower():
            return False
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for t in soup.find_all("table"):
                if len(t.find_all("tr")) >= 2 and t.get_text(strip=True):
                    return True
            return False
        except Exception:
            return False

    def extract_images(
        self,
        url: str,
        html: str,
        min_width: int = 50,
        min_height: int = 50,
        max_images: int = 500,
        base_url: str = "",
    ) -> List[Dict[str, Any]]:
        """图片专用批量提取：聚合 img/src/data-src/srcset/bg-image，去重并分类。

        相比 _fallback_generic 的增强点：
        - 覆盖懒加载属性：data-src / data-original / data-lazy / data-srcset
        - 解析 srcset 拿到真实高清 URL（选最大 w/h）
        - 抽取 style 中 background-image / inline 背景图
        - 收集 og:image / twitter:image / JSON-LD image
        - 对 a>img 组合补全 page_url（点击进入详情）
        - 去重：按 URL hash + 同一 alt 聚类最小图

        Args:
            url: 页面 URL
            html: 页面 HTML
            min_width/max_height: 过滤小图标阈值（像素，解析到 width/height 属性时生效）
            max_images: 最多返回条数（避免大图站炸内存）
            base_url: 可选，覆盖 url 推断的基准

        Returns:
            按「清晰度优先」排序的图片字典列表，字段：
            src / alt / title / width / height / kind(meta|og|inline|src|lazy|bg|link) / page_url
        """
        from bs4 import BeautifulSoup
        import hashlib as _hl

        if not base_url:
            from urllib.parse import urlparse
            _p = urlparse(url)
            base_url = f"{_p.scheme}://{_p.netloc}"

        soup = BeautifulSoup(html or "", "html.parser")
        out: List[Dict[str, Any]] = []
        seen: set = set()

        def _push(rec: Dict[str, Any]) -> None:
            src = rec.get("src", "")
            if not src or src.startswith(("data:", "#", "javascript:", "about:")):
                return
            abs_src = urljoin(base_url, src).strip()
            if not abs_src:
                return
            # UI/装饰噪声图（favicon/logo/图标/头像/默认背景）直接过滤
            if _is_ui_noise_image(abs_src, rec.get("alt", ""), rec.get("kind", "")):
                return
            # hash 去重（忽略 query 尾部签名差异）
            try:
                from urllib.parse import urlsplit, urlunsplit
                sp = urlsplit(abs_src)
                # scheme + netloc + path 做主键（query 里常见签名差异忽略）
                key_src = urlunsplit((sp.scheme, sp.netloc, sp.path, "", ""))
            except Exception:
                key_src = abs_src
            key = _hl.md5(key_src.encode("utf-8", errors="ignore")).hexdigest()
            if key in seen:
                return
            seen.add(key)
            rec["src"] = abs_src
            out.append(rec)
            if len(out) >= max_images:
                return

        # 1) Meta / OG / Twitter 图片（优先：一般是高质量缩略图）
        for meta in soup.find_all("meta"):
            prop = str(meta.get("property") or meta.get("name") or "").lower()
            content = str(meta.get("content") or "").strip()
            if not content:
                continue
            if prop in ("og:image", "twitter:image", "twitter:image:src", "image"):
                kind = "og" if "og:" in prop else ("twitter" if "twitter" in prop else "meta")
                _push({
                    "src": content,
                    "alt": str(soup.title.get_text(strip=True) if soup.title else ""),
                    "title": "",
                    "width": None, "height": None,
                    "kind": kind, "page_url": url,
                })
            if len(out) >= max_images:
                break

        # 2) JSON-LD image
        for ld in soup.find_all("script", type="application/ld+json"):
            txt = ld.get_text(strip=True)
            if not txt or "image" not in txt.lower():
                continue
            try:
                data = json.loads(txt)
            except Exception:
                continue
            stack = [data]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k.lower() == "image" and isinstance(v, (list, str, dict)):
                            if isinstance(v, str):
                                _push({"src": v, "alt": "", "title": "", "width": None, "height": None, "kind": "jsonld", "page_url": url})
                            elif isinstance(v, list):
                                for it in v:
                                    if isinstance(it, str):
                                        _push({"src": it, "alt": "", "title": "", "width": None, "height": None, "kind": "jsonld", "page_url": url})
                                    elif isinstance(it, dict):
                                        s = it.get("url") or it.get("contentUrl")
                                        if s:
                                            _push({
                                                "src": s,
                                                "alt": str(it.get("caption") or ""),
                                                "title": str(it.get("name") or ""),
                                                "width": it.get("width"),
                                                "height": it.get("height"),
                                                "kind": "jsonld", "page_url": url,
                                            })
                        else:
                            stack.append(v)
                elif isinstance(node, list):
                    stack.extend(node)
            if len(out) >= max_images:
                break

        # 3) <img> 标签：综合 src / 懒加载属性 / srcset，且解析 parent <a> 作为 page_url
        for img in soup.find_all("img"):
            if len(out) >= max_images:
                break
            attrs = img.attrs or {}
            alt = str(attrs.get("alt") or "").strip()
            title = str(attrs.get("title") or "").strip()
            # 解析尺寸
            w = attrs.get("width")
            h = attrs.get("height")
            try:
                width_i = int(w) if isinstance(w, (str, int)) and str(w).lstrip("-").isdigit() else None
                height_i = int(h) if isinstance(h, (str, int)) and str(h).lstrip("-").isdigit() else None
            except Exception:
                width_i, height_i = None, None
            if (width_i is not None and width_i < min_width) or (height_i is not None and height_i < min_height):
                continue

            # parent link
            page_url = url
            parent_a = img.find_parent("a", href=True)
            if parent_a:
                page_url = urljoin(base_url, parent_a["href"])

            # 候选源：优先级 lazy srcset > lazy src > srcset > src（懒加载属性往往是真实图）
            candidates: List[str] = []
            lazy_keys = ("data-src", "data-original", "data-lazy", "data-srcset", "data-url", "data-full")
            src_keys = ("src", "srcset")
            for k in lazy_keys:
                if attrs.get(k):
                    candidates.extend(_parse_srcset(str(attrs[k])))
            for k in src_keys:
                if attrs.get(k):
                    candidates.extend(_parse_srcset(str(attrs[k])))
            # 去重保留顺序
            added: set = set()
            ordered: List[str] = []
            for c in candidates:
                if c not in added:
                    added.add(c)
                    ordered.append(c)
            if ordered:
                # 首张图作为主图记录
                main_src = ordered[0]
                _push({
                    "src": main_src,
                    "alt": alt,
                    "title": title,
                    "width": width_i,
                    "height": height_i,
                    "kind": "lazy" if any(attrs.get(k) for k in lazy_keys) else "src",
                    "page_url": page_url,
                })
                # 其他尺寸也记录（最多再追加 1 条，srcset 常含多个）
                for s in ordered[1:2]:
                    _push({
                        "src": s,
                        "alt": alt,
                        "title": title,
                        "width": width_i,
                        "height": height_i,
                        "kind": "srcset",
                        "page_url": page_url,
                    })
            # inline style 背景
            style = str(attrs.get("style") or "")
            for bg in _parse_style_bg(style):
                _push({
                    "src": bg,
                    "alt": alt,
                    "title": title,
                    "width": width_i,
                    "height": height_i,
                    "kind": "bg",
                    "page_url": page_url,
                })

        # 4) 通用元素 style 背景图（div/section/li/figure 等带 background-image）
        if len(out) < max_images:
            for tag in soup.find_all(["div", "section", "li", "figure", "article", "span", "a"]):
                if len(out) >= max_images:
                    break
                style = str(tag.get("style") or "")
                if "background" not in style.lower():
                    continue
                page_url = url
                if tag.name == "a" and tag.get("href"):
                    page_url = urljoin(base_url, tag["href"])
                for bg in _parse_style_bg(style):
                    _push({
                        "src": bg,
                        "alt": "",
                        "title": "",
                        "width": None,
                        "height": None,
                        "kind": "bg",
                        "page_url": page_url,
                    })

        # 排序：内容评分优先（壁纸/高清/封面等），其次 kind 优先级，再次带尺寸信息
        def _sort_key(x):
            content = -_content_image_score(x.get("src", ""), x.get("alt", ""))
            has_size = 0 if (x.get("width") and x.get("height")) else 1
            pref_kind = {"og": 0, "twitter": 1, "meta": 2, "jsonld": 3, "lazy": 4, "srcset": 5, "src": 6, "bg": 7, "link": 8}.get(x.get("kind", ""), 5)
            return (content, pref_kind, has_size)

        out.sort(key=_sort_key)
        return out[:max_images]

    def _fallback_generic(self, result: CrawlResult, base_url: str, target_schema: Type[BaseModel]) -> List[BaseModel]:
        """通用兜底提取：从 HTML 中提取 title、links 和 images"""
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin
        
        soup = BeautifulSoup(result.html, "html.parser")
        items = []
        
        # 提取页面标题
        title = soup.find("title")
        title_text = title.get_text(strip=True) if title else ""
        
        # 提取主要图片（og:image、大图）
        main_image = None
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            main_image = og_image["content"].strip()
        
        # 提取所有图片链接（过滤小图标）
        images = []
        for img in soup.find_all("img", src=True):
            src = img["src"].strip()
            if not src or src.startswith(("data:", "#", "javascript:")):
                continue
            abs_img = urljoin(base_url, src)
            alt = img.get("alt", "").strip()
            # 过滤掉小图标
            width = img.get("width")
            height = img.get("height")
            if width and str(width).isdigit() and int(width) < 50:
                continue
            if height and str(height).isdigit() and int(height) < 50:
                continue
            images.append({"src": abs_img, "alt": alt})
        
        # 提取链接（同时提取链接内的图片）
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            abs_url = urljoin(base_url, href)
            text = a.get_text(strip=True) or href
            
            # 尝试从链接内提取图片
            img = a.find("img", src=True)
            img_src = None
            if img:
                src = img.get("src", "").strip()
                if src and not src.startswith(("data:", "#", "javascript:")):
                    img_src = urljoin(base_url, src)
            
            links.append({"title": text, "url": abs_url, "image": img_src})
        
        schema_fields = target_schema.model_fields.keys()
        has_image_field = any(k in schema_fields for k in ["image", "img", "picture", "photo", "src", "image_url"])
        has_title_field = "title" in schema_fields
        has_url_field = "url" in schema_fields
        has_content_field = "content" in schema_fields
        
        # 判断是否是"详情页"（单页，URL 包含 look/detail/item 等关键词）
        is_detail_page = any(k in base_url.lower() for k in ["look", "detail", "item", "show", "view", "/p/", "/post/"])
        
        if has_image_field and images:
            # 图片字段明确：每张图片一条
            for img in images:
                item = {
                    "title": img.get("alt") or title_text,
                    "image": img["src"],
                    "url": img["src"],
                }
                items.append(item)
        elif is_detail_page and (main_image or images):
            # 详情页模式：返回页面标题 + 主图 URL
            item = {"title": title_text, "url": result.url}
            if main_image:
                item["url"] = urljoin(base_url, main_image)
                item["image"] = item["url"]
            elif images:
                # 选最大的那张（简单策略：选 alt 非空的第一个，或第一个）
                best = next((img for img in images if img.get("alt")), images[0])
                item["url"] = best["src"]
                item["image"] = best["src"]
                if not item["title"] and best.get("alt"):
                    item["title"] = best["alt"]
            if has_content_field:
                item["content"] = title_text
            items.append(item)
        elif links:
            # 列表页模式：链接为主，附带图片
            for link in links:
                item = {
                    "title": link["title"],
                    "url": link["url"],
                    "source_title": title_text,
                }
                if link.get("image"):
                    item["image"] = link["image"]
                items.append(item)
        elif title_text:
            items.append({"title": title_text, "url": result.url})
        
        # 转换为 target_schema
        results = []
        for item in items:
            try:
                model = target_schema(**item)
                results.append(model)
            except Exception:
                continue
        
        return results


def _parse_srcset(value: str) -> List[str]:
    """解析 srcset / data-srcset 字符串，按宽度从大到小返回 URL 列表。

    示例输入：
      "https://a.jpg 1x, https://b.jpg 2x, https://c.jpg 1200w"
    输出：[b.jpg, c.jpg, a.jpg] 或按原始顺序中可靠部分优先
    """
    if not value:
        return []
    value = value.strip()
    # data URI（base64 内嵌图）整体跳过：内含逗号会被误切成伪 URL 片段
    if value.lower().startswith("data:") or ";base64," in value.lower():
        return []
    if "," not in value and " " not in value.strip():
        # 普通单个 URL
        v = value.strip()
        return [v] if v and not v.lower().startswith("data:") else []

    entries: List[tuple] = []
    for part in re.split(r",\s*", value):
        part = part.strip()
        if not part:
            continue
        tokens = part.rsplit(maxsplit=1)
        if len(tokens) == 1:
            url = tokens[0]
            size = 0
        else:
            url, sz = tokens
            sz = sz.strip().lower()
            # 1x / 2x / 1200w
            if sz.endswith("x"):
                try:
                    size = int(float(sz[:-1]) * 1000)
                except ValueError:
                    size = 0
            elif sz.endswith("w") or sz.endswith("h"):
                try:
                    size = int(sz[:-1])
                except ValueError:
                    size = 0
            else:
                size = 0
        url = url.strip().strip("'\"")
        if url and not url.lower().startswith("data:"):
            entries.append((-size, url))
    entries.sort()
    return [u for _, u in entries]


def _is_ui_noise_image(src: str = "", alt: str = "", kind: str = "") -> bool:
    """判断是否为 UI/装饰噪声图（非内容图）：favicon、logo、图标、头像、默认背景等。

    通用规则（不过度特化单个站点）：
    - URL 特征：favicon / pwa / logo / icon / avatar / banner / defaultBg / bg 装饰
    - alt 特征：上传图片 / 用户头像 / 图标 / logo 等 UI 文案
    - kind=bg 且 URL 含默认背景特征（defaultBg / background-image 装饰）
    """
    low_src = (src or "").lower()
    low_alt = (alt or "").strip().lower()

    # 1) URL 路径特征
    src_markers = (
        "favicon", "pwa-", "/logo", "logo.", "logo_", "logo-",
        ".icon", "_icon", "icon.", "icon_", "icon-",
        "avatar", "userimg", "headimg",
        "defaultbg", "default_bg", "bg-default", "background-default",
        "banner-bg", "nav-bg", "header-bg", "footer-bg",
    )
    if any(m in low_src for m in src_markers):
        return True

    # 2) 构建产物（/_nuxt/、/static/ 下的资源通常非内容图）
    if "/_nuxt/" in low_src or "/_assets/" in low_src:
        return True

    # 3) alt 为 UI 文案
    alt_markers = ("上传图片", "用户头像", "网页icon", "图标", "logo", "icon", "avatar")
    if low_alt and any(m in low_alt for m in alt_markers):
        return True

    return False


def _content_image_score(src: str = "", alt: str = "") -> int:
    """内容图加分：alt 含描述性关键词或 URL 含内容图特征 → 返回正分（排序优先）。

    通用内容特征（壁纸/封面/相册/高清图等）：
    - alt 含 壁纸/高清/背景图/封面/摄影/图片 等描述词（排除 UI 噪声已在上层过滤）
    - URL 含 wallpaper / wall / cover / photo / pic / img / upload / 高清 等路径特征
    """
    score = 0
    low_alt = (alt or "").strip().lower()
    low_src = (src or "").lower()
    alt_content = ("壁纸", "wallpaper", "高清", "背景图", "封面", "摄影", "图片", "照片", "桌面")
    for kw in alt_content:
        if kw in low_alt:
            score += 1
            break
    src_content = ("wallpaper", "/wall/", "getcroppingimg", "/cover", "/photo", "/pic", "upload/", "image/", "/img/")
    for kw in src_content:
        if kw in low_src:
            score += 1
            break
    return score


def _parse_style_bg(style: str) -> List[str]:
    """从 style 字符串中提取 background / background-image 的 url(...)。"""
    if not style or "background" not in style.lower():
        return []
    results: List[str] = []
    for m in re.finditer(
        r"url\(\s*(['\"]?)([^'\"\)]+)\1\s*\)",
        style,
        flags=re.IGNORECASE,
    ):
        url = m.group(2).strip()
        if url and not url.lower().startswith("data:"):
            results.append(url)
    return results


def create_dynamic_schema(fields: Dict[str, Dict], schema_name: str = "DynamicItem") -> Type[BaseModel]:
    """
    根据字段定义动态创建 Pydantic 模型
    fields 格式: {"field_name": {"type": "str", "description": "描述", "required": True}}
    """
    type_map = {
        "str": (str, ...),
        "int": (int, ...),
        "float": (float, ...),
        "bool": (bool, ...),
        "list": (List[Any], ...),
        "dict": (Dict[str, Any], ...),
        "optional_str": (Optional[str], None),
        "optional_int": (Optional[int], None),
    }

    field_definitions = {}
    for name, spec in fields.items():
        if not isinstance(spec, dict):
            # LLM 可能直接返回 {"title": "str"} 格式
            spec = {"type": str(spec) if spec else "str"}
        
        field_type = spec.get("type", "str")
        # 所有字段都设为 Optional，LLM 选择器可能匹配不到某些字段
        if field_type in type_map:
            py_type, _ = type_map[field_type]
            py_type = Optional[py_type]
        else:
            py_type = Optional[str]
        field_definitions[name] = (py_type, None)

    model = create_model(
        schema_name, 
        **field_definitions,
        __config__=ConfigDict(extra="allow")
    )
    return model


# 导出
DEFAULT_EXTRACTOR = CompositeExtractor()

__all__ = [
    "BaseExtractor",
    "SelectolaxExtractor",
    "LxmlExtractor",
    "LLMExtractor",
    "CompositeExtractor",
    "ExtractionContext",
    "create_dynamic_schema",
    "DEFAULT_EXTRACTOR",
]
