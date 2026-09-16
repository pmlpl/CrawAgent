"""Redis 连接池 — 分布式模块共享的 Redis 客户端。

设计意图：
    - 全局单例 ConnectionPool，所有 dist 子模块通过 get_redis() 获取连接
    - _k() 统一 namespace 前缀 "crawagent:"，避免与其他项目冲突
    - decode_responses=True，所有返回值是 str 而非 bytes
"""
from __future__ import annotations

import redis as _redis
from crawagent.config.settings import get_settings

_PREFIX = "crawagent"
_pool: _redis.ConnectionPool | None = None


def _k(*parts: str) -> str:
    """生成带 namespace 前缀的 Redis key：crawagent:part1:part2"""
    return ":".join([_PREFIX, *parts]) if parts else _PREFIX


def get_redis() -> _redis.Redis:
    """获取共享 Redis 连接（惰性建池）。

    首次调用时从 settings.redis_url 建池，后续复用。
    decode_responses=True，返回值都是 str。
    """
    global _pool
    if _pool is None:
        settings = get_settings()
        url = settings.redis_url or "redis://127.0.0.1:6379/0"
        _pool = _redis.ConnectionPool.from_url(url, decode_responses=True)
    return _redis.Redis(connection_pool=_pool)


def ensure_redis() -> bool:
    """探活 Redis 连接是否可用。连不上返回 False。"""
    try:
        r = get_redis()
        r.ping()
        return True
    except Exception:
        return False
