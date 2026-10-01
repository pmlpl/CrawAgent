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

        # 相对路径规范化为绝对路径（新会话首次绑定，不受锁定影响）
        import os
        got2 = ms.set_work_dir("sess_wd2", ".", s)
        assert Path(got2).is_absolute()
        assert Path(got2) == Path(os.path.abspath(".")).resolve()

        # 未配置的会话 → ""
        assert ms.get_work_dir("sess_missing", s) == ""
    finally:
        ms.reset_meta_conn()


# ---------- 038：会话↔项目 1:1 锁定守卫 ----------

def test_work_dir_rebind_rejected(tmp_path):
    """已绑定会话换绑 → WorkDirLockedError，绑定不变、不写历史表。"""
    import crawagent.storage.meta_store as ms

    s = _FakeSettings(tmp_path)
    ms.reset_meta_conn()
    try:
        first = ms.set_work_dir("s1", str(tmp_path / "projA"), s)
        try:
            ms.set_work_dir("s1", str(tmp_path / "projB"), s)
            raise AssertionError("换绑应被拒绝")
        except ms.WorkDirLockedError as e:
            msg = str(e)
            assert str(first) in msg  # 人话含旧路径
            assert "新建会话" in msg  # 含新建会话指引
        # 绑定未被覆盖
        assert ms.get_work_dir("s1", s) == str(first)
        # 历史表只有第一条，被拒的 projB 没进去
        assert ms.list_work_dir_history(s) == [str(first)]
    finally:
        ms.reset_meta_conn()


def test_work_dir_clear_rejected(tmp_path):
    """已绑定会话清除（path 空）→ WorkDirLockedError，防「清除再绑别的」绕过。"""
    import crawagent.storage.meta_store as ms

    s = _FakeSettings(tmp_path)
    ms.reset_meta_conn()
    try:
        bound = ms.set_work_dir("s1", str(tmp_path / "projA"), s)
        try:
            ms.set_work_dir("s1", "", s)
            raise AssertionError("清除应被拒绝")
        except ms.WorkDirLockedError as e:
            assert str(bound) in str(e)
            assert "新建会话" in str(e)
        assert ms.get_work_dir("s1", s) == str(bound)  # 绑定原封不动
    finally:
        ms.reset_meta_conn()


def test_work_dir_clear_unbound_noop(tmp_path):
    """未绑定会话传空串 → 合法 no-op（仍返回空串，不抛锁定异常）。"""
    import crawagent.storage.meta_store as ms

    s = _FakeSettings(tmp_path)
    ms.reset_meta_conn()
    try:
        assert ms.set_work_dir("fresh", "", s) == ""
        assert ms.get_work_dir("fresh", s) == ""
    finally:
        ms.reset_meta_conn()


def test_work_dir_same_path_idempotent(tmp_path):
    """同路径重绑幂等放行，且刷新 work_dir_history 的 last_used（常用置顶）。"""
    import os

    import crawagent.storage.meta_store as ms

    s = _FakeSettings(tmp_path)
    ms.reset_meta_conn()
    try:
        target = tmp_path / "projA"
        first = ms.set_work_dir("s1", str(target), s)
        # 换写法重绑同一目录：末尾补路径分隔符（normpath 消差异；Windows
        # 大小写差异同理由 normcase 消掉，判定入口只认规范化后的形态）
        again = ms.set_work_dir("s1", str(target) + os.sep, s)
        assert Path(again) == Path(first)
        assert ms.get_work_dir("s1", s) == str(first)
        # 历史表仍只有一条（append-only 去重），last_used 被刷新
        assert ms.list_work_dir_history(s) == [str(first)]
    finally:
        ms.reset_meta_conn()


def test_work_dir_reject_does_not_mkdir(tmp_path):
    """换绑被拒时不建新目录（连 mkdir 副作用都没有）。"""
    import crawagent.storage.meta_store as ms

    s = _FakeSettings(tmp_path)
    ms.reset_meta_conn()
    try:
        ms.set_work_dir("s1", str(tmp_path / "projA"), s)
        try:
            ms.set_work_dir("s1", str(tmp_path / "projB"), s)
        except ms.WorkDirLockedError:
            pass
        assert not (tmp_path / "projB").exists()
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


