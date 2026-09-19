"""session_dir — 会话产物子目录解析（变更 014）。

覆盖：sanitize_dirname 清洗、session_subdir 标题/兜底逻辑、
meta_store.get_session_title、save_to_file 默认落会话子目录 + 显式覆盖 + 无会话兜底、
script_tool 脚本头部 SESSION_SUBDIR 注入。全部离线（tmp_path + monkeypatch）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------- sanitize_dirname ----------

def test_sanitize_replaces_invalid_chars():
    from crawagent.tools.session_dir import sanitize_dirname
    assert sanitize_dirname("a/b:c") == "a_b_c"
    assert sanitize_dirname("back" + chr(92) + "slash") == "back_slash"
    assert sanitize_dirname("lt" + chr(60) + "gt" + chr(62)) == "lt_gt_"  # 非法字符一对一转 _，尾部 _ 合法


def test_sanitize_strips_and_truncates():
    from crawagent.tools.session_dir import sanitize_dirname
    assert sanitize_dirname("  .点开头. ") == "点开头"
    assert sanitize_dirname("x" * 50) == "x" * 40
    assert sanitize_dirname("") == ""
    assert sanitize_dirname(None) == ""


# ---------- session_subdir ----------

def test_session_subdir_empty_without_context():
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.session_dir import session_subdir
    tok = ctx_session_id.set("")
    try:
        assert session_subdir() == ""
    finally:
        ctx_session_id.reset(tok)


def test_session_subdir_uses_title(monkeypatch):
    import crawagent.storage.meta_store as ms
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.session_dir import session_subdir
    monkeypatch.setattr(ms, "get_session_title", lambda sid, settings=None: "逆向 分析/结果:1")
    tok = ctx_session_id.set("sess_abc123456")
    try:
        assert session_subdir() == "逆向 分析_结果_1"
    finally:
        ctx_session_id.reset(tok)


def test_session_subdir_falls_back_to_sid(monkeypatch):
    import crawagent.storage.meta_store as ms
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.session_dir import session_subdir
    monkeypatch.setattr(ms, "get_session_title", lambda sid, settings=None: "")
    tok = ctx_session_id.set("abcdefgh123456")
    try:
        assert session_subdir() == "abcdefgh"
    finally:
        ctx_session_id.reset(tok)


# ---------- meta_store.get_session_title ----------

def test_get_session_title_roundtrip(tmp_path, monkeypatch):
    import crawagent.storage.meta_store as ms

    class _S:
        sessions_db_path = tmp_path / "sessions.db"

    ms.reset_meta_conn()
    try:
        conn = ms.get_meta_conn(_S())
        conn.execute(
            "INSERT INTO session_titles (thread_id, title) VALUES (?, ?)", ("t1", "标题")
        )
        conn.commit()
        assert ms.get_session_title("t1", _S()) == "标题"
        assert ms.get_session_title("missing", _S()) == ""
    finally:
        ms.reset_meta_conn()


# ---------- save_to_file 集成 ----------

def _patch_tmp_settings(monkeypatch, tmp_path):
    """让 file_tool 的 get_settings 指向 tmp 目录。"""
    import crawagent.tools.file_tool as ft

    class _S:
        project_root = tmp_path
        output_dir = tmp_path / "output"

    _S.output_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ft, "get_settings", lambda: _S())
    return _S


def test_save_to_file_defaults_to_session_dir(monkeypatch, tmp_path):
    import crawagent.storage.meta_store as ms
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.file_tool import save_to_file
    _patch_tmp_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(ms, "get_session_title", lambda sid, settings=None: "我的会话")
    tok = ctx_session_id.set("sess_xyz1")
    try:
        r = save_to_file.func("note.md", "hello")
        assert "我的会话" in r and "note.md" in r
        assert (tmp_path / "output" / "我的会话" / "note.md").read_text(encoding="utf-8") == "hello"
    finally:
        ctx_session_id.reset(tok)


def test_save_to_file_explicit_subdir_overrides(monkeypatch, tmp_path):
    import crawagent.storage.meta_store as ms
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.file_tool import save_to_file
    _patch_tmp_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(ms, "get_session_title", lambda sid, settings=None: "我的会话")
    tok = ctx_session_id.set("sess_xyz1")
    try:
        r = save_to_file.func("note.md", "hi", subdir="手动目录")
        assert "手动目录" in r
        assert (tmp_path / "output" / "手动目录" / "note.md").exists()
        assert not (tmp_path / "output" / "我的会话").exists()
    finally:
        ctx_session_id.reset(tok)


def test_save_to_file_no_session_lands_root(monkeypatch, tmp_path):
    from crawagent.tools.file_tool import save_to_file
    _patch_tmp_settings(monkeypatch, tmp_path)
    r = save_to_file.func("loose.md", "x")
    assert (tmp_path / "output" / "loose.md").exists()
    assert "loose.md" in r


# ---------- script_tool SESSION_SUBDIR 注入 ----------

def test_run_custom_script_header_contains_session_subdir(monkeypatch):
    """_ALLOWED_HEADER.format 接受 session_subdir 参数且注入为常量行。"""
    import crawagent.storage.meta_store as ms
    import crawagent.tools.script_tool as st
    from crawagent.graph.agent import ctx_session_id
    monkeypatch.setattr(ms, "get_session_title", lambda sid, settings=None: "注入会话")
    tok = ctx_session_id.set("sess_inject1")
    try:
        header = st._ALLOWED_HEADER.format(
            root="R", downloads="D", output="O",
            session_subdir=st.session_subdir(), injected_profiles={},
        )
        assert "SESSION_SUBDIR = '注入会话'" in header
    finally:
        ctx_session_id.reset(tok)
