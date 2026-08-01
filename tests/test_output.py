"""P3 文件整理与保存测试。"""

import os
import re

import pytest

from crawagent.output.organizer import (
    FileOrganizer,
    extract_domain,
    organize_content,
    sanitize_filename,
    title_to_slug,
    unique_path,
)
from crawagent.output.media_downloader import MediaDownloader


def test_sanitize_filename_illegal_chars():
    """验收：标题含 /\\:*?\"<>| 非法字符，路径正确转义。"""
    title = 'A/B\\C:D*E?F"G<H>I|J'
    safe = sanitize_filename(title)
    assert re.search(r'[\\/:*?"<>|]', safe) is None
    assert safe == "A_B_C_D_E_F_G_H_I_J"


def test_sanitize_filename_edge_cases():
    assert sanitize_filename("") == "untitled"
    assert sanitize_filename("///***???") == "untitled"
    assert sanitize_filename("  标题  ") == "标题"
    assert sanitize_filename("end.") == "end"
    assert sanitize_filename("a" * 500) == "a" * 120


def test_title_to_slug():
    assert title_to_slug("Hello World: 测试") == "hello-world-测试"
    assert title_to_slug("中文标题") == "中文标题"
    assert title_to_slug("  ") == "untitled"


def test_extract_domain():
    assert extract_domain("https://www.ruanyifeng.com/blog/1.html") == "ruanyifeng.com"
    assert extract_domain("https://example.com:8080/x") == "example.com:8080"
    assert extract_domain("") == "unknown"


def test_render_template_with_illegal_title(tmp_path):
    org = FileOrganizer(base_dir=str(tmp_path))
    path = org.render(
        "articles/{domain}/{date}/{title}.{ext}",
        url="https://www.ruanyifeng.com/blog/2024/01/foo.html",
        title='阮一峰: "Hello/World" 2024*特辑',
        ext="md",
    )
    rel = os.path.relpath(path, tmp_path)
    parts = rel.split(os.sep)
    assert parts[0] == "articles"
    assert parts[1] == "ruanyifeng.com"
    assert re.match(r"\d{4}-\d{2}-\d{2}", parts[2])
    # 文件名不含非法字符
    filename = parts[-1]
    assert re.search(r'[\\/:*?"<>|]', filename) is None
    assert filename.endswith(".md")
    assert "Hello_World" in filename


def test_render_tilde_template_not_mangled(tmp_path):
    """回归：~/ 开头的模板不得被拼到 base_dir 下。"""
    org = FileOrganizer(base_dir=str(tmp_path))
    path = org.render(
        "~/crawagent_test/{domain}/{title}.md",
        url="https://example.com/a",
        title="标题",
    )
    assert str(tmp_path) not in path
    assert path.startswith(os.path.expanduser("~"))


def test_organize_content_writes_file(tmp_path):
    path = organize_content(
        "# 测试内容",
        url="https://example.com/post/1",
        title="测试/文章",
        base_dir=str(tmp_path),
    )
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        assert f.read() == "# 测试内容"


def test_unique_path_avoids_overwrite(tmp_path):
    p1 = str(tmp_path / "同名文章.md")
    with open(p1, "w", encoding="utf-8") as f:
        f.write("1")
    p2 = unique_path(p1)
    assert p2 == str(tmp_path / "同名文章-2.md")
    with open(p2, "w", encoding="utf-8") as f:
        f.write("2")
    assert unique_path(p1) == str(tmp_path / "同名文章-3.md")


def test_media_downloader_error_path():
    dl = MediaDownloader(base_dir="./output/media")
    result = asyncio_get(dl.download("", title="无URL"))
    assert result["success"] is False
    assert "missing url" in result["error"]


def test_media_downloader_video_site_detection():
    dl = MediaDownloader()
    assert dl._is_video_site("https://www.youtube.com/watch?v=x")
    assert dl._is_video_site("https://www.bilibili.com/video/BV1")
    assert not dl._is_video_site("https://example.com/file.pdf")


def asyncio_get(coro):
    import asyncio
    return asyncio.run(coro)


def test_save_executor_template_path(tmp_path, monkeypatch):
    """save 工具：path 含 {domain}/{title} 占位符时正确渲染并写入。"""
    from crawagent.harness.tools import save_executor

    # 让模板渲染落到 tmp_path 下（save_executor 内部 base_dir 固定 ./output，
    # 这里用 {domain}/{title} 相对模板 + 手动清理）
    result = asyncio_get(save_executor({
        "content": "测试正文",
        "path": "{domain}/{title}.{ext}",
        "url": "https://example.com/a",
        "title": "非法/标题?测试",
        "format": "markdown",
    }))
    assert result.get("status_code") == 200, result
    saved_path = result["path"]
    try:
        assert os.path.isfile(saved_path)
        assert "example.com" in saved_path
        assert re.search(r'[\\/:*?"<>|]', os.path.basename(saved_path)) is None
    finally:
        if os.path.isfile(saved_path):
            os.remove(saved_path)
            dir_part = os.path.dirname(saved_path)
            if dir_part and os.path.isdir(dir_part):
                try:
                    os.rmdir(dir_part)
                except OSError:
                    pass


def test_supervisor_save_flow(tmp_path, monkeypatch):
    """supervisor 全流程（stub 抓取/分析/提取）：保存阶段不崩且按模板落盘。"""
    import crawagent.harness.tools as T

    html = "<html><head><title>测试文章</title></head><body><article><p>正文</p></article></body></html>"

    async def fake_crawl(args):
        return {"content": html, "html": html, "status": 200, "status_code": T.STATUS_OK,
                "url": "https://www.ruanyifeng.com/blog/2024/01/a.html",
                "title": "测试文章", "strategy": "httpx", "links_count": 0, "page_signature": "sig"}

    async def fake_analyze(args):
        return {"status_code": T.STATUS_OK, "blueprint": None, "content": "{}"}

    async def fake_extract(args):
        return {"status_code": T.STATUS_OK, "content": "[]", "confidence": 80, "count": 0,
                "method": "css", "items": []}

    monkeypatch.setattr(T, "crawl_executor", fake_crawl)
    monkeypatch.setattr(T, "analyze_executor", fake_analyze)
    monkeypatch.setattr(T, "extract_executor", fake_extract)

    sup = T.CrawlSupervisor(output_dir=str(tmp_path))
    result = asyncio_get(sup.run("https://www.ruanyifeng.com/blog/2024/01/a.html"))
    assert result.get("status_code") == T.STATUS_OK, result
    assert result.get("output_path", "").endswith(".md")
    assert os.path.isfile(result["output_path"])
    with open(result["output_path"], encoding="utf-8") as f:
        content = f.read()
    assert "正文" in content or "来源" in content
    assert "ruanyifeng.com" in result["output_path"]