# ---------- 038：work_dir_unavailable 失效检查 helper ----------

def test_work_dir_unavailable_when_dir_deleted(monkeypatch, tmp_path):
    """已绑定但目录被删 → 人话错误串（含路径 + 恢复/新建会话指引 + 未写入声明）。"""
    import crawagent.tools.session_dir as sd

    gone = tmp_path / "gone"  # 从不创建
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(gone))
    msg = sd.work_dir_unavailable()
    assert str(gone) in msg
    assert "已不存在" in msg
    assert "新建会话" in msg
    assert "未写入" in msg


def test_work_dir_unavailable_empty_when_ok_or_unbound(monkeypatch, tmp_path):
    """目录存在 / 未绑定 work_dir → 恒空串（放行）。"""
    import crawagent.tools.session_dir as sd

    alive = tmp_path / "alive"
    alive.mkdir()
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(alive))
    assert sd.work_dir_unavailable() == ""
    monkeypatch.setattr(sd, "current_work_dir", lambda: "")
    assert sd.work_dir_unavailable() == ""


def test_work_dir_unavailable_recovers_without_restart(monkeypatch, tmp_path):
    """文件夹删了报失效、建回来自动恢复（现查 exists 不缓存）。"""
    import crawagent.tools.session_dir as sd

    wd = tmp_path / "wd"
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(wd))
    assert "已不存在" in sd.work_dir_unavailable()
    wd.mkdir()
    assert sd.work_dir_unavailable() == ""


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


# ---------- 038：四工具路径失效防护 ----------

def test_save_to_file_unavailable_dir_no_write(monkeypatch, tmp_path):
    """save_to_file：work_dir 被删 → 人话报错，目录不被重建、文件不落盘。"""
    import crawagent.tools.session_dir as sd
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.file_tool import save_to_file

    _patch_file_tool(monkeypatch, tmp_path)
    gone = tmp_path / "gone_wd"
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(gone))
    tok = ctx_session_id.set("sess_gone")
    try:
        r = save_to_file.func("测试.md", "内容")
        assert "Save failed" in r
        assert "已不存在" in r
        assert not gone.exists()          # 目录未被静默重建
        assert not (gone / "测试.md").exists()
    finally:
        ctx_session_id.reset(tok)


def test_download_social_media_unavailable_dir(monkeypatch, tmp_path):
    """download_social_media：work_dir 被删 → error dict 人话，不做网络解析。"""
    import crawagent.tools.session_dir as sd
    import crawagent.tools.social_tool as st

    gone = tmp_path / "gone_social"
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(gone))
    called = []
    monkeypatch.setattr(st, "_extract_data", lambda url, wanted: called.append(url))
    r = json.loads(st.download_social_media.func("https://v.douyin.com/x/"))
    assert "已不存在" in r["error"]
    assert not called  # 落盘前拦截，连解析都没发起
    assert not gone.exists()


def test_download_images_unavailable_dir(monkeypatch, tmp_path):
    """download_images：work_dir 被删 → error dict 人话，目录不被重建。"""
    import crawagent.tools.session_dir as sd
    import crawagent.tools.download_images as di

    gone = tmp_path / "gone_images"
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(gone))
    r = json.loads(di.download_images.func("http://a.example.com/x.jpg", subdir="walls"))
    assert "已不存在" in r["error"]
    assert not gone.exists()


def test_run_custom_script_unavailable_dir(monkeypatch, tmp_path):
    """run_custom_script：work_dir 被删 → [ERROR] 人话，不跑子进程。"""
    import crawagent.tools.script_tool.runner as runner
    import crawagent.tools.session_dir as sd

    gone = tmp_path / "gone_script"
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(gone))
    r = runner.run_custom_script.func("print('hi')")
    assert r.startswith("[ERROR]")
    assert "已不存在" in r
    assert "脚本无法运行" in r


def test_tools_unavailable_check_not_triggered_when_unbound(monkeypatch, tmp_path):
    """未绑定 work_dir → 失效检查恒放行，默认落点行为完全不变（回归）。"""
    import crawagent.tools.session_dir as sd
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools.file_tool import save_to_file

    _patch_file_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(sd, "current_work_dir", lambda: "")
    tok = ctx_session_id.set("sess_unbound")
    try:
        r = save_to_file.func("回归.md", "x")
        assert r.startswith("Saved to")
    finally:
        ctx_session_id.reset(tok)


