"""会话元数据存储 — 标题/错误，恒走 SQLite，与 checkpointer 解耦。

设计意图：
    - 旧版 state.py 把 session_titles / session_errors 建在 checkpointer 同库同连接上，
      redis 后端切换后这两张表会跟着跑偏。本模块独立到 data/meta.db，
      无论 checkpointer 是 sqlite 还是 redis，元数据查询路径都不变。
    - schema 与旧版完全一致（thread_id / message / ts），方便 state.py 后续平移改造。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from crawagent.config.settings import get_settings

if TYPE_CHECKING:
    from crawagent.config.settings import Settings

# 模块级惰性单例：避免每次访问都新建连接（同 state.py 的 _checkpointer 风格）
_meta_conn: sqlite3.Connection | None = None


def _resolve_db_path(settings: Settings) -> Path:
    """meta.db 路径：与 sessions.db 同目录，命名独立避免混淆。"""
    return settings.sessions_db_path.parent / "meta.db"


def get_meta_conn(settings: Settings | None = None) -> sqlite3.Connection:
    """惰性创建 meta.db 连接，并按需建表。

    表结构（与旧版 state.py L84-95 完全一致）：
        session_titles (thread_id TEXT PRIMARY KEY, title TEXT NOT NULL)
        session_errors (thread_id TEXT PRIMARY KEY, message TEXT NOT NULL, ts TEXT NOT NULL)

    Args:
        settings: 可选注入配置；为 None 时调 get_settings() 取全局单例。

    Returns:
        已建表并 commit 过的 sqlite3.Connection，调用方共享同一连接。
    """
    global _meta_conn
    if _meta_conn is None:
        if settings is None:
            settings = get_settings()
        db_path = _resolve_db_path(settings)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _meta_conn = sqlite3.connect(str(db_path), check_same_thread=False)
        # 会话重命名：thread_id → 自定义标题
        _meta_conn.execute(
            "CREATE TABLE IF NOT EXISTS session_titles ("
            "thread_id TEXT PRIMARY KEY, title TEXT NOT NULL)"
        )
        # 会话最后一次轮次失败原因：刷新页面后前端仍能显示红条，直到下一轮成功
        _meta_conn.execute(
            "CREATE TABLE IF NOT EXISTS session_errors ("
            "thread_id TEXT PRIMARY KEY, message TEXT NOT NULL, ts TEXT NOT NULL)"
        )
        _meta_conn.commit()
    return _meta_conn


def reset_meta_conn() -> None:
    """关闭并清空单例连接（设置变更或测试隔离时调用）。"""
    global _meta_conn
    if _meta_conn is not None:
        try:
            _meta_conn.close()
        except Exception:
            pass
    _meta_conn = None
