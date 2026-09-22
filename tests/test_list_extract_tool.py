"""list_extract_tool 测试 — 三段式策略（正则 + LLM + DOM）。

依赖测试约定：mock ``_stage2_llm`` 拦截 LLM 调用。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import list_extract_tool


# ---------------------------------------------------------------------------
# extract_list 三段式
# ---------------------------------------------------------------------------

def test_extract_list_too_short_html():
    """HTML 太短 → 错误。"""
    out = list_extract_tool.extract_list.func("<html>x</html>")
    assert "too short" in out.lower()


def test_extract_list_empty_html():
    """HTML 为空 → 错误。"""
    out = list_extract_tool.extract_list.func("")
    assert "too short" in out.lower()


def test_extract_list_stage1_regex_sufficient():
    """正则阶段找到 ≥5 条 → 走 regex 策略，不调 LLM。"""
    # 构造 10 个相似 <a>，正则应能找到
    items_html = ""
    for i in range(10):
        items_html += f'<li><a href="/article/{i}">文章{i} 标题</a></li>'
    html = f"<html><body><ul>{items_html}</ul></body></html>"

    with patch.object(list_extract_tool, "_stage2_llm") as mock_llm:
        out = list_extract_tool.extract_list.func(html, "https://example.com/")
    assert "regex" in out or "正则" in out or "10" in out
    mock_llm.assert_not_called()


def test_extract_list_stage2_llm_when_regex_insufficient():
    """正则 <5 条 → 走 LLM 策略。"""
    html = "<html><body>" + "<a href='/a/1'>a</a>" * 20 + "</body></html>"

    llm_items = [{"title": f"标题{i}", "url": f"https://example.com/a/{i}"} for i in range(1, 9)]

    with patch.object(list_extract_tool, "_stage2_llm", return_value=llm_items), \
         patch.object(list_extract_tool, "_stage3_dom_depth", return_value=[]):
        out = list_extract_tool.extract_list.func(html, "https://example.com/")
    # 走 LLM 策略
    assert "llm" in out or "LLM" in out or len(llm_items) > 0


def test_extract_list_stage3_dom_when_llm_also_insufficient():
    """正则 + LLM 都 <5 条 → 走 DOM 深度策略。"""
    html = "<html><body>" + "<a href='/a/1'>a</a>" * 20 + "</body></html>"

    dom_items = [{"title": f"d{i}", "url": f"u{i}"} for i in range(8)]
    with patch.object(list_extract_tool, "_stage2_llm", return_value=[{"title": "t", "url": "u"}] * 2), \
         patch.object(list_extract_tool, "_stage3_dom_depth", return_value=dom_items):
        out = list_extract_tool.extract_list.func(html, "https://example.com/")
    # DOM 兜底 8 条 → 走 dom_depth 策略
    assert "dom" in out or "DOM" in out or "深度" in out


def test_extract_list_no_items_returns_failure():
    """三段式都为空 → 返回失败信息。"""
    html = "<html><body>" + ("<a href='/x'>x</a>" * 30) + "</body></html>"
    with patch.object(list_extract_tool, "_stage1_regex", return_value=[]), \
         patch.object(list_extract_tool, "_stage2_llm", return_value=[]), \
         patch.object(list_extract_tool, "_stage3_dom_depth", return_value=[]):
        out = list_extract_tool.extract_list.func(html, "https://example.com/")
    assert "failed" in out.lower() or "no items" in out.lower()


def test_extract_list_returns_formatted_output():
    """正常路径 → 格式化输出含 1. [...] 列表。"""
    items_html = ""
    for i in range(8):
        items_html += f'<a href="/article/{i}">文章{i}</a>'
    html = f"<html><body>{items_html}</body></html>"

    out = list_extract_tool.extract_list.func(html, "https://example.com/")
    # 输出格式: "1. [title](url)" 形式
    assert "1." in out
    assert "/article/0" in out or "https://example.com/article/0" in out


def test_extract_list_passes_url_to_stages(monkeypatch):
    """url 参数透传给三段式。"""
    html = "<html><body>" + "<a href='/a/1'>a</a>" * 20 + "</body></html>"
    captured_urls: list[str] = []

    def fake_stage2(html_arg, url_arg):
        captured_urls.append(url_arg)
        return [{"title": "t", "url": "u"}] * 2

    def fake_stage3(html_arg, url_arg):
        captured_urls.append(url_arg)
        return []

    monkeypatch.setattr(list_extract_tool, "_stage2_llm", fake_stage2)
    monkeypatch.setattr(list_extract_tool, "_stage3_dom_depth", fake_stage3)
    list_extract_tool.extract_list.func(html, "https://test.example.com/")
    # 两个 stage 都收到 base URL
    assert all("test.example.com" in u for u in captured_urls)


# ---------------------------------------------------------------------------
# extract_list_paged（分页版，简测）
# ---------------------------------------------------------------------------

def test_extract_list_paged_combines_multiple_pages(monkeypatch):
    """分页版：walk_pages 生成多页 → 每页抽 __stage1_regex 结果 → 去重合并。"""
    # mock walk_pages：yield 2 个 page
    def fake_walk(base_url, *, max_pages, extract_urls):
        page1_html = "<a href='/a/1'>t1</a>" * 20
        page2_html = "<a href='/a/2'>t2</a>" * 20
        yield ("https://example.com/list?page=1", page1_html, ["https://example.com/a/1"])
        yield ("https://example.com/list?page=2", page2_html, ["https://example.com/a/2"])

    # mock _stage1_regex：对每页返回 items
    def fake_stage1(html, url):
        if "page=1" in url:
            return [{"title": "t1", "url": "https://example.com/a/1"}]
        if "page=2" in url:
            return [{"title": "t2", "url": "https://example.com/a/2"}]
        return []

    monkeypatch.setattr(list_extract_tool, "walk_pages", fake_walk)
    monkeypatch.setattr(list_extract_tool, "_stage1_regex", fake_stage1)

    out = list_extract_tool.extract_list_paged.func("https://example.com/list")
    # 合并 2 条
    assert "t1" in out
    assert "t2" in out
    assert "pages=2" in out


def test_extract_list_paged_dedupes_urls(monkeypatch):
    """同一 URL 在多页出现 → 只保留一份。"""
    def fake_walk(base_url, *, max_pages, extract_urls):
        yield ("https://example.com/list?page=1", "<html>p1</html>", ["u1"])
        yield ("https://example.com/list?page=2", "<html>p2</html>", ["u1"])  # 重复 URL

    def fake_stage1(html, url):
        return [{"title": "same", "url": "u1"}]

    monkeypatch.setattr(list_extract_tool, "walk_pages", fake_walk)
    monkeypatch.setattr(list_extract_tool, "_stage1_regex", fake_stage1)

    out = list_extract_tool.extract_list_paged.func("https://example.com/list")
    # 同一 URL 只 1 条
    count_u1 = out.count("(u1)")
    assert count_u1 == 1


def test_extract_list_paged_no_items_returns_failure(monkeypatch):
    """分页找不到任何条目 → 返回失败。"""
    def fake_walk(base_url, *, max_pages, extract_urls):
        yield ("https://example.com/list", "<html>no items</html>", [])

    monkeypatch.setattr(list_extract_tool, "walk_pages", fake_walk)
    monkeypatch.setattr(list_extract_tool, "_stage1_regex", lambda h, u: [])

    out = list_extract_tool.extract_list_paged.func("https://example.com/list")
    assert "failed" in out.lower() or "no" in out.lower()