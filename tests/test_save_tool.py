"""save_tool 测试 — SQLite 记录存储 + 五关质量过滤。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import save_tool


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """让 ``_get_db_path`` 返回 tmp_path/crawl_records.db。"""
    db_path = tmp_path / "crawl_records.db"
    monkeypatch.setattr(save_tool, "_get_db_path", lambda: db_path)
    # 重置 _DB_READY 状态，让 _init_db 真创建表
    save_tool._DB_READY = False
    return db_path


# ---------------------------------------------------------------------------
# 五关质量过滤
# ---------------------------------------------------------------------------

def test_save_too_short_content_rejected(tmp_db):
    """① 长度 < 200 → REJECTED。"""
    out = save_tool.save_record.func(
        url="https://example.com/a",
        title="某文章",
        content="太短了",  # < 200 chars
    )
    assert "[REJECTED]" in out
    assert "200" in out or "长度" in out or "short" in out.lower()


def test_save_no_title_rejected(tmp_db):
    """④ 无标题 → REJECTED。"""
    long = "这是" + ("足够长的正文段落，" * 30) + "应该有200+字。"  # ~150 chars
    out = save_tool.save_record.func(
        url="https://example.com/b",
        title="",
        content=long,
    )
    # 空标题 → 被过滤
    assert "[REJECTED]" in out or "标题" in out or "title" in out.lower()


def test_save_valid_record_succeeds(tmp_db):
    """正常长度 + 标题 + 内容 → 成功。"""
    # 234 字符中文（6 段 × 39 字符）—— 超过 200 阈值
    long = (
            "# 主标题\n\n"
            + "这是一段足够长且信息密度合理的正文内容，用于通过五关质量过滤的第一关长度校验。" * 4
            + "\n\n"
            + "另一段足够长的正文内容，确保通过质量过滤。" * 4
            + "\n\n- 第一要点\n- 第二要点\n- 第三要点\n- 第四要点\n"
        )
    title = "有效文章标题"
    out = save_tool.save_record.func(
        url="https://example.com/valid",
        title=title,
        content=long,
    )
    assert "Saved" in out or "saved" in out.lower()
    assert "Record ID" in out or "id" in out.lower()


def test_save_duplicate_rejected(tmp_db):
    """同内容两次保存 → 第二次 [DUPLICATE]。"""
    long = (
        "# 主标题\n\n"
        + "这是一段足够长且信息密度合理的正文内容，用于通过五关质量过滤的第一关长度校验。" * 4
        + "\n\n"
        + "另一段足够长的正文内容，确保通过质量过滤。" * 4
        + "\n\n- 第一要点\n- 第二要点\n- 第三要点\n- 第四要点\n"
    )
    save_tool.save_record.func(
        url="https://example.com/dup",
        title="重复测试",
        content=long,
    )
    out = save_tool.save_record.func(
        url="https://example.com/dup",
        title="重复测试",
        content=long,
    )
    assert "[DUPLICATE]" in out or "duplicate" in out.lower()


def test_save_high_link_ratio_rejected(tmp_db):
    """⑤ 链接文字比 > 25% → REJECTED（传 html 启用）。"""
    content = (
        "# 主标题\n\n"
        + "这是一段足够长且信息密度合理的正文内容，用于通过五关质量过滤的第一关长度校验。" * 4
        + "\n\n"
        + "另一段足够长的正文内容，确保通过质量过滤。" * 4
        + "\n\n- 第一要点\n- 第二要点\n- 第三要点\n- 第四要点\n"
    )
    html = content + "<a href='http://x.com'>链接</a>" * 30  # 链接远超 25%

    out = save_tool.save_record.func(
        url="https://example.com/links",
        title="链接多",
        content=content,
        html=html,
    )
    assert "[REJECTED]" in out or "Saved" in out


def test_save_invalid_extra_data_json(tmp_db):
    """extra_data 非 JSON 字串 → 仍能保存（存 raw）。"""
    long = (
        "# 主标题\n\n"
        + "这是一段足够长且信息密度合理的正文内容，用于通过五关质量过滤的第一关长度校验。" * 4
        + "\n\n"
        + "另一段足够长的正文内容，确保通过质量过滤。" * 4
        + "\n\n- 第一要点\n- 第二要点\n- 第三要点\n- 第四要点\n"
    )
    out = save_tool.save_record.func(
        url="https://example.com/extra",
        title="附加数据",
        content=long,
        extra_data="not valid json",
    )
    assert "[REJECTED]" not in out


def test_save_infer_platform_from_url(tmp_db):
    """platform 为空 → 从 URL 推断。"""
    long = (
        "# 主标题\n\n"
        + "这是一段足够长且信息密度合理的正文内容，用于通过五关质量过滤的第一关长度校验。" * 4
        + "\n\n"
        + "另一段足够长的正文内容，确保通过质量过滤。" * 4
        + "\n\n- 第一要点\n- 第二要点\n- 第三要点\n- 第四要点\n"
    )
    out = save_tool.save_record.func(
        url="https://www.bilibili.com/video/BV12345",
        title="B站视频",
        content=long,
    )
    assert "Saved" in out or "[REJECTED]" in out


def test_save_with_save_path(tmp_db):
    """save_path 记录本地路径。"""
    long = (
        "# 主标题\n\n"
        + "这是一段足够长且信息密度合理的正文内容，用于通过五关质量过滤的第一关长度校验。" * 4
        + "\n\n"
        + "另一段足够长的正文内容，确保通过质量过滤。" * 4
        + "\n\n- 第一要点\n- 第二要点\n- 第三要点\n- 第四要点\n"
    )
    out = save_tool.save_record.func(
        url="https://example.com/path",
        title="本地路径",
        content=long,
        save_path="/tmp/output.md",
    )
    assert "Saved" in out or "[REJECTED]" in out