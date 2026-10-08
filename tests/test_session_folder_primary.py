"""046 会话存储文件主盘化测试。

覆盖规格 §五.1-5：
  1. 会话创建建 conversation.md（契约）+ 轮落盘（worker join 后语义用单元模拟）；
  2. 蒸馏守卫（活跃态拒绝 + 输入集排除 conversation.md）；
  3. 清理脚本（留最近 N + 兜底校验 + VACUUM 体积降）；
  4. 迁移脚本（_messages_to_turns / _turn_to_snapshot / _export_session 原子性）。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(autouse=True)
def _iso_meta(monkeypatch, tmp_path):
    """补充 conftest：meta_store 也隔离到 tmp_path + 重置单例连接。"""
    import crawagent.storage.meta_store as ms

    class _S:
        project_root = tmp_path
        sessions_db_path = tmp_path / "sessions.db"

    monkeypatch.setattr(ms, "_meta_conn", None)
    monkeypatch.setattr(ms, "get_settings", lambda: _S())
    yield


def _fake_settings(tmp_path):
    return SimpleNamespace(project_root=tmp_path, sessions_db_path=tmp_path / "sessions.db")


# ---------- §五.1 契约：会话创建建 conversation.md + 轮落盘 ----------

def test_init_conversation_md_creates_header(tmp_path):
    from crawagent.tools.session_folder import init_conversation_md

    path = init_conversation_md("s1", _fake_settings(tmp_path))
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "会话 s1" in text
    assert "创建时间" in text
    assert text.endswith("---\n\n")  # 头后空正文待追加


def test_init_conversation_md_idempotent(tmp_path):
    from crawagent.tools.session_folder import init_conversation_md

    fake = _fake_settings(tmp_path)
    path1 = init_conversation_md("s1", fake)
    content1 = path1.read_text(encoding="utf-8")
    # 第二次调：已存在，不重写头
    path2 = init_conversation_md("s1", fake)
    assert path1 == path2
    assert path2.read_text(encoding="utf-8") == content1


def test_write_turn_snapshot_appends_user_tools_ai(tmp_path):
    from crawagent.tools.session_folder import init_conversation_md, write_turn_snapshot

    fake = _fake_settings(tmp_path)
    init_conversation_md("s1", fake)
    turn = {
        "turn_no": 1,
        "user_text": "帮我爬一下豆瓣",
        "tool_calls": [{"name": "fetch_page", "args": '{"url":"douban.com"}', "result": "OK 200"}],
        "ai_text": "已爬取豆瓣首页",
    }
    path = write_turn_snapshot("s1", turn, fake)
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "## 轮次 1" in text
    assert "帮我爬一下豆瓣" in text
    assert "fetch_page" in text
    assert "douban.com" in text
    assert "OK 200" in text
    assert "已爬取豆瓣首页" in text


def test_write_turn_snapshot_skip_when_folder_missing(tmp_path):
    from crawagent.tools.session_folder import write_turn_snapshot

    # 文件夹不存在（被 039 archive 移走）→ 静默 skip 不抛
    result = write_turn_snapshot("ghost", {"turn_no": 1, "user_text": "x", "tool_calls": [], "ai_text": "y"},
                                  _fake_settings(tmp_path))
    assert result is None


def test_write_turn_snapshot_inits_missing_md(tmp_path):
    """conversation.md 被删（但文件夹在）→ write 兜底 init 后追加。"""
    from crawagent.tools.session_folder import ensure_session_folder, write_turn_snapshot

    fake = _fake_settings(tmp_path)
    ensure_session_folder("s1", fake)  # 只建文件夹，不建 conversation.md
    turn = {"turn_no": 1, "user_text": "hi", "tool_calls": [], "ai_text": "hello"}
    path = write_turn_snapshot("s1", turn, fake)
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "会话 s1" in text  # 兜底 init 写了头
    assert "hi" in text
    assert "hello" in text


def test_engine_write_turn_snapshot_from_messages(tmp_path, monkeypatch):
    """turn_engine._write_turn_snapshot 从 mock agent.get_state 的 messages 落盘。"""
    from crawagent.web.turn_engine import _write_turn_snapshot
    from crawagent.tools.session_folder import init_conversation_md

    fake = _fake_settings(tmp_path)
    monkeypatch.setattr("crawagent.tools.session_folder.get_settings", lambda: fake)
    # 模拟 chat_ws 已建文件夹（真实流程：ws.accept 后 init，轮末 _write_turn_snapshot 直接写）
    init_conversation_md("s1", fake)

    messages = [
        HumanMessage(content="爬豆瓣", id="h1"),
        AIMessage(content="", tool_calls=[{"id": "tc1", "name": "fetch_page", "args": {"url": "douban.com"}}], id="a1"),
        ToolMessage(content="OK 200", tool_call_id="tc1", id="t1"),
        AIMessage(content="已爬取豆瓣首页", id="a2"),
    ]
    agent = SimpleNamespace(get_state=lambda c: SimpleNamespace(values={"messages": messages}))
    _write_turn_snapshot("s1", agent, {}, "爬豆瓣", set(), 1)
    from crawagent.tools.session_folder import conversation_md_path

    text = conversation_md_path("s1", fake).read_text(encoding="utf-8")
    assert "爬豆瓣" in text
    assert "fetch_page" in text
    assert "douban.com" in text
    assert "OK 200" in text
    assert "已爬取豆瓣首页" in text


# ---------- §五.5 蒸馏守卫 ----------

def test_distill_guard_rejects_active(tmp_path):
    """活跃会话（<24h）触发蒸馏被拒。"""
    from crawagent.tools.session_folder import init_conversation_md, write_turn_snapshot
    from crawagent.web.routers.data_manage import _distill_and_archive

    init_conversation_md("s1", _fake_settings(tmp_path))  # conversation.md 刚写，mtime=now
    write_turn_snapshot("s1", {"turn_no": 1, "user_text": "hi", "tool_calls": [], "ai_text": "hello"},
                        _fake_settings(tmp_path))
    result = _distill_and_archive("s1")
    assert result["ok"] is False
    assert "活跃" in result["error"]


def test_distill_input_excludes_conversation_md(tmp_path, monkeypatch):
    """蒸馏输入集排除 conversation.md（只读 archive/*.md）。"""
    from crawagent.tools.session_folder import ensure_session_folder, init_conversation_md, write_turn_snapshot
    from crawagent.web.routers.data_manage import _distill_and_archive

    fake = _fake_settings(tmp_path)
    ensure_session_folder("s1", fake)
    init_conversation_md("s1", fake)
    # conversation.md 写「轮次原始日志」（不应进蒸馏素材）
    write_turn_snapshot("s1", {"turn_no": 1, "user_text": "轮次原始日志内容", "tool_calls": [], "ai_text": "raw"},
                        fake)
    # archive/a.md 写「档案内容」（应进蒸馏素材）
    (fake.project_root / "data" / "sessions" / "s1" / "archive").mkdir(parents=True, exist_ok=True)
    (fake.project_root / "data" / "sessions" / "s1" / "archive" / "a.md").write_text(
        "档案内容：豆瓣爬取要点", encoding="utf-8")
    # 让会话不活跃（mtime 设为 25h 前，过 24h 阈值）
    old_ts = time.time() - 25 * 3600
    conv_md = fake.project_root / "data" / "sessions" / "s1" / "conversation.md"
    os.utime(conv_md, (old_ts, old_ts))

    captured = []

    def mock_invoke(args):
        captured.append(args)
        return SimpleNamespace(content="[]")  # 空数组 → 蒸馏结果为空，不入库

    mock_llm = SimpleNamespace(invoke=mock_invoke)
    monkeypatch.setattr("crawagent.llm.model.get_llm", lambda **k: mock_llm)

    _distill_and_archive("s1")
    assert captured, "蒸馏应至少调一次 LLM"
    prompt_text = str(captured[0])
    assert "轮次原始日志内容" not in prompt_text, "conversation.md 的原始轮次日志不应进蒸馏素材"
    assert "档案内容" in prompt_text, "archive/*.md 的档案内容应进蒸馏素材"


# ---------- §五.3 清理脚本 ----------


def test_cleanup_keeps_recent_n(tmp_path):
    from scripts.cleanup_checkpointer import run_cleanup

    db_path = tmp_path / "sessions.db"
    sessions_root = tmp_path / "data" / "sessions"
    sessions_root.mkdir(parents=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT, checkpoint BLOB)")
    conn.execute("CREATE TABLE writes (thread_id TEXT, data BLOB)")
    # 会话 s1：15 轮，建 conversation.md（兜底校验通过）
    for i in range(15):
        conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES (?, ?, ?)",
                     ("s1", f"ckpt-{i}", b"x"))
        conn.execute("INSERT INTO writes (thread_id, data) VALUES (?, ?)", ("s1", b"y"))
    conn.commit()
    conn.close()
    (sessions_root / "s1" / "conversation.md").parent.mkdir(parents=True, exist_ok=True)
    (sessions_root / "s1" / "conversation.md").write_text("# 会话 s1\n\n## 轮次 1\n\n", encoding="utf-8")

    size_before = db_path.stat().st_size
    result = run_cleanup(db_path, sessions_root, keep=10, auto_yes=True, print_fn=lambda *a: None)
    assert result["deleted"] == 5
    assert result["vacuum_ok"] is True
    assert result["size_after"] <= size_before

    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='s1'").fetchone()[0]
    conn.close()
    assert n == 10


def test_cleanup_skips_no_md_sessions(tmp_path):
    """无 conversation.md 的会话不被清（兜底校验：防丢内容）。"""
    from scripts.cleanup_checkpointer import run_cleanup

    db_path = tmp_path / "sessions.db"
    sessions_root = tmp_path / "data" / "sessions"
    sessions_root.mkdir(parents=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT, checkpoint BLOB)")
    conn.execute("CREATE TABLE writes (thread_id TEXT, data BLOB)")
    for i in range(15):
        conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES (?, ?, ?)",
                     ("nomd", f"ckpt-{i}", b"x"))
    conn.commit()
    conn.close()
    # 不建 conversation.md（s1 的文件夹不存在）

    result = run_cleanup(db_path, sessions_root, keep=10, auto_yes=True, print_fn=lambda *a: None)
    assert result["deleted"] == 0
    assert "nomd" in result["skipped_no_md"]

    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='nomd'").fetchone()[0]
    conn.close()
    assert n == 15  # 未清


# ---------- §五.4 迁移脚本 ----------

def test_messages_to_turns_splits_by_human():
    from scripts.migrate_checkpointer_to_files import _messages_to_turns

    msgs = [
        HumanMessage(content="hi", id="h1"),
        AIMessage(content="", tool_calls=[], id="a1"),
        AIMessage(content="hello", id="a2"),
        HumanMessage(content="second", id="h2"),
        AIMessage(content="reply2", id="a3"),
    ]
    turns = _messages_to_turns(msgs)
    assert len(turns) == 2
    assert turns[0][0].content == "hi"
    assert turns[1][0].content == "second"


def test_turn_to_snapshot_pairs_tool_calls():
    from scripts.migrate_checkpointer_to_files import _turn_to_snapshot

    turn = [
        HumanMessage(content="爬", id="h1"),
        AIMessage(content="", tool_calls=[{"id": "tc1", "name": "fetch", "args": {"u": "x"}}], id="a1"),
        ToolMessage(content="res1", tool_call_id="tc1", id="t1"),
        AIMessage(content="done", id="a2"),
    ]
    snap = _turn_to_snapshot(turn, 1)
    assert snap["turn_no"] == 1
    assert snap["user_text"] == "爬"
    assert len(snap["tool_calls"]) == 1
    assert snap["tool_calls"][0]["name"] == "fetch"
    assert snap["tool_calls"][0]["result"] == "res1"
    assert snap["ai_text"] == "done"


def test_export_session_ok_with_mock_saver(tmp_path):
    from scripts.migrate_checkpointer_to_files import _export_session

    fake = _fake_settings(tmp_path)
    messages = [
        HumanMessage(content="hi", id="h1"),
        AIMessage(content="hello", id="a1"),
    ]
    saver = SimpleNamespace(list=lambda config, limit=1: [
        SimpleNamespace(checkpoint={"channel_values": {"messages": messages}}),
    ])
    r = _export_session(saver, "s1", fake, 10)
    assert r["status"] == "ok"
    assert r["turns"] == 1
    from crawagent.tools.session_folder import conversation_md_path

    text = conversation_md_path("s1", fake).read_text(encoding="utf-8")
    assert "hi" in text
    assert "hello" in text


def test_export_session_failed_keeps_reason(tmp_path):
    from scripts.migrate_checkpointer_to_files import _export_session

    fake = _fake_settings(tmp_path)
    # saver.list 抛异常 → status=failed（state 由调用方保留不清）
    saver = SimpleNamespace(list=lambda config, limit=1: (_ for _ in ()).throw(RuntimeError("boom")))
    r = _export_session(saver, "s1", fake, 10)
    assert r["status"] == "failed"
    assert "list 失败" in r["reason"]


def test_export_session_skips_existing_md(tmp_path):
    """conversation.md 已存在 → 跳过该会话（不合并）。"""
    from scripts.migrate_checkpointer_to_files import _export_session
    from crawagent.tools.session_folder import init_conversation_md

    fake = _fake_settings(tmp_path)
    init_conversation_md("s1", fake)  # 已建 conversation.md
    saver = SimpleNamespace(list=lambda config, limit=1: [])
    r = _export_session(saver, "s1", fake, 10)
    assert r["status"] == "skipped_existing"


def test_run_migrate_per_session_atomic(tmp_path):
    """run_migrate：失败会话 state 不清（per-session 原子性）。"""
    from scripts.migrate_checkpointer_to_files import run_migrate, _default_saver_factory

    fake = _fake_settings(tmp_path)
    db_path = fake.sessions_db_path
    sessions_root = fake.project_root / "data" / "sessions"

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT, checkpoint BLOB)")
    conn.execute("CREATE TABLE writes (thread_id TEXT, data BLOB)")
    # s_ok：会导出成功；s_bad：saver.list 抛 → failed
    for i in range(3):
        conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES (?, ?, ?)",
                     ("s_ok", f"c{i}", b"x"))
        conn.execute("INSERT INTO writes (thread_id, data) VALUES (?, ?)", ("s_ok", b"y"))
    for i in range(3):
        conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES (?, ?, ?)",
                     ("s_bad", f"c{i}", b"x"))
        conn.execute("INSERT INTO writes (thread_id, data) VALUES (?, ?)", ("s_bad", b"y"))
    conn.commit()
    conn.close()

    # mock saver：s_ok 返回预设 messages，s_bad 抛
    messages_ok = [HumanMessage(content="hi", id="h1"), AIMessage(content="hello", id="a1")]

    def list_fn(config, limit=1):
        tid = config["configurable"]["thread_id"]
        if tid == "s_bad":
            raise RuntimeError("boom")
        return [SimpleNamespace(checkpoint={"channel_values": {"messages": messages_ok}})]

    saver = SimpleNamespace(list=list_fn)

    result = run_migrate(db_path, fake, keep=10, auto_yes=True,
                         print_fn=lambda *a: None, saver_factory=lambda conn: saver)
    assert result["ok"] == 1  # s_ok 成功
    assert result["failed"] == 1  # s_bad 失败

    # s_bad state 原样保留（3 行）
    conn = sqlite3.connect(str(db_path))
    n_bad = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='s_bad'").fetchone()[0]
    n_ok = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='s_ok'").fetchone()[0]
    conn.close()
    assert n_bad == 3, "失败会话 state 应原样保留"
    assert n_ok == 3, "成功会话留最近 keep=10 轮（3<10 全留）"
