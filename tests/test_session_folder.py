"""变更 039 — 会话文件夹 + 长期记忆提炼与清理。

覆盖：session_folder 路径/惰性创建/快照双写/统计/归档移动、save_record
档案双写（含盘失败降级、无会话跳过）、蒸馏归档全流程（mock LLM 先行：
成功入库+移入 _archived / LLM 失败不动文件夹 / 解析失败不动 / 空文件夹
不动 / 直插条目可被 search_knowledge 检索）。全部离线（LLM 全 mock）。
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeSettings:
    def __init__(self, tmp_path):
        self.project_root = tmp_path
        self.db_path = tmp_path / "crawagent.db"


@pytest.fixture()
def sf_env(tmp_path, monkeypatch):
    """session_folder 全部打到 tmp_path/data/sessions/。"""
    import crawagent.tools.session_folder as sf
    s = _FakeSettings(tmp_path)
    monkeypatch.setattr(sf, "get_settings", lambda: s)
    return sf, tmp_path


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    """save_tool / query_tool 的 DB 打到 tmp_path（双写与提炼入库用）。"""
    import crawagent.tools.query_tool as qt
    import crawagent.tools.save_tool as st
    db = tmp_path / "crawagent.db"
    monkeypatch.setattr(st, "_get_db_path", lambda: str(db))
    monkeypatch.setattr(qt, "_get_db_path", lambda: str(db))
    return st, qt, db


# ---------- session_folder：路径 / 惰性创建 / 快照 ----------

def test_session_folder_path_sanitized(sf_env):
    """sid 防御性清洗：路径分隔符等非法字符替换为 _，只落 sessions 根下一层。"""
    sf, tmp_path = sf_env
    root = tmp_path / "data" / "sessions"
    p = sf.session_folder_path("../../evil")
    assert p.is_absolute()
    assert p.parent == root  # 只落根下一层，穿越失效
    assert "/" not in p.name and "\\" not in p.name and ":" not in p.name
    # 空 sid → 兜底名
    assert sf.session_folder_path("").name == "unknown"


def test_ensure_session_folder_lazy(sf_env):
    """惰性创建：archive/ 子目录一并建；不写东西就不会有文件夹。"""
    sf, tmp_path = sf_env
    root = tmp_path / "data" / "sessions"
    assert not (root / "sess_a").exists()  # 未写不建
    out = sf.ensure_session_folder("sess_a")
    assert (out / "archive").is_dir()


def test_write_archive_snapshot_header_and_dedup(sf_env):
    """快照 md 头部元信息完整；重名自动序号 -1/-2（与上传同款）。"""
    sf, tmp_path = sf_env
    p1 = sf.write_archive_snapshot("sess_b", "一加官网抓取", "正文内容", source_url="https://oneplus.com")
    assert p1 == tmp_path / "data" / "sessions" / "sess_b" / "archive" / "一加官网抓取.md"
    text = p1.read_text(encoding="utf-8")
    assert "# 一加官网抓取" in text
    assert "> 会话: sess_b" in text
    assert "归档时间" in text
    assert "> 来源: https://oneplus.com" in text
    assert "正文内容" in text
    p2 = sf.write_archive_snapshot("sess_b", "一加官网抓取", "第二次")
    assert p2.name == "一加官网抓取-1.md"
    p3 = sf.write_archive_snapshot("sess_b", "一加官网抓取", "第三次")
    assert p3.name == "一加官网抓取-2.md"


# ---------- session_folder：统计 + 归档移动 ----------

def _make_folder(sf, sid, files: dict, mtime: float | None = None):
    root = sf.ensure_session_folder(sid)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if mtime is not None:
            import os
            os.utime(p, (mtime, mtime))
    return root


def test_iter_session_folders_skips_archived(sf_env):
    sf, tmp_path = sf_env
    _make_folder(sf, "sess_c", {"archive/a.md": "x"})
    _make_folder(sf, "sess_d", {"archive/b.md": "y"})
    # 手工造一个已归档目录 + 一个散文件
    arch = tmp_path / "data" / "sessions" / "_archived" / "sess_old"
    arch.mkdir(parents=True)
    (tmp_path / "data" / "sessions" / "loose.txt").write_text("not a dir")
    names = {p.name for p in sf.iter_session_folders()}
    assert names == {"sess_c", "sess_d"}


def test_folder_last_active_and_size(sf_env):
    sf, tmp_path = sf_env
    old = time.time() - 200 * 86400  # 200 天前
    root = _make_folder(sf, "sess_e", {"archive/a.md": "abc" * 100}, mtime=old)
    last = sf.folder_last_active(root)
    assert abs(last - old) < 5  # 取夹内文件最新 mtime
    assert sf.folder_size(root) == 300
    # 空文件夹 → 退回夹自身 mtime（不炸）
    empty = sf.ensure_session_folder("sess_empty")
    assert sf.folder_last_active(empty) > 0
    assert sf.folder_size(empty) == 0


def test_archive_session_folder_move_and_suffix(sf_env):
    """提炼成功后整夹移入 _archived/；目标同名加序号，绝不覆盖。"""
    sf, tmp_path = sf_env
    _make_folder(sf, "sess_f", {"archive/a.md": "x"})
    dst = sf.archive_session_folder("sess_f")
    assert dst == tmp_path / "data" / "sessions" / "_archived" / "sess_f"
    assert (dst / "archive" / "a.md").exists()
    assert not (tmp_path / "data" / "sessions" / "sess_f").exists()
    # 二次归档同名 → -1 序号，原归档不丢
    _make_folder(sf, "sess_f", {"archive/a.md": "y"})
    dst2 = sf.archive_session_folder("sess_f")
    assert dst2.name == "sess_f-1"
    assert (dst / "archive" / "a.md").read_text(encoding="utf-8") == "x"


# ---------- save_record 档案双写 ----------

def test_save_record_double_writes_snapshot(sf_env, db_env, monkeypatch):
    """save_record 入库成功 → md 快照同步落会话文件夹（库为主盘为副本）。"""
    st, qt, db = db_env
    # 对环境免疫：同线程前面测试可能泄漏过 set_current_session（ContextVar
    # 不随 monkeypatch 还原），先显式清空再验证「无会话上下文」分支
    st.set_current_session("")
    # 过五关：长度 + 结构（标题 + 空行分段）
    content = "# 归档正文\n\n" + ("这是足够长的正文段落。\n\n" * 30)
    out = st.save_record.func(
        url="https://example.com/arch", title="归档主题", content=content,
    )
    # 无会话上下文 → 只入库不落快照（行为同改前）
    assert "Saved successfully" in out
    assert not (sf_env[1] / "data" / "sessions").exists()

    # 设置会话上下文 → 双写快照（换一份正文，避开五关 md5 去重）
    st.set_current_session("sess_g")
    try:
        content2 = "# 另一篇归档\n\n" + ("完全不同的一段正文内容。\n\n" * 30)
        out2 = st.save_record.func(
            url="https://example.com/arch2", title="第二个归档", content=content2,
        )
        assert "Saved successfully" in out2
        snap = sf_env[1] / "data" / "sessions" / "sess_g" / "archive" / "第二个归档.md"
        assert snap.exists()
        text = snap.read_text(encoding="utf-8")
        assert "> 会话: sess_g" in text
        assert "> 来源: https://example.com/arch2" in text
        assert "完全不同的一段正文内容" in text
    finally:
        st.set_current_session("")


def test_save_record_snapshot_failure_degrades(sf_env, db_env, monkeypatch):
    """库成功、盘失败 → 结果注明「已入库但本地快照写入失败」，不回滚。"""
    import crawagent.tools.session_folder as sf
    st, qt, db = db_env
    content = "# 归档正文\n\n" + ("这是足够长的正文段落。\n\n" * 30)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(sf, "write_archive_snapshot", _boom)
    st.set_current_session("sess_h")
    try:
        out = st.save_record.func(
            url="https://example.com/x", title="t", content=content,
        )
        assert "Saved successfully" in out
        assert "已入库但本地快照写入失败" in out
        # 库里确实有
        con = sqlite3.connect(str(db))
        n = con.execute("SELECT count(*) FROM crawl_records").fetchone()[0]
        con.close()
        assert n == 1
    finally:
        st.set_current_session("")


def test_save_record_rejected_no_snapshot(sf_env, db_env):
    """五关淘汰（[REJECTED]）→ 不入库也不落快照。"""
    st, qt, db = db_env
    st.set_current_session("sess_i")
    try:
        out = st.save_record.func(url="https://e.com/x", title="t", content="太短")
        assert "[REJECTED]" in out
        assert not (sf_env[1] / "data" / "sessions" / "sess_i").exists()
    finally:
        st.set_current_session("")


# ---------- 长期记忆直插 + 可检索 ----------

def test_insert_longterm_record_searchable(sf_env, db_env):
    """insert_longterm_record 直插（免五关）→ FTS 触发器自动入索引，
    search_knowledge 可检索到（008 链路复用验证）。"""
    st, qt, db = db_env
    rid = st.insert_longterm_record(
        url="session://sess_j",
        title="某站点反爬结论",
        content="该站图片走 CSS background-image，requests 抓不到，需 Playwright 渲染",
        session_id="sess_j",
    )
    assert rid > 0
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT url, platform, session_id FROM crawl_records WHERE id=?", (rid,)
    ).fetchone()
    con.close()
    assert row[0] == "session://sess_j"
    assert row[1] == "长期记忆"
    assert row[2] == "sess_j"
    # 短条目不被五关误杀（同内容走 save_record 会被 ① 淘汰）
    res = qt.search_knowledge.func("CSS background-image", scope="all")
    assert "反爬结论" in res


# ---------- 蒸馏归档全流程（mock LLM） ----------

_GOOD_LLM_JSON = (
    "```json\n"
    '[{"topic": "壁纸站抓取策略", "points": ["图片走 CSS background-image",'
    ' "需要 Playwright 渲染", "referer 传站点主页防热链"]},'
    ' {"topic": "B站下载要点", "points": ["音视频 DASH 分离需 ffmpeg 合并"]}]'
    "\n```"
)


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    def invoke(self, messages):
        class _R:
            content = self._content
        return _R()


@pytest.fixture()
def dm():
    import crawagent.web.routers.data_manage as dm
    return dm


def test_distill_and_archive_success(sf_env, db_env, dm, monkeypatch):
    """mock LLM 返回条目 → 直插入库 → 整夹移入 _archived。"""
    sf, tmp_path = sf_env
    _make_folder(sf, "sess_k", {
        "archive/壁纸站.md": "抓了一堆壁纸，图片在 CSS 里",
        "archive/b站.md": "下载了视频，DASH 流",
    })
    import crawagent.llm.model as lm
    monkeypatch.setattr(lm, "get_llm", lambda **kw: _FakeLLM(_GOOD_LLM_JSON))

    r = dm._distill_and_archive("sess_k")
    assert r["ok"] is True
    assert r["records"] == 2
    assert "_archived" in r["archived_to"]
    # 原文件夹没了，归档里有
    assert not (tmp_path / "data" / "sessions" / "sess_k").exists()
    # 条目入库且带来源标记
    st, qt, db = db_env
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT title, platform, session_id, url FROM crawl_records"
    ).fetchall()
    con.close()
    assert len(rows) == 2
    assert all(row[1] == "长期记忆" and row[2] == "sess_k" for row in rows)
    assert all(row[3] == "session://sess_k" for row in rows)
    assert any("壁纸" in row[0] for row in rows)
    # 正文带来源说明
    con = sqlite3.connect(str(db))
    body = con.execute("SELECT content FROM crawl_records LIMIT 1").fetchone()[0]
    con.close()
    assert "来源：会话 sess_k" in body


def test_distill_llm_failure_folder_untouched(sf_env, db_env, dm, monkeypatch):
    """蒸馏失败（LLM 错误）→ 文件夹保持原样，宁留勿丢。"""
    sf, tmp_path = sf_env
    _make_folder(sf, "sess_l", {"archive/a.md": "重要内容"})
    import crawagent.llm.model as lm

    class _BadLLM:
        def invoke(self, messages):
            raise RuntimeError("LLM timeout")
    monkeypatch.setattr(lm, "get_llm", lambda **kw: _BadLLM())

    r = dm._distill_and_archive("sess_l")
    assert r["ok"] is False
    assert "提炼失败" in r["error"]
    # 文件夹原封不动
    assert (tmp_path / "data" / "sessions" / "sess_l" / "archive" / "a.md").exists()
    # 库里没有半条（LLM 失败路径在 _init_db 前返回，先建表再计数）
    st, qt, db = db_env
    st._init_db()
    con = sqlite3.connect(str(db))
    n = con.execute("SELECT count(*) FROM crawl_records").fetchone()[0]
    con.close()
    assert n == 0


def test_distill_parse_garbage_folder_untouched(sf_env, db_env, dm, monkeypatch):
    """LLM 返回解析不出 JSON → 报提炼失败，文件夹不动。"""
    sf, tmp_path = sf_env
    _make_folder(sf, "sess_m", {"archive/a.md": "内容"})
    import crawagent.llm.model as lm
    monkeypatch.setattr(lm, "get_llm", lambda **kw: _FakeLLM("我觉得没法提炼，抱歉。"))
    r = dm._distill_and_archive("sess_m")
    assert r["ok"] is False
    assert (tmp_path / "data" / "sessions" / "sess_m" / "archive" / "a.md").exists()


def test_distill_empty_and_missing_folder(sf_env, db_env, dm):
    """空文件夹（无 md）/ 不存在的文件夹 → 人话报错，不调 LLM 不动手。"""
    sf, tmp_path = sf_env
    sf.ensure_session_folder("sess_n")  # 空夹
    r = dm._distill_and_archive("sess_n")
    assert r["ok"] is False and "没有可提炼" in r["error"]
    r2 = dm._distill_and_archive("sess_missing")
    assert r2["ok"] is False and "不存在" in r2["error"]
    r3 = dm._distill_and_archive("")
    assert r3["ok"] is False and "缺少会话" in r3["error"]


def test_distill_endpoint_registered():
    """路由注册：overview + distill 两端点在 openapi 里（_IncludedRouter 惰性包装，
    查注册必须拉 /openapi.json）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from crawagent.web.routers.data_manage import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/data/sessions/overview" in paths
    assert "/api/data/sessions/{sid}/distill" in paths


