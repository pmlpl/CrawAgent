"""变更 034 — 会话工作文件夹。

覆盖：meta_store work_dir 列迁移 + 读写、session_dir.current_work_dir、
pick-folder 端点（mock tkinter / 防抖）、save_to_file work_dir 路由与逃逸拒绝、
媒体工具落点跟随（download_images / download_social_media）、
run_custom_script 路径注入、未配置 work_dir 行为回归。全部离线。
"""
import asyncio
import json
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------- meta_store：work_dir 列迁移 + 读写 ----------

class _FakeSettings:
    def __init__(self, tmp_path):
        self.sessions_db_path = tmp_path / "sessions.db"


def test_work_dir_roundtrip_and_mkdir(tmp_path):
    import crawagent.storage.meta_store as ms

    s = _FakeSettings(tmp_path)
    ms.reset_meta_conn()
    try:
        target = tmp_path / "产物" / "一加官网"
        got = ms.set_work_dir("sess_wd1", str(target), s)
        assert Path(got) == target.resolve()
        assert target.is_dir()  # 不存在自动创建
        assert ms.get_work_dir("sess_wd1", s) == str(target.resolve())

        # 相对路径规范化为绝对路径
        import os
        got2 = ms.set_work_dir("sess_wd1", ".", s)
        assert Path(got2).is_absolute()
        assert Path(got2) == Path(os.path.abspath(".")).resolve()

        # 清除：空串落回 ""
        ms.set_work_dir("sess_wd1", "", s)
        assert ms.get_work_dir("sess_wd1", s) == ""
        # 未配置的会话 → ""
        assert ms.get_work_dir("sess_missing", s) == ""
    finally:
        ms.reset_meta_conn()


def test_work_dir_set_preserves_title(tmp_path):
    import crawagent.storage.meta_store as ms

    s = _FakeSettings(tmp_path)
    ms.reset_meta_conn()
    try:
        conn = ms.get_meta_conn(s)
        conn.execute(
            "INSERT INTO session_titles (thread_id, title) VALUES (?, ?)", ("t9", "原标题")
        )
        conn.commit()
        ms.set_work_dir("t9", str(tmp_path / "wd"), s)
        row = conn.execute(
            "SELECT title, work_dir FROM session_titles WHERE thread_id = 't9'"
        ).fetchone()
        assert row[0] == "原标题"  # upsert 不清掉已有标题
        assert row[1] == str((tmp_path / "wd").resolve())
    finally:
        ms.reset_meta_conn()


def test_work_dir_old_db_migration(tmp_path):
    """老库（无 work_dir 列）连上后自动 ALTER TABLE 补列，读写可用。"""
    import sqlite3

    import crawagent.storage.meta_store as ms

    db = tmp_path / "meta.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE session_titles (thread_id TEXT PRIMARY KEY, title TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO session_titles VALUES ('old1', '老会话')")
    conn.commit()
    conn.close()

    s = _FakeSettings(tmp_path)
    ms.reset_meta_conn()
    try:
        assert ms.get_work_dir("old1", s) == ""  # 老记录 work_dir 视为空
        ms.set_work_dir("old1", str(tmp_path / "new_wd"), s)
        assert ms.get_work_dir("old1", s) == str((tmp_path / "new_wd").resolve())
        assert ms.get_session_title("old1", s) == "老会话"
    finally:
        ms.reset_meta_conn()


# ---------- session_dir.current_work_dir ----------

def test_current_work_dir_empty_without_context():
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.session_dir import current_work_dir

    tok = ctx_session_id.set("")
    try:
        assert current_work_dir() == ""
    finally:
        ctx_session_id.reset(tok)


def test_current_work_dir_reads_meta(monkeypatch):
    import crawagent.storage.meta_store as ms
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.session_dir import current_work_dir

    monkeypatch.setattr(ms, "get_work_dir", lambda sid, settings=None: "D:/wd" if sid == "s1" else "")
    tok = ctx_session_id.set("s1")
    try:
        assert current_work_dir() == "D:/wd"
    finally:
        ctx_session_id.reset(tok)
    tok = ctx_session_id.set("s2")
    try:
        assert current_work_dir() == ""
    finally:
        ctx_session_id.reset(tok)


