"""P2-1 内容过滤器测试：三档过滤器 + 脏数据不崩。"""

import pytest

from crawagent.core.content_filter import (
    BM25ContentFilter,
    PruningContentFilter,
    create_content_filter,
)


ARTICLE_HTML = """
<html>
<head>
  <title>Python 爬虫教程 - 2026</title>
  <meta name="description" content="Python 爬虫教程，requests 与 BeautifulSoup 实战">
</head>
<body>
  <nav><a href="/">首页</a><a href="/login">登录</a></nav>
  <header><h1>Python 爬虫教程</h1></header>
  <div id="sidebar"><div class="advert">广告：点击购买</div></div>
  <article>
    <p>Python 爬虫使用 requests 库发送 HTTP 请求。</p>
    <p>BeautifulSoup 用于解析 HTML 文档，提取正文数据。</p>
    <div class="comment">评论区：楼主说得对。</div>
  </article>
  <footer>版权所有 © 2026</footer>
  <script>alert("noise")</script>
</body>
</html>
"""


def test_prune_removes_noise_keeps_article():
    result = PruningContentFilter().filter_content(ARTICLE_HTML)
    assert "nav" not in result
    assert "script" not in result
    assert "sidebar" not in result
    assert "advert" not in result
    assert "footer" not in result
    assert "BeautifulSoup" in result
    assert "requests" in result


def test_prune_does_not_kill_body_with_ad_in_class():
    """回归：body class 含 "ad"（如 dd-apple-upgrade）不得被负模式误删。"""
    html = """
    <html><body class="dd-apple-upgrade-202607-theme">
      <div class="ad-box">广告位</div>
      <article><p>正文内容必须保留。</p></article>
    </body></html>
    """
    result = PruningContentFilter().filter_content(html)
    assert "<body" in result
    assert "正文内容必须保留" in result
    assert "广告位" not in result


def test_prune_empty_body():
    assert PruningContentFilter().filter_content("") == ""
    assert PruningContentFilter().filter_content("   ") == ""


def test_prune_pure_script_does_not_crash():
    html = "<html><body><script>var a=1;</script><style>.x{}</style></body></html>"
    result = PruningContentFilter().filter_content(html)
    assert "script" not in result
    assert "style" not in result


def test_prune_one_mb_html_fast():
    """1MB 超大 HTML 清洗不崩（验收用例）。"""
    big_html = (
        "<html><body>"
        + "<nav>" + "菜单" * 1000 + "</nav>"
        + "<article>" + (("<p>" + "正文内容段落内容。" * 12 + "</p>") * 15000) + "</article>"
        + "<footer>页脚</footer>"
        + "</body></html>"
    )
    assert len(big_html.encode()) > 1_000_000
    result = PruningContentFilter().filter_content(big_html)
    assert "正文内容段落" in result
    assert "<nav>" not in result


def test_bm25_keeps_relevant_chunks():
    html = """
    <html><head><title>服务器监控工具对比</title></head>
    <body>
      <p>Prometheus 是一款开源监控系统，支持多维数据模型。</p>
      <p>Zabbix 提供企业级监控告警能力。</p>
      <p>今天天气很好，适合出去散步。</p>
    </body></html>
    """
    result = BM25ContentFilter(threshold=0.3).filter_content(html)
    assert "Prometheus" in result
    assert "散步" not in result


def test_bm25_empty_and_noise():
    assert BM25ContentFilter().filter_content("") == ""
    # 无查询时原样返回
    result = BM25ContentFilter().filter_content("<html><body><p>hello</p></body></html>")
    assert "hello" in result


def test_create_content_filter_factory():
    assert isinstance(create_content_filter("prune"), PruningContentFilter)
    assert isinstance(create_content_filter("bm25"), BM25ContentFilter)
    with pytest.raises(ValueError):
        create_content_filter("unknown")


def test_prune_keeps_media_nodes():
    """回归：img/picture/video 等媒体节点无文本也不得被当作低内容节点删除
    （图片站 clean 后 img 丢失会导致 extract_images 提取不到壁纸）。"""
    html = """
    <html><body>
      <div class="wallpaper-grid">
        <a href="/w/1"><img src="/img/1.jpg" alt="4k壁纸"></a>
        <a href="/w/2"><img src="/img/2.jpg" alt="5k壁纸"></a>
      </div>
      <p>说明文字</p>
    </body></html>
    """
    result = PruningContentFilter().filter_content(html)
    assert "img/1.jpg" in result
    assert "img/2.jpg" in result
    assert "4k壁纸" in result
