"""P6 深度爬取验收测试：BFS/DFS 本地站点 + 过滤器链 + 饱和度。"""

import asyncio

from crawagent.core.adaptive import Saturation, SaturationConfig
from crawagent.core.deep_crawl import DeepCrawler, DeepCrawlStrategy, CrawledPage
from crawagent.core.filters import (
    ExtensionFilter,
    FilterChain,
    SameDomainFilter,
    URLFilter,
)


def _await(coro):
    return asyncio.run(coro)


def _build_site(tmp_path):
    (tmp_path / "index.html").write_text(
        """<html><body><h1>首页</h1>
        <a href="/a.html">A</a>
        <a href="/b.html">B</a>
        <a href="https://example.com/ext">外链</a>
        <a href="/image.jpg">图片</a>
        </body></html>""",
        encoding="utf-8",
    )
    (tmp_path / "a.html").write_text('<html><body><a href="/c.html">C</a></body></html>', encoding="utf-8")
    (tmp_path / "b.html").write_text("<html><body>B 页面</body></html>", encoding="utf-8")
    (tmp_path / "c.html").write_text('<html><body><a href="/index.html">回首页</a></body></html>', encoding="utf-8")
    (tmp_path / "image.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")


def test_deep_crawl_bfs(local_server):
    base_url, tmp_path = local_server
    _build_site(tmp_path)
    crawler = DeepCrawler(strategy=DeepCrawlStrategy.BFS, max_depth=2, max_pages=10)
    result = _await(crawler.crawl(f"{base_url}/index.html"))
    pages: list[CrawledPage] = result.pages
    urls = {p.url for p in pages}
    assert len(pages) == 4, [(p.url, p.status_code, p.error) for p in pages]
    assert urls == {
        f"{base_url}/index.html",
        f"{base_url}/a.html",
        f"{base_url}/b.html",
        f"{base_url}/c.html",
    }
    assert all(p.status_code == 200 for p in pages)
    # BFS：首页在前
    assert pages[0].url.endswith("/index.html")
    assert result.stats.pages_crawled == 4
    assert result.stats.urls_filtered >= 2  # 外链 + 图片
    assert result.stats.max_depth_reached >= 2


def test_deep_crawl_dfs(local_server):
    base_url, tmp_path = local_server
    _build_site(tmp_path)
    crawler = DeepCrawler(strategy=DeepCrawlStrategy.DFS, max_depth=2, max_pages=10)
    result = _await(crawler.crawl(f"{base_url}/index.html"))
    assert result.stats.pages_crawled == 4
    assert result.stats.errors == 0


def test_filters_chain():
    chain = FilterChain([SameDomainFilter("https://example.com/a"), ExtensionFilter()])
    assert chain.accept("https://example.com/b.html")
    assert not chain.accept("https://evil.com/x.html")
    assert not chain.accept("https://example.com/photo.jpg")
    r = chain.accept_one("https://evil.com/x.html")
    assert r.accepted is False and r.rejected_by == "same_domain"


def test_url_filter_basic():
    f = URLFilter()
    assert f.accept("https://example.com/")


def test_saturation_logic():
    sat = Saturation()
    cfg = SaturationConfig(consecutive_off_topic=10, off_topic_ratio=0.6, off_topic_min_pages=20)
    for i in range(15):
        sat.record(_fake_page(f"https://x.com/{i}"), on_topic=False)
    assert sat.check_saturated(cfg) is True
    assert "已饱和" in sat.to_summary()

    sat2 = Saturation()
    for i in range(10):
        sat2.record(_fake_page(f"https://x.com/{i}"), on_topic=True)
    assert sat2.check_saturated(cfg) is False


def _fake_page(url: str):
    return CrawledPage(url=url, status_code=200, depth=0, title="t", out_links=[])