# ---------- pick-folder 端点 ----------

def _install_fake_tkinter(monkeypatch, picked):
    """伪造 tkinter / tkinter.filedialog，askdirectory 返回 picked。"""
    created = []

    class _FakeTk:
        def __init__(self):
            created.append(self)

        def withdraw(self):
            pass

        def attributes(self, *a):
            pass

        def destroy(self):
            pass

    fake_tk = types.ModuleType("tkinter")
    fake_tk.Tk = _FakeTk

    fake_fd = types.ModuleType("tkinter.filedialog")

    def _askdirectory(**kwargs):
        _askdirectory.kwargs = kwargs
        return picked

    fake_fd.askdirectory = _askdirectory
    fake_tk.filedialog = fake_fd
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_fd)
    return created, _askdirectory


def test_pick_folder_sync_selected(monkeypatch):
    from crawagent.web.routers import fs as fs_router

    created, ask = _install_fake_tkinter(monkeypatch, picked="D:/chosen")
    r = fs_router._pick_folder_sync()
    assert r == {"ok": True, "path": "D:/chosen"}
    assert len(created) == 1  # Tk 建了且只有一次
    assert "工作文件夹" in ask.kwargs.get("title", "")


def test_pick_folder_sync_canceled(monkeypatch):
    from crawagent.web.routers import fs as fs_router

    _install_fake_tkinter(monkeypatch, picked="")
    r = fs_router._pick_folder_sync()
    assert r == {"ok": True, "canceled": True, "path": ""}


def test_pick_folder_sync_tkinter_error(monkeypatch):
    from crawagent.web.routers import fs as fs_router

    fake_tk = types.ModuleType("tkinter")

    class _Boom:
        def __init__(self):
            raise RuntimeError("no display")

    fake_tk.Tk = _Boom
    fake_tk.filedialog = types.ModuleType("tkinter.filedialog")
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    r = fs_router._pick_folder_sync()
    assert r["ok"] is False
    assert "no display" in r["error"]


def test_pick_folder_endpoint_selected_and_busy(monkeypatch):
    """端点正常返回 + 并发防抖（弹窗打开期间二次请求直接 busy）。"""
    from crawagent.web.routers import fs as fs_router

    release = threading.Event()

    def _blocked():
        release.wait(3)
        return {"ok": True, "path": "D:/slow"}

    monkeypatch.setattr(fs_router, "_pick_folder_sync", _blocked)

    async def _scenario():
        t1 = asyncio.create_task(fs_router.pick_folder())
        await asyncio.sleep(0.05)
        r2 = await fs_router.pick_folder()
        release.set()
        r1 = await t1
        return r1, r2

    r1, r2 = asyncio.run(_scenario())
    assert r1 == {"ok": True, "path": "D:/slow"}
    assert r2 == {"ok": False, "error": "选择框已打开"}
    # 结束后标志复位，可再次发起
    assert asyncio.run(fs_router.pick_folder()) == {"ok": True, "path": "D:/slow"}


# ---------- save_to_file：work_dir 路由 + 安全校验随迁 ----------

def _patch_file_tool(monkeypatch, tmp_path):
    import crawagent.tools.file_tool as ft

    class _S:
        project_root = tmp_path / "proj"
        output_dir = tmp_path / "proj" / "output"

    (_S.output_dir).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ft, "get_settings", lambda: _S())
    return _S


def test_save_to_file_work_dir_root(monkeypatch, tmp_path):
    import crawagent.storage.meta_store as ms
    import crawagent.tools.session_dir as sd
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.file_tool import save_to_file

    _patch_file_tool(monkeypatch, tmp_path)
    wd = tmp_path / "我的产物"
    wd.mkdir()
    monkeypatch.setattr(ms, "get_work_dir", lambda sid, settings=None: str(wd))
    monkeypatch.setattr(ms, "get_session_title", lambda sid, settings=None: "会话名")
    tok = ctx_session_id.set("sess_w")
    try:
        r = save_to_file.func("测试.md", "内容")
        # 落 work_dir 根，不套会话子目录
        assert (wd / "测试.md").read_text(encoding="utf-8") == "内容"
        assert not (tmp_path / "proj" / "output").exists() or not any(
            (tmp_path / "proj" / "output").iterdir()
        )
        assert str(wd) in r
    finally:
        ctx_session_id.reset(tok)


