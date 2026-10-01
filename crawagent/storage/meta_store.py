"""会话元数据存储 — 标题/错误，恒走 SQLite，与 checkpointer 解耦。

设计意图：
    - 旧版 state.py 把 session_titles / session_errors 建在 checkpointer 同库同连接上，
      redis 后端切换后这两张表会跟着跑偏。本模块独立到 data/meta.db，
      无论 checkpointer 是 sqlite 还是 redis，元数据查询路径都不变。
    - schema 与旧版完全一致（thread_id / message / ts），方便 state.py 后续平移改造。
"""
from __future__ import annotations

import datetime
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
        _migrate_meta_columns(_meta_conn)
        _meta_conn.commit()
    return _meta_conn


def _migrate_meta_columns(conn: sqlite3.Connection) -> None:
    """兼容迁移：给老库 session_titles 补 work_dir / work_dir_ts 列。

    - work_dir（034 会话工作文件夹）：TEXT DEFAULT ''
    - work_dir_ts（037 历史项目排序）：每次 set_work_dir 刷 ISO 时间戳，
      供「切换项目」菜单按最近使用排序（rowid 在 conflict-update 时不变，
      无法反映「同会话换项目」后的真实时序）。

    SQLite ADD COLUMN 无损；并发首启另一线程可能已完成迁移，duplicate column 直接忽略。
    """
    cursor = conn.execute("PRAGMA table_info(session_titles)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "work_dir" not in existing_cols:
        try:
            conn.execute("ALTER TABLE session_titles ADD COLUMN work_dir TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    if "work_dir_ts" not in existing_cols:
        try:
            conn.execute("ALTER TABLE session_titles ADD COLUMN work_dir_ts TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    _migrate_work_dir_history(conn)


def _migrate_work_dir_history(conn: sqlite3.Connection) -> None:
    """建 work_dir_history 绑定历史表 + 老库回填（037 缺陷修复）。

    缺陷：历史列表原先查 session_titles 的 work_dir 列——那是「各会话当前
    绑定」的快照，会话换绑会覆盖旧值，旧路径若无人再持有就从历史里蒸发。
    修复：append-only 历史表，set_work_dir 每次非空绑定都记录；回填把
    老库现存快照灌入（已被覆盖掉的路径无法追溯，属既成事实）。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS work_dir_history ("
        "path TEXT PRIMARY KEY, last_used TEXT NOT NULL)"
    )
    try:
        conn.execute(
            "INSERT OR IGNORE INTO work_dir_history (path, last_used) "
            "SELECT work_dir, MAX(work_dir_ts) FROM session_titles "
            "WHERE work_dir != '' GROUP BY work_dir"
        )
    except sqlite3.OperationalError:
        pass  # 老库缺 work_dir_ts 列时上面两个 ALTER 已补，理论不到这里


def get_session_title(thread_id: str, settings: Settings | None = None) -> str:
    """取会话标题；无记录或查询失败返回空串（调用方自行兜底，不抛异常）。"""
    try:
        conn = get_meta_conn(settings)
        row = conn.execute(
            "SELECT title FROM session_titles WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return row[0] if row else ""
    except Exception:
        return ""


def get_work_dir(thread_id: str, settings: Settings | None = None) -> str:
    """取会话工作文件夹（变更 034）；无记录 / 空 / 查询失败返回空串。

    空串 = 未配置，工具层走 014 默认落点（output/<会话名>/ + downloads/<分类>/）。
    """
    try:
        conn = get_meta_conn(settings)
        row = conn.execute(
            "SELECT work_dir FROM session_titles WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return (row[0] or "") if row else ""
    except Exception:
        return ""


def set_work_dir(thread_id: str, work_dir: str, settings: Settings | None = None) -> str:
    """设置会话工作文件夹：abspath 规范化 + 不存在自动创建后入库。

    返回规范化后的绝对路径；目录创建失败抛 OSError（调用方决定是否兜底）。
    空 work_dir 视为清除（落回空串，不动绑定历史）。非空绑定时同步把路径
    记入 work_dir_history（append-only，供「切换项目」菜单历史列表）。
    """
    cleaned = (work_dir or "").strip()
    conn = get_meta_conn(settings)
    if cleaned:
        import os
        cleaned = os.path.abspath(cleaned)
        os.makedirs(cleaned, exist_ok=True)
    ts = datetime.datetime.now().isoformat()
    conn.execute(
        "INSERT INTO session_titles (thread_id, title, work_dir, work_dir_ts) "
        "VALUES (?, '', ?, ?) "
        "ON CONFLICT(thread_id) DO UPDATE SET work_dir = excluded.work_dir, "
        "work_dir_ts = excluded.work_dir_ts",
        (thread_id, cleaned, ts),
    )
    if cleaned:
        # 绑定历史 append-only：换绑不丢旧路径（清除绑定 ≠ 没打开过，不清历史）
        conn.execute(
            "INSERT INTO work_dir_history (path, last_used) VALUES (?, ?) "
            "ON CONFLICT(path) DO UPDATE SET last_used = excluded.last_used",
            (cleaned, ts),
        )
    conn.commit()
    return cleaned


def list_work_dir_history(settings: Settings | None = None) -> list[str]:
    """最近打开过的 work_dir（按最近打开倒序，最多 10 个，变更 037）。

    供聊天页提示条「切换项目」菜单展示历史项。数据源 work_dir_history
    （append-only 绑定历史）——会话换绑只覆盖 session_titles 当前绑定列，
    历史表不删旧路径，换绑后旧项目仍在列表里。查询异常静默返回空列表
    （菜单降级为「打开新项目 / 清除项目」两项）。
    """
    try:
        conn = get_meta_conn(settings)
        rows = conn.execute(
            "SELECT path FROM work_dir_history ORDER BY last_used DESC LIMIT 10"
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def reset_meta_conn() -> None:
    """关闭并清空单例连接（设置变更或测试隔离时调用）。"""
    global _meta_conn
    if _meta_conn is not None:
        try:
            _meta_conn.close()
        except Exception:
            pass
    _meta_conn = None