# ---------- work-dir 绑定端点（变更 035：选择即落库） ----------

def _patch_meta_conn(monkeypatch, tmp_path):
    """meta_store 打到 tmp 库（set/get_work_dir 内部调模块级 get_meta_conn，patch 即生效）。"""
    import sqlite3

    import crawagent.storage.meta_store as ms

    conn = sqlite3.connect(str(tmp_path / "meta.db"), check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS session_titles ("
        "thread_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "work_dir TEXT DEFAULT '', work_dir_ts TEXT DEFAULT '')"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS work_dir_history ("
        "path TEXT PRIMARY KEY, last_used TEXT NOT NULL)"
    )
    conn.commit()
    monkeypatch.setattr(ms, "get_meta_conn", lambda settings=None: conn)
    return conn


def _work_dir_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from crawagent.web.routers.sessions.work_dir import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_work_dir_endpoint_bind_new_session(monkeypatch, tmp_path):
    """新会话（没发过消息、meta.db 无记录）直接绑定 → upsert 建记录 + 目录创建。"""
    _patch_meta_conn(monkeypatch, tmp_path)
    client = _work_dir_client()

    target = tmp_path / "新项目"
    r = client.post("/api/sessions/brand_new/work-dir", json={"path": str(target)})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert Path(data["work_dir"]) == target.resolve()
    assert target.is_dir()

    import crawagent.storage.meta_store as ms
    assert ms.get_work_dir("brand_new") == str(target.resolve())


def test_work_dir_endpoint_rebind_and_clear_rejected(monkeypatch, tmp_path):
    """038 1:1 锁定（路由层）：同会话换项目/清除 → ok:False 人话错误，绑定不变。"""
    _patch_meta_conn(monkeypatch, tmp_path)
    client = _work_dir_client()

    wd1 = tmp_path / "项目一"
    wd2 = tmp_path / "项目二"
    assert client.post("/api/sessions/s1/work-dir", json={"path": str(wd1)}).json()["ok"]

    # 换绑被拒：错误含旧路径 + 新建会话指引
    r = client.post("/api/sessions/s1/work-dir", json={"path": str(wd2)})
    data = r.json()
    assert data["ok"] is False
    assert str(wd1.resolve()) in data["error"]
    assert "新建会话" in data["error"]

    # 清除被拒：同款人话（防「清除再绑别的」绕过）
    r2 = client.post("/api/sessions/s1/work-dir", json={"path": ""})
    data2 = r2.json()
    assert data2["ok"] is False
    assert "新建会话" in data2["error"]

    import crawagent.storage.meta_store as ms
    assert ms.get_work_dir("s1") == str(wd1.resolve())
    # 被拒的路径不进历史表
    assert ms.list_work_dir_history() == [str(wd1.resolve())]


def test_work_dir_endpoint_lock_error_distinct_from_mkdir_error(monkeypatch, tmp_path):
    """错误分流：WorkDirLockedError（锁定文案）≠ mkdir OSError（创建失败文案）。"""
    _patch_meta_conn(monkeypatch, tmp_path)
    client = _work_dir_client()

    # 锁定文案：不含「创建失败」
    wd1 = tmp_path / "锁定项目"
    client.post("/api/sessions/s1/work-dir", json={"path": str(wd1)})
    r = client.post("/api/sessions/s1/work-dir", json={"path": str(tmp_path / "别的")})
    assert r.json()["ok"] is False
    assert "一个会话只支持一个项目" in r.json()["error"]
    assert "创建失败" not in r.json()["error"]

    # mkdir 文案：新会话绑到被文件占用的路径 → 「文件夹创建失败」
    blocker = tmp_path / "blocker"
    blocker.write_text("我是文件不是目录")
    r2 = client.post("/api/sessions/s_new/work-dir", json={"path": str(blocker / "sub")})
    assert r2.json()["ok"] is False
    assert "创建失败" in r2.json()["error"]


