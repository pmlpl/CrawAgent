"""search_knowledge + FTS5 检索层回归测试（变更 008）。

覆盖：FTS 迁移幂等 + 触发器同步 + 存量回填、FTS 命中 + snippet、
LIKE 兜底短词、scope 过滤、miss 引导。
用 tmp_path 隔离 DB，不碰真实 sessions.db。
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    """隔离 DB：save_tool/query_tool 都指向 tmp_path/test.db。"""
    db = tmp_path / "test.db"
    import crawagent.tools.save_tool as st
    import crawagent.tools.query_tool as qt
    monkeypatch.setattr(st, "_get_db_path", lambda: str(db))
    monkeypatch.setattr(qt, "_get_db_path", lambda: str(db))
    st._init_db()  # 建主表 + FTS 虚表 + 触发器
    # 直接 SQL 插 3 条（绕过 save_record 的 session 注入，可控）
    con = sqlite3.connect(str(db))
    con.executemany(
        "INSERT INTO crawl_records(url,title,content,created_at,platform,session_id) VALUES(?,?,?,?,?,?)",
        [
            ("http://a/first", "第一步", "FastAPI 第一步教程 安装与启动", "2026-01-01T10:00", "FastAPI", "s1"),
            ("http://b/path", "路径参数", "FastAPI 路径参数用法详解 路径参数的传入", "2026-01-02T10:00", "FastAPI", "s1"),
            ("http://c/other", "无关", "随便别的主题 content here", "2026-01-03T10:00", "", "s2"),
        ],
    )
    con.commit()
    con.close()
    st.set_current_session("s1")
    return qt


def test_fts_index_built_and_backfilled(tmp_path, monkeypatch):
    """_init_db 建 FTS 虚表 + 触发器；存量行（建表前已插入）回填进 FTS。"""
    db = tmp_path / "t2.db"
    import crawagent.tools.save_tool as st
    monkeypatch.setattr(st, "_get_db_path", lambda: str(db))
    # 先建主表插存量（无 FTS）
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE crawl_records (id INTEGER PRIMARY KEY, title TEXT, content TEXT, url TEXT, created_at TEXT, platform TEXT, save_path TEXT, session_id TEXT)")
    con.execute("INSERT INTO crawl_records(title,content,url,created_at,session_id) VALUES('存量','FastAPI 存量内容','http://x','2026-01-01','s1')")
    con.commit(); con.close()
    # 跑 _init_db（建 FTS + 触发器 + 回填）
    st._init_db()
    con = sqlite3.connect(str(db))
    has_fts = con.execute("SELECT name FROM sqlite_master WHERE name='crawl_records_fts'").fetchone()
    assert has_fts, "FTS 虚表应被建"
    # 回填：存量行进 FTS
    n = con.execute("SELECT count(*) FROM crawl_records_fts").fetchone()[0]
    assert n == 1, f"存量应回填进 FTS，实际 {n}"
    con.close()


def test_fts_match_returns_snippet(kb):
    """≥3 字短语命中 FTS，返回 snippet 文段。"""
    res = kb.search_knowledge.func("路径参数")
    assert "Found" in res
    assert "路径参数" in res
    assert "http://b/path" in res  # 命中正确记录


def test_fts_multi_hit_ranked(kb):
    """跨多记录命中，返回多条。"""
    res = kb.search_knowledge.func("FastAPI")
    assert "Found" in res
    # 两条 s1 记录都含 FastAPI
    assert "http://a/first" in res
    assert "http://b/path" in res


def test_short_query_like_fallback(kb):
    """2 字短词（trigram 需 ≥3 字）走 LIKE 兜底，仍命中。"""
    res = kb.search_knowledge.func("路径")
    assert "Found" in res
    assert "http://b/path" in res


def test_scope_session_filter(kb):
    """scope=session（默认）只查当前会话 s1，不返回 s2 的记录。"""
    res = kb.search_knowledge.func("随便")
    assert "No match" in res  # "随便"在 s2，当前会话 s1 查不到


def test_scope_all_cross_session(kb):
    """scope=all 跨会话命中 s2 记录。"""
    res = kb.search_knowledge.func("随便", scope="all")
    assert "Found" in res
    assert "http://c/other" in res


def test_miss_returns_guidance(kb):
    """miss 返回引导（出门抓 + save_record）。"""
    res = kb.search_knowledge.func("完全不存在的词xyz")
    assert "No match" in res
    assert "save_record" in res  # 引导入库


def test_trigger_sync_on_insert(tmp_path, monkeypatch):
    """触发器同步：save_record INSERT 后 FTS 立即可检索（不用手动回填）。"""
    db = tmp_path / "t3.db"
    import crawagent.tools.save_tool as st
    import crawagent.tools.query_tool as qt
    monkeypatch.setattr(st, "_get_db_path", lambda: str(db))
    monkeypatch.setattr(qt, "_get_db_path", lambda: str(db))
    st._init_db()
    st.set_current_session("s1")
    # 用 save_record 插（触发 AFTER INSERT 触发器）
    # content ≥200 字（过 quality_filter 关①长度，否则会被拒不入库，FTS 也建不上索引）
    long_content = ("这是一个用于验证触发器同步的全新主题文档，包含独特关键词组合。\n\n") * 8
    st.save_record.func(url="http://d", title="新内容", content=long_content)
    res = qt.search_knowledge.func("独特关键词")
    assert "Found" in res
    assert "http://d" in res