def test_save_to_file_work_dir_subdir(monkeypatch, tmp_path):
    import crawagent.storage.meta_store as ms
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.file_tool import save_to_file

    _patch_file_tool(monkeypatch, tmp_path)
    wd = tmp_path / "wd2"
    wd.mkdir()
    monkeypatch.setattr(ms, "get_work_dir", lambda sid, settings=None: str(wd))
    tok = ctx_session_id.set("sess_w")
    try:
        save_to_file.func("报告.md", "x", subdir="sub")
        assert (wd / "sub" / "报告.md").exists()
    finally:
        ctx_session_id.reset(tok)


def test_save_to_file_work_dir_escape_rejected(monkeypatch, tmp_path):
    import crawagent.storage.meta_store as ms
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.file_tool import save_to_file

    _patch_file_tool(monkeypatch, tmp_path)
    wd = tmp_path / "wd3"
    wd.mkdir()
    monkeypatch.setattr(ms, "get_work_dir", lambda sid, settings=None: str(wd))
    tok = ctx_session_id.set("sess_w")
    try:
        # 相对逃逸
        r = save_to_file.func("../逃逸.txt", "x")
        assert "Save failed" in r and "escapes" in r.lower()
        assert not (tmp_path / "逃逸.txt").exists()
        # 绝对路径（基准外）同样拒绝
        outside = tmp_path / "outside_abs.txt"
        r2 = save_to_file.func(str(outside), "x")
        assert "Save failed" in r2 and "escapes" in r2.lower()
        assert not outside.exists()
    finally:
        ctx_session_id.reset(tok)


def test_save_to_file_no_work_dir_regression(monkeypatch, tmp_path):
    """未配置 work_dir → 014 行为完全不变（会话子目录 + downloads 分类）。"""
    import crawagent.storage.meta_store as ms
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.file_tool import save_to_file

    _patch_file_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(ms, "get_work_dir", lambda sid, settings=None: "")
    monkeypatch.setattr(ms, "get_session_title", lambda sid, settings=None: "回归会话")
    tok = ctx_session_id.set("sess_r")
    try:
        save_to_file.func("a.md", "x")
        assert (tmp_path / "proj" / "output" / "回归会话" / "a.md").exists()
    finally:
        ctx_session_id.reset(tok)


# ---------- 媒体工具落点跟随 ----------

def test_download_images_out_dir_follows_work_dir(monkeypatch, tmp_path):
    import crawagent.tools.download_images as di
    import crawagent.tools.session_dir as sd

    class _S:
        project_root = tmp_path / "proj"
        downloads_dir = tmp_path / "proj" / "downloads"

    monkeypatch.setattr(di, "get_settings", lambda: _S())
    wd = tmp_path / "wd_media"
    wd.mkdir()
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(wd))

    out, err = di._resolve_out_dir("images")
    assert err == ""
    assert out == (wd / "images").resolve()  # 分类 subdir 保留、基准换 work_dir

    # 逃逸拒绝
    out2, err2 = di._resolve_out_dir("../escape")
    assert err2
    assert not out2.is_relative_to(wd)


def test_download_images_out_dir_default_regression(monkeypatch, tmp_path):
    import crawagent.tools.download_images as di
    import crawagent.tools.session_dir as sd

    class _S:
        project_root = tmp_path / "proj"
        downloads_dir = tmp_path / "proj" / "downloads"

    (_S.downloads_dir).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(di, "get_settings", lambda: _S())
    monkeypatch.setattr(sd, "current_work_dir", lambda: "")

    out, err = di._resolve_out_dir("wallpapers")
    assert err == ""
    assert out == (_S.downloads_dir / "wallpapers").resolve()


