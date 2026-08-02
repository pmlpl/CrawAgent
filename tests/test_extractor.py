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


# ==================== 提取链路健壮性回归（2026-08-02 e2e 修复） ====================

def test_css_extract_item_url_fallback():
    """无 url 选择器匹配时回填页面 URL，避免 ExtractedItem 校验失败被丢弃。"""
    html = """
    <html><body><div class="list">
      <div class="item"><span class="text">名言一</span><small class="author">作者A</small></div>
      <div class="item"><span class="text">名言二</span><small class="author">作者B</small></div>
    </div></body></html>
    """
    schema = _schema(title={"type": "str", "description": "标题"}, url={"type": "str", "description": "链接"})
    selectors = Selectors(item=".item", title=".text", url="", extra={"author": ".author"})
    items = _ctx(html, schema, selectors)
    assert len(items) == 2
    # url 必填字段回填为页面 URL
    assert all(i.url == "https://example.com/a" for i in items)
    assert items[0].title == "名言一"
    assert items[0].author == "作者A"


def test_css_extract_empty_extra_selector_no_crash():
    """extra 字段含空选择器时不崩溃（lxml cssselect('') 抛 EOF 错误）。"""
    html = """
    <html><body><div class="item"><h3>标题</h3><a href="/x">链接</a></div></body></html>
    """
    schema = _schema(title={"type": "str", "description": "标题"}, url={"type": "str", "description": "链接"})
    selectors = Selectors(item=".item", title="h3", url="a@href", extra={"bad": "", "empty": "  "})
    items = _ctx(html, schema, selectors)
    assert len(items) == 1
    assert items[0].title == "标题"


def test_css_extract_invalid_selector_no_crash():
    """非法/损坏选择器（尾随逗号）不抛异常，返回空结果或部分结果。"""
    html = '<html><body><div class="item"><h3>标题</h3></div></body></html>'
    schema = _schema(title={"type": "str", "description": "标题"})
    selectors = Selectors(item=".item,", title="h3,,", url="a[href=,", extra={"x": ".item > :nth-child("})
    items = _ctx(html, schema, selectors)
    # 不抛异常即可；item 选择器损坏时应返回空列表
    assert isinstance(items, list)


def test_llm_extractor_empty_content_no_crash(monkeypatch):
    """LLM 返回空 content（None）时不抛 TypeError。"""
    from crawagent.core.extractor import LLMExtractor, ExtractionContext
    from crawagent.core.models import Selectors

    class _FakeLLM:
        def invoke(self, prompt):
            class _Resp:
                content = None
            return _Resp()

    html = "<html><body><div class='item'>内容</div></body></html>"
    schema = _schema(title={"type": "str", "description": "标题"})
    llm = LLMExtractor()
    # 实例级注入 fake LLM（property 检查实例属性 _llm）
    monkeypatch.setattr(llm, "_llm", _FakeLLM())
    items = llm.extract(
        ExtractionContext(url="https://example.com", html=html, selectors=Selectors(),
                          target_schema=schema, base_url="https://example.com")
    )
    assert items == []


# ==================== 表格提取加强（2026-08-02 表格页质量修复） ====================

_TABLE_HTML = """
<html><body><table>
  <tr><th>产品名称</th><th>尺寸</th><th>价格</th><th>描述</th></tr>
  <tr><td>MacBook Air</td><td>13.6"</td><td>999</td><td>起始价</td></tr>
  <tr><td>MacBook Pro</td><td>14"</td><td>1599</td><td>专业之选</td></tr>
</table></body></html>
"""


def test_table_extractor_generic_mapping_to_extracted_item():
    """中文表头表格 → ExtractedItem 通用字段（title/url/content）。"""
    from crawagent.core.extractor import TableExtractor, ExtractionContext
    from crawagent.core.models import ExtractedItem, Selectors

    items = TableExtractor().extract(
        ExtractionContext(
            url="https://example.com/list", html=_TABLE_HTML,
            selectors=Selectors(), target_schema=ExtractedItem, base_url="https://example.com",
        )
    )
    assert len(items) == 2
    # title 命中"产品名称"列，url 回填页面 URL
    assert items[0].title == "MacBook Air"
    assert items[0].url == "https://example.com/list"
    # 其余列拼入 content
    assert "尺寸: 13.6\"" in items[0].content
    assert "价格: 999" in items[0].content


def test_table_extractor_direct_field_match_unchanged():
    """表头与 schema 字段名直配时保持原映射行为（name/price）。"""
    schema = _schema(name={"type": "str", "description": "名称"}, price={"type": "str", "description": "价格"})
    html = """
    <html><body><table>
      <tr><th>name</th><th>price</th></tr>
      <tr><td>苹果</td><td>5.5</td></tr>
    </table></body></html>
    """
    items = TableExtractor().extract(
        __import__("crawagent.core.extractor", fromlist=["ExtractionContext"]).ExtractionContext(
            url="", html=html, selectors=Selectors(), target_schema=schema, base_url="",
        )
    )
    assert len(items) == 1
    assert items[0].name == "苹果"
    assert items[0].price == "5.5"


def test_composite_table_fallback_when_css_empty():
    """CSS 选择器不匹配但页面含表格时，走 table 回退提取。"""
    schema = _schema(title={"type": "str", "description": "标题"}, url={"type": "str", "description": "链接"})
    selectors = Selectors(item=".nonexistent", title=".nonexistent", url="")
    items = _ctx(_TABLE_HTML, schema, selectors)
    # 表格回退生效：提取到 2 行
    assert len(items) == 2
    assert items[0].title == "MacBook Air"
    assert items[0].url == "https://example.com/a"


