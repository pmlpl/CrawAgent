"""P2-3 提取器测试：8 种策略 + CompositeExtractor 兼容。"""

import pytest

from crawagent.core.extractor import (
    CompositeExtractor,
    JsonCssExtractor,
    JsonLdExtractor,
    MetaExtractor,
    RegexExtractor,
    TableExtractor,
    create_dynamic_schema,
)
from crawagent.core.models import Selectors


def _schema(**fields):
    return create_dynamic_schema(fields)


def _ctx(html, schema, selectors=None):
    extractor = CompositeExtractor()
    return extractor.extract(
        url="https://example.com/a",
        html=html,
        selectors=selectors or Selectors(),
        target_schema=schema,
    )


def test_composite_css_fallback_still_works():
    html = """
    <html><body>
      <div class="list"><div class="item"><h3>标题A</h3><a href="/a">链接A</a></div></div>
    </body></html>
    """
    schema = _schema(title={"type": "str", "description": "标题"}, url={"type": "str", "description": "链接"})
    selectors = Selectors(item=".item", title="h3", url="a@href")
    items = _ctx(html, schema, selectors)
    assert len(items) == 1
    assert items[0].title == "标题A"


def test_jsonld_extractor():
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"Article",
       "headline":"JSON-LD 测试文章","url":"https://example.com/a",
       "articleBody":"这是正文内容","datePublished":"2026-08-01"}
      </script>
    </head><body></body></html>
    """
    schema = _schema(
        title={"type": "str", "description": "标题"},
        url={"type": "str", "description": "链接"},
        content={"type": "str", "description": "正文"},
    )
    items = JsonLdExtractor().extract(
        __import__("crawagent.core.extractor", fromlist=["ExtractionContext"]).ExtractionContext(
            url="https://example.com/a", html=html, selectors=Selectors(),
            target_schema=schema, base_url="https://example.com",
        )
    )
    assert len(items) == 1
    assert items[0].title == "JSON-LD 测试文章"
    assert items[0].content == "这是正文内容"


def test_meta_extractor():
    html = """
    <html><head>
      <meta property="og:title" content="OG 标题">
      <meta property="og:url" content="https://example.com/b">
      <meta property="og:image" content="https://example.com/img.jpg">
      <meta property="og:description" content="描述文本">
    </head><body></body></html>
    """
    schema = _schema(
        title={"type": "str", "description": "标题"},
        url={"type": "str", "description": "链接"},
        image={"type": "str", "description": "图片"},
    )
    items = MetaExtractor().extract(
        __import__("crawagent.core.extractor", fromlist=["ExtractionContext"]).ExtractionContext(
            url="https://example.com/b", html=html, selectors=Selectors(),
            target_schema=schema, base_url="https://example.com",
        )
    )
    assert len(items) == 1
    assert items[0].title == "OG 标题"
    assert items[0].url == "https://example.com/b"


def test_table_extractor():
    html = """
    <html><body><table>
      <tr><th>name</th><th>price</th></tr>
      <tr><td>苹果</td><td>5.5</td></tr>
      <tr><td>香蕉</td><td>3.2</td></tr>
    </table></body></html>
    """
    schema = _schema(name={"type": "str", "description": "名称"}, price={"type": "str", "description": "价格"})
    items = TableExtractor().extract(
        __import__("crawagent.core.extractor", fromlist=["ExtractionContext"]).ExtractionContext(
            url="", html=html, selectors=Selectors(), target_schema=schema, base_url="",
        )
    )
    assert len(items) == 2
    assert items[0].name == "苹果"


def test_regex_extractor():
    html = "<html><body><p>价格: ￥99.90 库存: 10 件</p></body></html>"
    schema = _schema(price={"type": "str", "description": "价格"})
    items = RegexExtractor(patterns={"price": r"价格[:：]\s*￥?([\d.]+)"}).extract(
        __import__("crawagent.core.extractor", fromlist=["ExtractionContext"]).ExtractionContext(
            url="", html=html, selectors=Selectors(), target_schema=schema, base_url="",
        )
    )
    assert len(items) == 1
    assert items[0].price == "99.90"


def test_json_css_extractor():
    html = """
    <html><body>
      <div class="item"><h3>商品一</h3><a href="/p/1">链接</a></div>
      <div class="item"><h3>商品二</h3><a href="/p/2">链接</a></div>
    </body></html>
    """
    schema = _schema(title={"type": "str", "description": "标题"}, url={"type": "str", "description": "链接"})
    tree = {
        "items": ".item",
        "fields": {
            "title": "h3",
            "url": {"selector": "a", "attribute": "href"},
        },
    }
    items = JsonCssExtractor(tree).extract(
        __import__("crawagent.core.extractor", fromlist=["ExtractionContext"]).ExtractionContext(
            url="https://example.com/list", html=html, selectors=Selectors(),
            target_schema=schema, base_url="https://example.com",
        )
    )
    assert len(items) == 2
    assert items[0].url == "https://example.com/p/1"


def test_composite_strategy_param():
    html = '<html><head><meta property="og:title" content="直接策略标题"></head><body></body></html>'
    schema = _schema(title={"type": "str", "description": "标题"})
    items = CompositeExtractor().extract(
        url="https://example.com", html=html, selectors=Selectors(),
        target_schema=schema, strategy="meta",
    )
    assert items and items[0].title == "直接策略标题"


def test_composite_unknown_strategy():
    with pytest.raises(ValueError):
        CompositeExtractor().extract(
            url="", html="<html></html>", selectors=Selectors(),
            target_schema=_schema(title="标题"), strategy="nope",
        )
