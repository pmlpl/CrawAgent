"""存储层测试 — checkpointer backend 切换 + meta_store 建表，全离线。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.storage.checkpointer import build_checkpointer
from crawagent.storage.meta_store import (
    get_meta_conn, reset_meta_conn, _resolve_db_path,
)


class _Settings:
    def __init__(self, checkpoint_backend="sqlite", sessions_db_path=None, redis_url=""):
        self.checkpoint_backend = checkpoint_backend
        self.sessions_db_path = sessions_db_path or Path("data/sessions.db")
        self.redis_url = redis_url


# ---- build_checkpointer ----

def test_build_checkpointer_sqlite(tmp_path):
    s = _Settings("sqlite", tmp_path / "sessions.db")
    saver = build_checkpointer(s)
    assert saver is not None
    assert (tmp_path / "sessions.db").parent.exists()


def test_build_checkpointer_default_is_sqlite(tmp_path):
    """checkpoint_backend 不设时默认 sqlite。"""
    s = _Settings(None, tmp_path / "sessions.db")
    assert build_checkpointer(s) is not None


def test_build_checkpointer_redis_no_url(tmp_path, monkeypatch):
    """redis 后端但没配 redis_url → ValueError。

    用 monkeypatch 塞假模块让 import 成功，以测到 url 校验分支
    （环境未装 langgraph-checkpoint-redis 时 import 会先失败）。
    """
    import sys as _sys
    import types
    fake_mod = types.ModuleType("langgraph.checkpoint.redis")
    fake_mod.RedisSaver = object  # 占位，url 为空前抛 ValueError，不会被调用
    monkeypatch.setitem(_sys.modules, "langgraph.checkpoint.redis", fake_mod)
    s = _Settings("redis", tmp_path / "sessions.db", redis_url="")
    with pytest.raises(ValueError, match="redis_url"):
        build_checkpointer(s)


def test_build_checkpointer_redis_missing_package(tmp_path, monkeypatch):
    """redis 后端但 langgraph.checkpoint.redis 不可 import → ImportError。"""
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "langgraph.checkpoint.redis", None)
    s = _Settings("redis", tmp_path / "sessions.db", redis_url="redis://localhost:6379")
    with pytest.raises(ImportError, match="langgraph-checkpoint-redis"):
        build_checkpointer(s)


# ---- meta_store ----

def test_meta_conn_creates_tables(tmp_path):
    reset_meta_conn()
    s = _Settings("sqlite", tmp_path / "sessions.db")
    conn = get_meta_conn(s)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "session_titles" in tables
    assert "session_errors" in tables
    conn2 = get_meta_conn(s)
    assert conn2 is conn  # 单例
    reset_meta_conn()


def test_meta_resolve_db_path(tmp_path):
    s = _Settings("sqlite", tmp_path / "sessions.db")
    assert _resolve_db_path(s).name == "meta.db"
    assert _resolve_db_path(s).parent == tmp_path


def test_meta_conn_crud_roundtrip(tmp_path):
    reset_meta_conn()
    s = _Settings("sqlite", tmp_path / "sessions.db")
    conn = get_meta_conn(s)
    conn.execute(
        "INSERT OR REPLACE INTO session_titles (thread_id, title) VALUES (?, ?)",
        ("t1", "测试标题"),
    )
    conn.commit()
    row = conn.execute("SELECT title FROM session_titles WHERE thread_id=?", ("t1",)).fetchone()
    assert row[0] == "测试标题"
    reset_meta_conn()
