"""advanced_tools 回归测试（变更 009）。

markitdown_convert：用 reportlab 生成真实 PDF 测转换。
crawl4ai_deep_crawl / browser_use_navigate：测错误路径（坏参数/缺文件），
真实抓取/浏览器交互靠手动冒烟（需 Playwright 浏览器 + 网络 + LLM，不进单测）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools.advanced_tools import (
    markitdown_convert, crawl4ai_deep_crawl, browser_use_navigate
)


# ── markitdown_convert ──

def test_markitdown_missing_file():
    res = markitdown_convert.func("/nonexistent/xyz.pdf")
    assert "[ERROR]" in res and "不存在" in res


def test_markitdown_converts_real_pdf(tmp_path):
    """reportlab 生成真实 PDF → markitdown 转出文本。"""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
    except ImportError:
        pytest.skip("reportlab 未装，跳过 PDF 转换测试")
    pdf = tmp_path / "t.pdf"
    c = canvas.Canvas(str(pdf), pagesize=A4)
    c.drawString(100, 750, "Hello markitdown PDF conversion test")
    c.drawString(100, 730, "CrawAgent advanced tools smoke")
    c.save()
    res = markitdown_convert.func(str(pdf))
    assert "[ERROR]" not in res, f"转换失败: {res[:200]}"
    # 抽出的文本应包含写入的字样（至少一个）
    assert "markitdown" in res or "advanced tools" in res or "smoke" in res


def test_markitdown_truncates_huge(tmp_path):
    """超大输入截断到 30000 字符内。"""
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab 未装")
    pdf = tmp_path / "big.pdf"
    c = canvas.Canvas(str(pdf))
    # 写大量文本行（每页几十行，多页）—— 简单起见写一个长 drawString 串
    c.drawString(72, 750, "X" * 50000)
    c.save()
    res = markitdown_convert.func(str(pdf))
    assert "truncated" in res or len(res) <= 30100  # 截断或本就短


# ── crawl4ai_deep_crawl 错误路径 ──

def test_crawl4ai_bad_url():
    """坏 URL 不应让进程崩（crawl4ai 内部抛错被工具兜成 [ERROR]）。"""
    res = crawl4ai_deep_crawl.func("not-a-url", max_pages=2)
    # 可能是 [ERROR] 抓取失败，或 crawl4ai 容错返回 0 页
    assert isinstance(res, str)
    assert ("[ERROR]" in res) or ("0 页" in res) or ("Crawled" in res)


# ── lang 语言变体去重（_lang_deny_patterns，fnmatch 验证 glob 行为，不起浏览器） ──

from fnmatch import fnmatch  # crawl4ai URLPatternFilter(use_glob=True) 内部用 fnmatch


def _is_denied(url: str, patterns: list[str]) -> bool:
    """url 命中任一 deny 模式 → 被 URLPatternFilter(reverse=True) 丢弃。"""
    return any(fnmatch(url, p) for p in patterns)


def test_lang_zh_keeps_zh_and_neutral_drops_others():
    """lang=zh：/zh/ 与变体 + 语言中性 URL 保留；/de/ /es/ /fr/ /en/ 丢弃。"""
    from crawagent.tools.advanced_tools import _lang_deny_patterns

    patterns = _lang_deny_patterns("zh")
    # 保留
    assert not _is_denied("https://x.com/zh/docs/intro", patterns)
    assert not _is_denied("https://x.com/zh-cn/docs", patterns)
    assert not _is_denied("https://x.com/zh-tw/docs", patterns)
    assert not _is_denied("https://x.com/api/endpoint", patterns)  # 语言中性
    assert not _is_denied("https://x.com/docs/intro", patterns)     # 语言中性
    # 丢弃
    assert _is_denied("https://x.com/de/docs/intro", patterns)
    assert _is_denied("https://x.com/es/docs", patterns)
    assert _is_denied("https://x.com/fr/guide", patterns)
    assert _is_denied("https://x.com/en/docs", patterns)
    assert _is_denied("https://x.com/ja-jp/docs", patterns)


def test_lang_en_keeps_en_drops_zh():
    """lang=en：/en/ 及变体保留，/zh/ /de/ 丢弃。"""
    from crawagent.tools.advanced_tools import _lang_deny_patterns

    patterns = _lang_deny_patterns("en")
    assert not _is_denied("https://x.com/en/docs", patterns)
    assert not _is_denied("https://x.com/en-us/docs", patterns)
    assert _is_denied("https://x.com/zh/docs", patterns)
    assert _is_denied("https://x.com/de/docs", patterns)


def test_lang_empty_no_filter():
    """lang 不设 → 无 deny 模式（不过滤）。"""
    from crawagent.tools.advanced_tools import _lang_deny_patterns
    assert _lang_deny_patterns("") == []
    assert _lang_deny_patterns(None) == []


# ── browser_use_navigate 错误路径 ──

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("browser_use") is None,
    reason="browser_use not installed",
)
def test_browser_use_agent_failure_returns_error(monkeypatch):
    """Agent.run 抛错时工具兜成 [ERROR]，不裸异常杀任务。不真起浏览器（mock 掉 Agent）。"""
    import asyncio

    class _FailAgent:
        def __init__(self, *a, **kw):
            pass

        async def run(self, *a, **kw):
            raise RuntimeError("simulated browser/LLM failure")

    import browser_use
    monkeypatch.setattr(browser_use, "Agent", _FailAgent)
    monkeypatch.setattr(browser_use, "Browser", lambda *a, **kw: object())  # 不真建 Browser
    res = browser_use_navigate.func("https://example.com", "测试")
    assert "[ERROR]" in res
    assert "浏览器交互失败" in res
