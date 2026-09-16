"""LangGraph checkpointer 工厂 — 按 settings.checkpoint_backend 切换 sqlite/redis。

设计意图：
    - sqlite（默认）：单进程，行为与旧版完全一致（向后兼容）
    - redis：多 worker 共享会话状态，支持分布式
    - 导入 RedisSaver 放函数体内，未装 langgraph-checkpoint-redis 时 sqlite 模式不崩
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver

from crawagent.config.settings import get_settings

if TYPE_CHECKING:
    from crawagent.config.settings import Settings


def build_checkpointer(settings: Settings | None = None) -> BaseCheckpointSaver:
    """按 settings.checkpoint_backend 选择并初始化 checkpointer。

    Args:
        settings: 可选注入配置；为 None 时调 get_settings() 取全局单例。

    Returns:
        已调 .setup() 完成的 BaseCheckpointSaver 实例。

    Raises:
        ImportError: redis 后端但未装 langgraph-checkpoint-redis 时抛出，
                    异常消息会提示安装命令，方便用户排查。
    """
    if settings is None:
        settings = get_settings()

    backend = getattr(settings, "checkpoint_backend", "sqlite") or "sqlite"

    if backend == "redis":
        # 导入放函数体内：sqlite 模式不需要 redis 依赖也能跑
        try:
            from langgraph.checkpoint.redis import RedisSaver
        except ImportError as e:
            raise ImportError(
                "checkpoint_backend=redis 需要 langgraph-checkpoint-redis，"
                "请执行 `uv add langgraph-checkpoint-redis` 或 `pip install "
                "langgraph-checkpoint-redis` 后重试。原始错误: " + str(e)
            ) from e

        redis_url = getattr(settings, "redis_url", "") or ""
        if not redis_url:
            raise ValueError(
                "checkpoint_backend=redis 必须配置 redis_url（settings.redis_url）。"
            )
        saver = RedisSaver.from_conn_str(redis_url)  # type: ignore[attr-defined]
        saver.setup()
        return saver

    # 默认 sqlite：行为与旧版 get_checkpointer() 完全一致（向后兼容）
    settings.sessions_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(settings.sessions_db_path),
        check_same_thread=False,
    )
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