def test_download_social_media_lands_in_work_dir(monkeypatch, tmp_path):
    """download_social_media：work_dir 非空时 <平台>_<id>/ 落工作文件夹（comments 离线链路）。"""
    import crawagent.tools.social_tool as st
    import crawagent.tools.session_dir as sd

    class _S:
        project_root = tmp_path / "proj"
        downloads_dir = tmp_path / "proj" / "downloads"

    monkeypatch.setattr(st, "get_settings", lambda: _S())
    wd = tmp_path / "wd_social"
    wd.mkdir()
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(wd))
    monkeypatch.setattr(
        st, "_extract_data",
        lambda url, wanted: {"platform": "douyin", "aweme_id": "123", "title": "测试", "comments": [{"t": "c"}]},
    )

    r = json.loads(st.download_social_media.func("https://v.douyin.com/x/", include="comments"))
    assert "error" not in r
    comments_file = wd / "douyin_123" / "comments.json"
    assert comments_file.exists()  # 平台子目录保留、基准换 work_dir
    assert str(wd) in r["output_dir"]


def test_download_social_media_default_regression(monkeypatch, tmp_path):
    import crawagent.tools.social_tool as st
    import crawagent.tools.session_dir as sd

    class _S:
        project_root = tmp_path / "proj"
        downloads_dir = tmp_path / "proj" / "downloads"

    (_S.downloads_dir).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(st, "get_settings", lambda: _S())
    monkeypatch.setattr(sd, "current_work_dir", lambda: "")
    monkeypatch.setattr(
        st, "_extract_data",
        lambda url, wanted: {"platform": "douyin", "aweme_id": "456", "title": "t", "comments": []},
    )

    r = json.loads(st.download_social_media.func("https://v.douyin.com/y/", include="comments"))
    assert (_S.downloads_dir / "douyin_456" / "comments.json").exists()

    # 逃逸拒绝（改后 is_relative_to 边界）
    r2 = json.loads(st.download_social_media.func(
        "https://v.douyin.com/z/", include="comments", subdir="../evil"))
    assert "error" in r2 and "escapes" in r2["error"]


# ---------- run_custom_script 路径注入 ----------

def test_script_format_kwargs_follows_work_dir(monkeypatch, tmp_path):
    import crawagent.tools.script_tool.runner as runner
    import crawagent.tools.session_dir as sd

    class _S:
        project_root = tmp_path / "proj"
        downloads_dir = tmp_path / "proj" / "downloads"
        output_dir = tmp_path / "proj" / "output"

    wd = tmp_path / "wd_script"
    wd.mkdir()
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(wd))
    kw = runner._script_format_kwargs(_S())
    # output/downloads 都指向 work_dir，SESSION_SUBDIR 空串 → 脚本产物落 work_dir 根
    assert kw["output"] == str(wd)
    assert kw["downloads"] == str(wd)
    assert kw["session_subdir"] == ""
    assert kw["root"] == str(_S.project_root)  # PROJECT_ROOT 恒为真实项目根


def test_script_format_kwargs_default_regression(monkeypatch):
    import crawagent.tools.script_tool.runner as runner
    import crawagent.tools.session_dir as sd

    class _S:
        project_root = Path("P")
        downloads_dir = Path("D")
        output_dir = Path("O")

    monkeypatch.setattr(sd, "current_work_dir", lambda: "")
    monkeypatch.setattr(runner, "session_subdir", lambda: "某会话")
    kw = runner._script_format_kwargs(_S())
    assert kw["downloads"] == "D" and kw["output"] == "O" and kw["session_subdir"] == "某会话"


def test_script_header_with_work_dir_formats_clean(monkeypatch, tmp_path):
    """work_dir 注入后整段 header 能 format 成合法脚本（无占位残留）。"""
    import crawagent.tools.script_tool as st
    import crawagent.tools.script_tool.runner as runner
    import crawagent.tools.session_dir as sd

    class _S:
        project_root = tmp_path / "proj"
        downloads_dir = tmp_path / "proj" / "downloads"
        output_dir = tmp_path / "proj" / "output"

    wd = tmp_path / "wd_hdr"
    wd.mkdir()
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(wd))
    header = st._ALLOWED_HEADER.format(
        injected_profiles={}, **runner._script_format_kwargs(_S())
    )
    assert f"OUTPUT_DIR = Path({str(wd)!r})" in header
    assert "SESSION_SUBDIR = ''" in header