def test_work_dir_endpoint_mkdir_failure(monkeypatch, tmp_path):
    """目录创建失败（父级被同名文件占用）→ ok:False + 人话错误，不裸异常。"""
    _patch_meta_conn(monkeypatch, tmp_path)
    client = _work_dir_client()

    blocker = tmp_path / "blocker"
    blocker.write_text("我是文件不是目录")
    r = client.post("/api/sessions/s2/work-dir", json={"path": str(blocker / "sub")})
    data = r.json()
    assert data["ok"] is False
    assert "失败" in data["error"]


def test_work_dir_endpoint_get_roundtrip(monkeypatch, tmp_path):
    """GET 回读绑定 + exists 磁盘存在性（038）。"""
    _patch_meta_conn(monkeypatch, tmp_path)
    client = _work_dir_client()

    wd = tmp_path / "回读项目"
    client.post("/api/sessions/s3/work-dir", json={"path": str(wd)})
    r = client.get("/api/sessions/s3/work-dir")
    assert r.json() == {"ok": True, "work_dir": str(wd.resolve()), "exists": True}

    # 手动删掉文件夹 → exists 翻 false（前端失效标注数据源）
    import shutil
    shutil.rmtree(wd)
    r2 = client.get("/api/sessions/s3/work-dir")
    assert r2.json()["exists"] is False

    # 未绑定的会话 → 空串 + exists 恒 true（无意义，前端不消费）
    assert client.get("/api/sessions/s_none/work-dir").json() == {
        "ok": True, "work_dir": "", "exists": True,
    }


def test_chat_ws_no_longer_accepts_work_dir_payload():
    """035 回归：chat_ws 不再有 work_dir 载荷处理（绑定收敛到 REST 端点）。

    WS 层离线测不了（发消息即触发真实轮次），用源码扫描锁住契约：
    server.py 的 chat_ws 里不允许再出现 payload work_dir 分支。
    """
    import io

    server_path = Path(__file__).resolve().parents[1] / "crawagent" / "web" / "server.py"
    src = io.open(server_path, encoding="utf-8").read()
    assert 'payload.get("work_dir")' not in src
    assert "set_work_dir" not in src  # 绑定只发生在 sessions/work_dir.py


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


def test_script_cwd_follows_work_dir(monkeypatch, tmp_path):
    """子进程 CWD 跟随 work_dir：脚本里相对路径 open() 自然落工作文件夹（034 bug 修复）。"""
    import crawagent.tools.script_tool.runner as runner
    import crawagent.tools.session_dir as sd

    wd = tmp_path / "cwd_wd"
    wd.mkdir()
    monkeypatch.setattr(sd, "current_work_dir", lambda: str(wd))
    assert runner._resolve_script_cwd() == str(wd)


def test_script_cwd_defaults_to_tmp_when_no_work_dir(monkeypatch, tmp_path):
    """未设 work_dir → CWD 退回 _tmp（启动自动清理兜底，与 034 前行为一致）。"""
    import crawagent.tools.script_tool.runner as runner
    import crawagent.tools.session_dir as sd

    monkeypatch.setattr(sd, "current_work_dir", lambda: "")
    tmp = tmp_path / "_tmp"
    monkeypatch.setattr(runner, "_ensure_tmp", lambda: tmp)
    assert runner._resolve_script_cwd() == str(tmp)


# ---------- 历史项目查询端点（变更 037） ----------

def _insert_wd_history_row(conn, path, ts):
    """直插一条绑定历史（可控时间戳），供历史排序测试。"""
    conn.execute(
        "INSERT INTO work_dir_history (path, last_used) VALUES (?, ?) "
        "ON CONFLICT(path) DO UPDATE SET last_used = excluded.last_used",
        (path, ts),
    )
    conn.commit()


def test_work_dir_history_dedup_and_sort(monkeypatch, tmp_path):
    """去重 + 按最近打开时间倒序；同 path 重复打开刷新 ts。"""
    conn = _patch_meta_conn(monkeypatch, tmp_path)
    _insert_wd_history_row(conn, "/projA", "2026-01-01T00:00:00")
    _insert_wd_history_row(conn, "/projB", "2026-01-03T00:00:00")
    _insert_wd_history_row(conn, "/projA", "2026-01-02T00:00:00")  # 同 path 重开 → 刷 ts

    import crawagent.storage.meta_store as ms
    history = ms.list_work_dir_history()
    # projB(01-03) → projA(01-02)；去重后 2 项
    assert history == ["/projB", "/projA"]


