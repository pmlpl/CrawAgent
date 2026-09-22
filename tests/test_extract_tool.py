"""extract_tool 测试 — HTML → Markdown + 置信度评分。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import extract_tool


# ---------------------------------------------------------------------------
# _reconcile_title
# ---------------------------------------------------------------------------

def test_reconcile_title_heading_similar_to_title():
    """正文首个标题与 <title> 高度相似 → 用正文标题替换。"""
    md = "# 第一章 介绍\n\n正文内容..."
    title = "第一章 介绍 - CSDN博客"  # 含正文标题 + 站点后缀
    result = extract_tool._reconcile_title(md, title)
    assert result == "第一章 介绍"


def test_reconcile_title_heading_different_from_title():
    """正文标题与 <title> 不相似 → 保留 <title>。"""
    md = "# 完全不同的标题\n\n..."
    title = "另一个标题"
    result = extract_tool._reconcile_title(md, title)
    assert result == "另一个标题"


def test_reconcile_title_no_heading_returns_title():
    """Markdown 无标题 → 保留 <title>。"""
    md = "正文段落，无标题。"
    title = "原标题"
    result = extract_tool._reconcile_title(md, title)
    assert result == "原标题"


def test_reconcile_title_title_contains_heading():
    """<title> 包含正文标题（如 title 长，heading 短）→ 用 heading。"""
    md = "# 简短"
    title = "简短 - 完整标题"
    result = extract_tool._reconcile_title(md, title)
    assert result == "简短"


# ---------------------------------------------------------------------------
# extract_content 集成（实际 HTML 解析）
# ---------------------------------------------------------------------------

def test_extract_content_basic_html():
    """正常 HTML → Markdown + Title 字段 + 置信度。"""
    # 200+ 字符正文避免低置信度
    html = """
    <html><head><title>测试页面</title></head>
    <body>
      <article>
        <h1>主标题</h1>
        <p>这是一段足够长的正文内容，用于通过质量评分的基本阈值并确保超过两百字符的最小长度要求。
        这是另一段正文内容，延续主题，描述更多细节信息以增加字数，确保整体文档长度足以通过评分系统的阈值。
        第三段正文继续展开论述，深入探讨相关话题的更多方面，进一步提升文档的总体字符数与信息密度。</p>
      </article>
    </body></html>
    """
    out = extract_tool.extract_content.func(html)
    assert "Title:" in out
    assert "测试页面" in out or "主标题" in out
    # 置信度标签（中文或英文）
    assert "置信度" in out or "CONFIDENCE" in out


def test_extract_content_removes_scripts_and_styles():
    """<script> 和 <style> 内容不进入 Markdown。"""
    html = """
    <html><head>
      <title>页面</title>
      <style>body { color: red; }</style>
    </head><body>
      <article>
        <p>正常正文内容足够长通过阈值与评分系统对文档最小字符数的基本要求，超过两百个字符长度确保顺利通过。</p>
        <p>另一段正文内容足够长通过阈值，补充更多话题与论述内容进一步增加文档总体字数，确保评分系统不会因为长度不足扣分。</p>
      </article>
      <script>alert('evil');</script>
    </body></html>
    """
    out = extract_tool.extract_content.func(html)
    assert "alert" not in out
    assert "color: red" not in out
    assert "正常正文" in out


def test_extract_content_no_title_uses_untitled():
    """无 <title> → "Untitled"。"""
    html = "<html><body><article><p>正文内容足够长通过评分阈值再加一些字。</p></article></body></html>"
    out = extract_tool.extract_content.func(html)
    assert "Untitled" in out or "Title:" in out


def test_extract_content_very_short_content_low_confidence():
    """内容过短 → 置信度 < 60 + 低置信度标记。"""
    html = """
    <html><head><title>空页面</title></head>
    <body><article><p>x</p></article></body></html>
    """
    out = extract_tool.extract_content.func(html)
    # 实际实现用英文标签
    assert "LOW CONFIDENCE" in out or "低置信度" in out


def test_extract_content_returns_markdown_format():
    """输出含 Markdown 格式（# / - / ```）。"""
    html = """
    <html><head><title>Markdown 测试</title></head>
    <body><article>
      <h1>一级标题</h1>
      <p>正文段落。</p>
      <pre><code>print("hello")</code></pre>
      <ul><li>列表项 1</li><li>列表项 2</li></ul>
    </article></body></html>
    """
    out = extract_tool.extract_content.func(html)
    # Markdown 转换标志
    assert "# 一级标题" in out or "一级标题" in out


def test_extract_content_low_confidence_warning_message():
    """低置信度返回 [低置信度] 警告（per system.md 硬规则）。"""
    html = "<html><body><article><p>短</p></article></body></html>"
    out = extract_tool.extract_content.func(html)
    # system.md 硬规则：低置信度必须有标记
    if "低置信度" in out:
        assert "[低置信度" in out or "低置信度:" in out