def test_overview_endpoint_marks_stale(sf_env, db_env, dm):
    """overview：mtime 超期 → stale true；标题从 meta.db 反查。"""
    sf, tmp_path = sf_env
    old = time.time() - 200 * 86400
    fresh_t = time.time()
    _make_folder(sf, "sess_old", {"archive/a.md": "旧"}, mtime=old)
    _make_folder(sf, "sess_new", {"archive/b.md": "新"}, mtime=fresh_t)
    # 标题反查（meta_store 打 tmp 库）
    import crawagent.storage.meta_store as ms

    class _MetaSettings:
        sessions_db_path = tmp_path / "sessions.db"

    ms.reset_meta_conn()
    try:
        ms.get_meta_conn(_MetaSettings())
        ms._meta_conn.execute(
            "INSERT INTO session_titles (thread_id, title) VALUES ('sess_old', '旧会话标题')"
        )
        ms._meta_conn.commit()
        rows = dm._overview_rows()
        by_sid = {r["sid"]: r for r in rows}
        assert by_sid["sess_old"]["stale"] is True
        assert by_sid["sess_old"]["title"] == "旧会话标题"
        assert by_sid["sess_new"]["stale"] is False
        assert by_sid["sess_old"]["size_bytes"] > 0
    finally:
        ms.reset_meta_conn()
