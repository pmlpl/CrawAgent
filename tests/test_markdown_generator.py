"""P2-2 Markdown 生成器测试。"""

from crawagent.core.markdown_generator import MarkdownGenerator, html_to_clean_markdown


def test_basic_conversion_and_citation():
    html = """
    <html><body>
      <h1>标题</h1>
      <p>第一段正文，<a href="https://example.com/x">链接</a>。</p>
    </body></html>
    """
    md = MarkdownGenerator().generate(html, url="https://example.com/x", title="标题")
    assert "标题" in md
    assert "第一段正文" in md
    assert "来源" in md
    assert "https://example.com/x" in md


def test_no_citation_when_style_none():
    md = MarkdownGenerator(cite_style=None).generate(
        "<html><body><p>正文</p></body></html>", url="https://example.com"
    )
    assert "来源" not in md


def test_inline_citation():
    md = MarkdownGenerator(cite_style="inline").generate(
        "<html><body><p>正文</p></body></html>", url="https://example.com", title="示例"
    )
    assert "（来源：" in md


def test_fit_markdown_truncates_at_paragraph():
    md = "\n\n".join(f"第{i}段正文内容" for i in range(20))
    fitted = MarkdownGenerator(max_length=60).fit_markdown(md)
    assert len(fitted) <= 60 + 30  # 尾部截断提示占少量空间
    assert "截断" in fitted


def test_empty_html():
    assert html_to_clean_markdown("") == ""
    assert html_to_clean_markdown("<html><body></body></html>", url="https://a.com") != ""


def test_dirty_html_pure_script():
    md = html_to_clean_markdown("<html><body><script>bad();</script></body></html>", url="https://a.com")
    assert "bad" not in md
