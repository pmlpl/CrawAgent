"""翻页共享模块 + extract_list_paged 的纯逻辑测试（fetch 注入，无网络）。
覆盖 ADR-0002 的 v1 策略：下一页链接追踪、URL 参数猜测（含参数粘性）、终止条件。
"""
import pytest

from crawagent.tools import pagination
from crawagent.tools.pagination import _find_next_link, _with_param, walk_pages


def _page(items: list[str], next_html: str = "") -> str:
    """构造静态列表页：items 为 <a> HTML 片段列表，next_html 为下一页链接片段。"""
    return "<html><body>" + "".join(items) + next_html + "</body></html>"


def _items(start: int, count: int = 5) -> list[str]:
    return [f'<a href="/novel/{i}.html">Chapter {i}</a>' for i in range(start, start + count)]


def _fetch_factory(pages: dict[str, str]):
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url not in pages:
            raise KeyError(url)
        return pages[url]

    fetch.calls = calls
    return fetch


def _extract_a_urls(html: str, base_url: str) -> list[str]:
    import re
    return [m.group(1) for m in re.finditer(r'href="(/novel/\d+\.html)"', html)]


def _walk(url: str, pages: dict[str, str], **kw):
    fetch = _fetch_factory(pages)
    got = [(u, fresh) for u, _h, fresh in walk_pages(url, extract_urls=_extract_a_urls, fetch=fetch, **kw)]
    return got, fetch.calls


def test_walk_pages_param_guessing():
    pages = {
        "https://x/list": _page(_items(1)),
        "https://x/list?page=2": _page(_items(6)),
    }
    got, calls = _walk("https://x/list", pages, max_pages=5)
    assert [u for u, _ in got] == ["https://x/list", "https://x/list?page=2"]
    assert sum(len(f) for _, f in got) == 10


def test_walk_pages_next_link():
    # 第 1 页用相对链接，第 2/3 页用根相对链接（urljoin 语义：相对 base 目录解析）
    pages = {
        "https://x/posts/": _page(_items(1), '<a rel="next" href="page/2/">下一页</a>'),
        "https://x/posts/page/2/": _page(_items(6), '<a rel="next" href="/posts/page/3/">下一页</a>'),
        # 第 3 页回链第 2 页 → visited 拦住，翻页终止
        "https://x/posts/page/3/": _page(_items(11), '<a rel="next" href="/posts/page/2/">下一页</a>'),
    }
    got, calls = _walk("https://x/posts/", pages, max_pages=10)
    assert [u for u, _ in got] == ["https://x/posts/", "https://x/posts/page/2/", "https://x/posts/page/3/"]
    assert sum(len(f) for _, f in got) == 15


def test_walk_pages_stops_when_no_new_items():
    same = _items(1)
    pages = {
        "https://x/list": _page(same),
        "https://x/list?page=2": _page(same),  # 第 2 页条目与第 1 页完全相同
    }
    got, calls = _walk("https://x/list", pages, max_pages=10)
    # 第 2 页被参数探测过，但无新条目 → 不采纳、不产出，翻页在第 1 页后终止
    assert len(got) == 1
    assert len(got[0][1]) == 5
    assert "https://x/list?page=2" in calls


def test_walk_pages_respects_max_pages():
    pages = {f"https://x/list?page={n}": _page(_items(n * 5 + 1)) for n in range(2, 6)}
    pages["https://x/list"] = _page(_items(1))
    got, _ = _walk("https://x/list", pages, max_pages=3)
    assert len(got) == 3


def test_walk_pages_param_sticky():
    pages = {
        "https://x/list": _page(_items(1)),
        "https://x/list?page=2": _page(_items(6)),
        "https://x/list?page=3": _page(_items(11)),
    }
    got, calls = _walk("https://x/list", pages, max_pages=10)
    # 命中 page 参数后第 3 页只试 page，不再逐个试 p / pageNum
    assert all("p=3" not in c and "pageNum" not in c for c in calls)
    assert [u for u, _ in got][-1] == "https://x/list?page=3"


def test_with_param_preserves_query_and_replaces_page():
    assert _with_param("https://x/list?a=1", "page", 2) == "https://x/list?a=1&page=2"
    assert _with_param("https://x/list?page=9&a=1", "page", 2) == "https://x/list?a=1&page=2"


def test_find_next_link_variants():
    base = "https://x/list"
    assert _find_next_link('<a rel="next" href="/p2">n</a>', base) == "https://x/p2"
    assert _find_next_link('<a class="page-next" href="/p2">›</a>', base) == "https://x/p2"
    assert _find_next_link('<a href="/p2">下一页</a>', base) == "https://x/p2"
    assert _find_next_link('<a href="/p2">Next »</a>', base) == "https://x/p2"
    assert _find_next_link('<a href="/p2">第 2 页</a>', base) == ""
    assert _find_next_link('<a href="/p2">nextmonth</a>', base) == ""


def test_extract_list_paged_merges_pages(monkeypatch):
    pages = {
        "https://x/novel/": _page(_items(1), '<a class="page-next" href="?page=2">下一页</a>'),
        "https://x/novel/?page=2": _page(_items(7)),
    }
    monkeypatch.setattr(pagination, "http_get", lambda u, timeout=20: pages[u])
    from crawagent.tools.list_extract_tool import extract_list_paged

    out = extract_list_paged.invoke({"url": "https://x/novel/"})
    assert "pages=2" in out
    assert "Chapter 1" in out and "Chapter 11" in out  # 每页 5 条，两页合并 10 条


def test_extract_list_paged_respects_limit(monkeypatch):
    pages = {"https://x/novel/": _page(_items(1, 8))}
    monkeypatch.setattr(pagination, "http_get", lambda u, timeout=20: pages[u])
    from crawagent.tools.list_extract_tool import extract_list_paged

    out = extract_list_paged.invoke({"url": "https://x/novel/", "limit": 6})
    assert "pages=1" in out
    assert "Chapter 7" not in out


def test_extract_list_paged_no_items_hints(monkeypatch):
    monkeypatch.setattr(pagination, "http_get", lambda u, timeout=20: "<html><body>hello world</body></html>")
    from crawagent.tools.list_extract_tool import extract_list_paged

    out = extract_list_paged.invoke({"url": "https://x/"})
    assert "List extraction failed" in out
    assert "run_custom_script" in out