def test_has_table_detection():
    from crawagent.core.extractor import CompositeExtractor
    assert CompositeExtractor._has_table(_TABLE_HTML) is True
    assert CompositeExtractor._has_table("<html><body><ul><li>a</li></ul></body></html>") is False
    assert CompositeExtractor._has_table("") is False


async def test_extract_executor_table_strategy_passthrough(tmp_path, monkeypatch):
    """method=table 时 extract_executor 走 table 策略（策略透传）。"""
    import crawagent.harness.tools as tools
    from crawagent.core.extractor import CompositeExtractor

    captured = {}

    def fake_extract(self, **kwargs):
        captured["strategy"] = kwargs.get("strategy")
        captured["html"] = kwargs.get("html", "")
        # 模拟 table 策略返回 5 条（confidence 75 ≥ 70 阈值）
        from crawagent.core.models import ExtractedItem
        return [ExtractedItem(title=f"t{i}", url="u", content="c") for i in range(5)]

    monkeypatch.setattr(CompositeExtractor, "extract", fake_extract)
    result = await tools.extract_executor({
        "html": _TABLE_HTML,
        "url": "https://example.com/list",
        "method": "table",
    })
    assert captured["strategy"] == "table"
    assert result["status_code"] == 200


async def test_extract_executor_default_no_strategy(tmp_path, monkeypatch):
    """method=css（默认）时不强制策略，保持默认回退链。"""
    import crawagent.harness.tools as tools
    from crawagent.core.extractor import CompositeExtractor

    captured = {}

    def fake_extract(self, **kwargs):
        captured["strategy"] = kwargs.get("strategy")
        return []

    monkeypatch.setattr(CompositeExtractor, "extract", fake_extract)
    await tools.extract_executor({
        "html": "<html><body>hi</body></html>",
        "url": "https://example.com",
        "method": "css",
    })
    assert captured["strategy"] is None


# ==================== 图片提取加强（图片站训练） ====================

_IMG_HTML = """
<html><head><title>壁纸站</title>
<meta property="og:image" content="/favicon.ico">
</head><body>
<div class="main-bg" style="background-image:url('/_nuxt/defaultBgV2.abc.jpg')"></div>
<img src="/favicon.ico" alt="网页icon">
<img src="/link/common/file/getCroppingImg/111" alt="4k壁纸-城市夜景「壁纸站」">
<img src="/link/common/file/getCroppingImg/222" alt="5k卡通壁纸-海绵宝宝「壁纸站」">
<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==" alt="上传图片">
<img src="/user/avatar/123.png" alt="用户头像">
<img src="https://cdn.example.com/cover/photo_1.jpg" alt="摄影作品">
</body></html>
"""


def test_extract_images_filters_ui_noise():
    """favicon/图标/头像/默认背景/上传图应被过滤，只留内容图。"""
    ex = CompositeExtractor()
    imgs = ex.extract_images(
        url="https://haowallpaper.com/",
        html=_IMG_HTML,
        min_width=0, min_height=0, max_images=100,
    )
    srcs = [i["src"] for i in imgs]
    # 噪声图全部排除
    assert "https://haowallpaper.com/favicon.ico" not in srcs
    assert "https://haowallpaper.com/_nuxt/defaultBgV2.abc.jpg" not in srcs
    assert "https://haowallpaper.com/user/avatar/123.png" not in srcs
    assert not any(s.startswith("data:") for s in srcs)
    assert not any("iVBORw0" in s for s in srcs)  # base64 片段未误拼接
    # 内容图保留
    assert "https://haowallpaper.com/link/common/file/getCroppingImg/111" in srcs
    assert "https://haowallpaper.com/link/common/file/getCroppingImg/222" in srcs
    assert "https://cdn.example.com/cover/photo_1.jpg" in srcs


def test_extract_images_content_images_first():
    """壁纸/封面等内容图应排在噪声之前的合理顺序（内容评分优先）。"""
    ex = CompositeExtractor()
    imgs = ex.extract_images(
        url="https://haowallpaper.com/",
        html=_IMG_HTML,
        min_width=0, min_height=0, max_images=100,
    )
    srcs = [i["src"] for i in imgs]
    # 首条必须是内容图（壁纸或封面），不是 favicon
    assert srcs and "favicon" not in srcs[0]
    assert any("getCroppingImg" in s for s in srcs)


def test_extract_images_empty_html():
    ex = CompositeExtractor()
    assert ex.extract_images(url="https://example.com/", html="") == []


def test_parse_srcset_skips_data_uri():
    from crawagent.core.extractor import _parse_srcset
    # base64 data URI 不应被逗号切分产生伪 URL
    assert _parse_srcset("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==") == []
    assert _parse_srcset("https://a.com/1.jpg 1x, https://a.com/2.jpg 2x") == [
        "https://a.com/2.jpg", "https://a.com/1.jpg",
    ]


def test_is_ui_noise_image_rules():
    from crawagent.core.extractor import _is_ui_noise_image
    assert _is_ui_noise_image("https://a.com/favicon.ico") is True
    assert _is_ui_noise_image("https://a.com/pwa-108.png") is True
    assert _is_ui_noise_image("https://a.com/_nuxt/logo.2b.jpg") is True
    assert _is_ui_noise_image("https://a.com/img/cover/1.jpg", alt="摄影作品") is False
    assert _is_ui_noise_image("https://a.com/wallpaper/1080.jpg", alt="4k壁纸") is False
    assert _is_ui_noise_image("https://a.com/u/avatar.png", alt="用户头像") is True
