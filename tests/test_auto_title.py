"""auto-title 回归 — 绑定 work_dir 先建空标题行的会话也能被自动命名（2026-10-04 真机发现）。

035 起 POST /api/sessions/{id}/work-dir 会先 upsert 出 title='' 的行；旧逻辑
`SELECT 1 ... if existing: return` 把它当「已有标题」跳过，且 `ON CONFLICT DO
NOTHING` 对已存在的空行也无操作 → 绑过项目的会话永远拿不到自动命名（审计里
显示「无标题」）。修复：守卫只挡真标题；upsert 改为只补空标题行、真标题
（含期间手动改名）不覆盖。全部离线（LLM 走桩）。
"""
import sqlite3
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _tmp_meta_db(tmp_path):
    """建带 session_titles 表的临时库，返回连接（turn_engine.get_meta_conn 指向它）。"""
    conn = sqlite3.connect(tmp_path / "meta.db")
    conn.execute(
        "CREATE TABLE session_titles ("
        "thread_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', "
        "work_dir TEXT NOT NULL DEFAULT '', work_dir_ts TEXT)"
    )
    return conn


def _patch_llm(monkeypatch, title):
    import crawagent.llm.model as llm_model

    monkeypatch.setattr(
        llm_model, "get_llm", lambda **kw: types.SimpleNamespace(
            invoke=lambda p: types.SimpleNamespace(content=title))
    )


def test_generate_title_fills_empty_workdir_row(tmp_path, monkeypatch):
    """先绑 work_dir（title 空行）→ 后台命名能把空行补上（旧 DO NOTHING 漏掉）。"""
    import crawagent.web.turn_engine as te

    conn = _tmp_meta_db(tmp_path)
    conn.execute(
        "INSERT INTO session_titles (thread_id, title, work_dir) VALUES (?, '', 'C:/tmp/wd')",
        ("sid-fill",),
    )
    conn.commit()
    monkeypatch.setattr(te, "get_meta_conn", lambda: conn)
    _patch_llm(monkeypatch, "豆瓣电影爬取")

    te._generate_title("sid-fill", "帮我爬取豆瓣电影Top250", "好的")

    row = conn.execute(
        "SELECT title FROM session_titles WHERE thread_id='sid-fill'"
    ).fetchone()
    assert row[0] == "豆瓣电影爬取"


def test_generate_title_respects_manual_rename(tmp_path, monkeypatch):
    """已有真标题的行不被覆盖（保留手动改名语义）。"""
    import crawagent.web.turn_engine as te

    conn = _tmp_meta_db(tmp_path)
    conn.execute(
        "INSERT INTO session_titles (thread_id, title) VALUES (?, '我的手动名')",
        ("sid-manual",),
    )
    conn.commit()
    monkeypatch.setattr(te, "get_meta_conn", lambda: conn)
    _patch_llm(monkeypatch, "自动生成的名字")

    te._generate_title("sid-manual", "帮我爬取豆瓣电影Top250", "好的")

    row = conn.execute(
        "SELECT title FROM session_titles WHERE thread_id='sid-manual'"
    ).fetchone()
    assert row[0] == "我的手动名"


def test_auto_title_guard_allows_empty_title_row(tmp_path, monkeypatch):
    """守卫只挡真标题：空标题行（仅绑定 work_dir）照常进入命名流程。"""
    import crawagent.web.turn_engine as te
    from langchain_core.messages import AIMessage, HumanMessage

    conn = _tmp_meta_db(tmp_path)
    conn.execute(
        "INSERT INTO session_titles (thread_id, title, work_dir) VALUES (?, '', 'C:/tmp/wd')",
        ("sid-guard",),
    )
    conn.commit()
    monkeypatch.setattr(te, "get_meta_conn", lambda: conn)
    monkeypatch.setattr(te, "_generate_title", lambda *a, **kw: None)  # 线程别真调 LLM

    stub_agent = types.SimpleNamespace(
        get_state=lambda cfg: types.SimpleNamespace(values={"messages": [
            HumanMessage("帮我爬取豆瓣电影Top250"),
            AIMessage(content="好的"),
        ]})
    )
    te._pending_titles.discard("sid-guard")
    try:
        te._maybe_auto_title("sid-guard", stub_agent, {})
        assert "sid-guard" in te._pending_titles  # 进了命名流程
    finally:
        te._pending_titles.discard("sid-guard")


def test_auto_title_guard_skips_real_title(tmp_path, monkeypatch):
    """真标题行仍然跳过（不覆盖手动命名）。"""
    import crawagent.web.turn_engine as te
    from langchain_core.messages import AIMessage, HumanMessage

    conn = _tmp_meta_db(tmp_path)
    conn.execute(
        "INSERT INTO session_titles (thread_id, title) VALUES (?, '真标题')",
        ("sid-real",),
    )
    conn.commit()
    monkeypatch.setattr(te, "get_meta_conn", lambda: conn)

    stub_agent = types.SimpleNamespace(
        get_state=lambda cfg: types.SimpleNamespace(values={"messages": [
            HumanMessage("帮我爬取豆瓣电影Top250"),
            AIMessage(content="好的"),
        ]})
    )
    te._pending_titles.discard("sid-real")
    try:
        te._maybe_auto_title("sid-real", stub_agent, {})
        assert "sid-real" not in te._pending_titles  # 没进命名流程
    finally:
        te._pending_titles.discard("sid-real")
