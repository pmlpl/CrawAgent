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
from selectolax.parser import HTMLParser

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


class SelectolaxExtractor(BaseExtractor):
    """Selectolax CSS 选择器提取器（快）"""

    def extract(self, ctx: ExtractionContext) -> List[BaseModel]:
        if not ctx.html or not ctx.selectors.item:
            return []

        parser = HTMLParser(ctx.html)
        container = None

        if ctx.selectors.list_container:
            container = parser.css_first(ctx.selectors.list_container)
            if not container:
                return []
        else:
            container = parser

        items = container.css(ctx.selectors.item)
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
            sel = self._convert_selector(ctx.selectors.title)
            el = node.css_first(sel)
            if el:
                data["title"] = self._normalize_text(el.text())

        # URL
        if ctx.selectors.url:
            sel = self._convert_selector(ctx.selectors.url)
            el = node.css_first(sel)
            if el:
                href = el.attributes.get("href") or el.text()
                if href:
                    data["url"] = urljoin(ctx.base_url, href.strip())

        # 额外字段
        for field_name, selector in ctx.selectors.extra.items():
            sel = self._convert_selector(selector)
            el = node.css_first(sel)
            if el:
                if el.tag in ("a", "link"):
                    data[field_name] = urljoin(ctx.base_url, el.attributes.get("href", "").strip())
                elif el.tag in ("img", "image"):
                    data[field_name] = urljoin(ctx.base_url, el.attributes.get("src", "").strip())
                else:
                    data[field_name] = self._normalize_text(el.text())

        # 如果没有任何字段，返回 None
        if not data:
            return None

        data["_source_index"] = idx
        return data


class LxmlExtractor(BaseExtractor):
    """lxml XPath/CSS 提取器（强）"""

    def _convert_selector(self, selector: str) -> str:
        """转换 @attr 语法为标准 CSS"""
        if not selector or '@' not in selector:
            return selector
        parts = selector.split('@')
        if len(parts) == 2:
            tag, attr = parts
            return f"{tag}[{attr}]"
        return selector

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
            containers = doc.cssselect(ctx.selectors.list_container)
            if not containers:
                return []
            root = containers[0]
        else:
            root = doc

        items = root.cssselect(ctx.selectors.item)
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
            sel = self._convert_selector(ctx.selectors.title)
            els = node.cssselect(sel)
            if els:
                data["title"] = self._normalize_text(els[0].text_content())

        # URL
        if ctx.selectors.url:
            sel = self._convert_selector(ctx.selectors.url)
            els = node.cssselect(sel)
            if els:
                href = els[0].get("href") or els[0].text_content()
                if href:
                    data["url"] = urljoin(ctx.base_url, href.strip())

        # 额外字段
        for field_name, selector in ctx.selectors.extra.items():
            sel = self._convert_selector(selector)
            els = node.cssselect(sel)
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

            # 尝试解析 JSON
            import json
            json_match = re.search(r"\[.*\]", response.content, re.DOTALL)
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
    """表格提取器：HTML <table> → 行字典列表（P2-3）。"""

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
                item = {header[i]: cells[i] for i in range(len(header))}
                try:
                    results.append(ctx.target_schema(**item))
                except Exception:
                    continue
        return results


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

        from selectolax.parser import HTMLParser

        parser = HTMLParser(ctx.html)
        items_sel = tree.get("items")
        fields = tree.get("fields", {})
        if not items_sel:
            node = parser.body or parser
            item = self._extract_node(node, fields, ctx)
            try:
                return [ctx.target_schema(**item)] if item else []
            except Exception:
                return []

        nodes = parser.css(items_sel)
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
                    sub_nodes = node.css(conf["items"])
                    item[name] = [self._extract_node(n, conf.get("fields", {}), ctx) for n in sub_nodes]
                else:
                    item[name] = self._extract_node(node, conf.get("fields", {}), ctx)
                continue

            selector = conf.get("selector") if isinstance(conf, dict) else conf
            attr = conf.get("attribute") if isinstance(conf, dict) else None
            default = conf.get("default") if isinstance(conf, dict) else None
            el = node.css_first(selector) if selector else node
            if el is None:
                if default is not None:
                    item[name] = default
                continue
            if attr:
                value = el.attributes.get(attr, "")
                if attr == "href" and value and ctx.base_url:
                    from urllib.parse import urljoin
                    value = urljoin(ctx.base_url, value)
            else:
                value = el.text().strip()
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
        self._css = SelectolaxExtractor()
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

        # 1. 尝试 CSS (selectolax)
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

        # 3. LLM 兜底
        logger.debug("选择器均失效，启用 LLM 兜底提取")
        results = self._llm.extract(ctx)
        logger.debug(f"LLM 提取: {len(results)} 条")
        return results

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