def test_work_dir_history_empty(monkeypatch, tmp_path):
    _patch_meta_conn(monkeypatch, tmp_path)
    import crawagent.storage.meta_store as ms
    assert ms.list_work_dir_history() == []


def test_work_dir_history_limit_10(monkeypatch, tmp_path):
    """超过 10 个去重 work_dir → 只返回最近 10 个。"""
    conn = _patch_meta_conn(monkeypatch, tmp_path)
    for i in range(12):
        _insert_wd_history_row(conn, f"/p{i}", f"2026-01-{i + 1:02d}T00:00:00")
    import crawagent.storage.meta_store as ms
    history = ms.list_work_dir_history()
    assert len(history) == 10
    assert history[0] == "/p11"  # 最近
    assert "/p0" not in history  # 最老被截掉


def test_work_dir_history_accumulates_across_sessions(monkeypatch, tmp_path):
    """038 后换绑不可能，append-only 历史的累积来源 = 多会话各绑各的。

    （037 原回归「换绑后旧路径仍在历史」随 1:1 锁定语义退役：换绑在
    set_work_dir 入口即被拒，append-only 逻辑原样保留，此处锁多会话累积。）
    """
    s = _FakeSettings(tmp_path)
    import crawagent.storage.meta_store as ms
    ms.reset_meta_conn()
    try:
        a = ms.set_work_dir("s1", str(tmp_path / "projA"), s)
        b = ms.set_work_dir("s2", str(tmp_path / "projB"), s)
        assert ms.list_work_dir_history(s) == [b, a]  # 按最近使用倒序
    finally:
        ms.reset_meta_conn()


def test_work_dir_history_backfill_from_legacy(monkeypatch, tmp_path):
    """老库回填：连接初始化时把 session_titles 现存绑定快照灌进历史表。"""
    import sqlite3

    import crawagent.storage.meta_store as ms
    # 手工造一个"老库"：session_titles 已有绑定，无 work_dir_history 表
    conn = sqlite3.connect(str(tmp_path / "meta.db"))
    conn.execute(
        "CREATE TABLE session_titles ("
        "thread_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "work_dir TEXT DEFAULT '', work_dir_ts TEXT DEFAULT '')"
    )
    conn.execute(
        "INSERT INTO session_titles VALUES ('s1', '', '/legacyA', '2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO session_titles VALUES ('s2', '', '/legacyB', '2026-01-02T00:00:00')"
    )
    conn.commit()
    conn.close()

    s = _FakeSettings(tmp_path)
    ms.reset_meta_conn()
    try:
        # 首次连接触发迁移：建历史表 + 回填快照（按 work_dir_ts 倒序）
        assert ms.list_work_dir_history(s) == ["/legacyB", "/legacyA"]
        # 迁移后正常追加新绑定
        fresh = ms.set_work_dir("s3", str(tmp_path / "fresh"), s)
        assert ms.list_work_dir_history(s) == [fresh, "/legacyB", "/legacyA"]
    finally:
        ms.reset_meta_conn()


def test_work_dir_history_endpoint(monkeypatch, tmp_path):
    """GET history → {work_dirs: [{path, exists}]}（038 新结构，按最近使用排序）。"""
    conn = _patch_meta_conn(monkeypatch, tmp_path)
    _insert_wd_history_row(conn, "/projA", "2026-01-01T00:00:00")
    _insert_wd_history_row(conn, "/projB", "2026-01-02T00:00:00")
    client = _work_dir_client()
    r = client.get("/api/sessions/work-dirs/history")
    assert r.status_code == 200
    data = r.json()
    paths = [w["path"] for w in data["work_dirs"]]
    assert paths == ["/projB", "/projA"]
    # 磁盘上都不存在 → exists 全 false（失效项标灰数据源）
    assert all(w["exists"] is False for w in data["work_dirs"])

    # 真实存在的目录 → exists true
    alive = tmp_path / "alive_proj"
    alive.mkdir()
    _insert_wd_history_row(conn, str(alive), "2026-01-03T00:00:00")
    data2 = client.get("/api/sessions/work-dirs/history").json()
    by_path = {w["path"]: w["exists"] for w in data2["work_dirs"]}
    assert by_path[str(alive)] is True
    assert by_path["/projB"] is False
