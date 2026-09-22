"""会话列表只读视图抽象 — 按 checkpointer 后端返回对应实现。

设计意图：
    - 旧版 sessions.py L277-281 直接 SQL 查 checkpoints 表，耦合 sqlite 后端。
    - 切 redis 后端后没有 checkpoints 表，需要走 Redis SMEMBERS / HGETALL 路径。
    - 抽象成 Protocol 后，调用方只拿 list_thread_ids()，不感知后端差异。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from crawagent.config.settings import get_settings

if TYPE_CHECKING:
    from crawagent.config.settings import Settings


@runtime_checkable
class CheckpointView(Protocol):
    """会话列表只读视图接口。

    list_thread_ids 返回三元组列表：
        [(thread_id, latest_rowid, ckpt_size), ...]
        - thread_id：会话 ID（即 LangGraph thread_id）
        - latest_rowid：该会话最新 checkpoint 的 rowid（用作排序键，越大越新）
        - ckpt_size：该会话最大 checkpoint 字节长度（前端用作"重量"指示）
    """

    def list_thread_ids(self, limit: int = 20) -> list[tuple[str, int, int]]:
        """返回最近活跃的会话列表，按 latest_rowid 倒序，最多 limit 条。"""
        ...


class SqliteCheckpointView:
    """sqlite 后端视图：直接查 checkpoints 表。

    SQL 与旧版 sessions.py L277-281 完全一致（向后兼容）：
        SELECT thread_id, MAX(rowid) AS latest, MAX(LENGTH(checkpoint)) AS ckpt_size
        FROM checkpoints GROUP BY thread_id ORDER BY latest DESC LIMIT ?
    """

    def __init__(self, conn):
        # 接受 sqlite3.Connection 或任何暴露 execute 的对象
        self._conn = conn

    def list_thread_ids(self, limit: int = 20) -> list[tuple[str, int, int]]:
        """列出最近 ``limit`` 个 thread_id，按最近写入时间倒序。

        Args:
            limit: 返回条数上限。

        Returns:
            ``(thread_id, latest_rowid, ckpt_size)`` 三元组列表；表不存在时返回 ``[]``。
        """
        try:
            rows = self._conn.execute(
                "SELECT thread_id, MAX(rowid) AS latest, "
                "MAX(LENGTH(checkpoint)) AS ckpt_size FROM checkpoints "
                "GROUP BY thread_id ORDER BY latest DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except Exception:
            # 表不存在或其它异常：返回空，调用方按"无会话"处理
            return []
        # sqlite 返回的是 tuple，无需转换
        return [(str(r[0]), int(r[1] or 0), int(r[2] or 0)) for r in rows]


class RedisCheckpointView:
    """redis 后端视图：SMEMBERS 索引 + HGETALL 元数据。

    约定 key 结构（与 RedisSaver 配合）：
        - crawagent:sessions：Set，member 为 thread_id
        - crawagent:session:{thread_id}：Hash，字段含 latest_rowid / ckpt_size
    """

    SESSIONS_KEY = "crawagent:sessions"

    def __init__(self, client) -> None:
        # 接受 redis.Redis 实例或任何暴露 smembers/hgetall 的客户端
        self._client = client

    def list_thread_ids(self, limit: int = 20) -> list[tuple[str, int, int]]:
        """列出最近 ``limit`` 个 thread_id（按 Redis 元数据 ``latest_rowid`` 倒序）。

        Args:
            limit: 返回条数上限。

        Returns:
            ``(thread_id, latest_rowid, ckpt_size)`` 三元组列表；连接失败时返回 ``[]``。
        """
        try:
            thread_ids = self._client.smembers(self.SESSIONS_KEY)
        except Exception:
            return []

        results: list[tuple[str, int, int]] = []
        for tid in thread_ids:
            # redis 返回 bytes，统一解码为 str
            tid_str = tid.decode() if isinstance(tid, (bytes, bytearray)) else str(tid)
            try:
                meta = self._client.hgetall(f"crawagent:session:{tid_str}")
            except Exception:
                meta = {}

            def _get(key: str) -> int:
                v = meta.get(key) or meta.get(key.encode())
                if v is None:
                    return 0
                if isinstance(v, (bytes, bytearray)):
                    v = v.decode()
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return 0

            results.append((tid_str, _get("latest_rowid"), _get("ckpt_size")))

        # 按 latest_rowid 倒序，截断 limit
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]


def get_checkpoint_view(settings: Settings | None = None) -> CheckpointView:
    """按 settings.checkpoint_backend 返回对应 CheckpointView 实现。

    Args:
        settings: 可选注入配置；为 None 时调 get_settings() 取全局单例。

    Returns:
        CheckpointView 实例。sqlite 后端从 state.get_checkpointer() 复用现有连接，
        避免重复打开 sessions.db；redis 后端惰性建 client，导入失败回退 sqlite 视图。
    """
    if settings is None:
        settings = get_settings()

    backend = getattr(settings, "checkpoint_backend", "sqlite") or "sqlite"

    if backend == "redis":
        try:
            # 导入放函数体内：未装 redis 包时 sqlite 模式不崩
            import redis  # type: ignore
        except ImportError:
            # redis 未装：回退 sqlite 视图（兜底，避免崩掉会话列表接口）
            from crawagent.web.state import get_checkpointer
            return SqliteCheckpointView(get_checkpointer().conn)

        redis_url = getattr(settings, "redis_url", "") or ""
        if not redis_url:
            from crawagent.web.state import get_checkpointer
            return SqliteCheckpointView(get_checkpointer().conn)

        client = redis.from_url(redis_url)  # type: ignore[call-arg]
        return RedisCheckpointView(client)  # type: ignore[return-value]

    # 默认 sqlite：复用 state.py 的 checkpointer 连接，避免重复打开 sessions.db
    from crawagent.web.state import get_checkpointer
    return SqliteCheckpointView(get_checkpointer().conn)
