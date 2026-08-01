"""CrawAgent 数据库管理模块

使用 SQLAlchemy 2.0 async + aiomysql，支持 MySQL（生产）和 SQLite（开发/测试）。
提供 DatabaseManager 管理 AsyncEngine 和 session 生命周期，
以及 Base 供其他 ORM 模型继承。
"""
from __future__ import annotations

from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from loguru import logger

from crawagent.config.settings import get_settings


class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类"""
    pass


class DatabaseManager:
    """数据库管理器

    管理 SQLAlchemy AsyncEngine 和 session 生命周期。
    支持 MySQL（生产）和 SQLite（开发/测试）。
    """

    def __init__(self, dsn: Optional[str] = None, echo: bool = False):
        """
        Args:
            dsn: 数据库连接字符串，默认从 settings.mysql_dsn 获取
            echo: 是否输出 SQL 日志
        """
        self._dsn = dsn
        self._echo = echo
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    async def initialize(self) -> None:
        """初始化数据库引擎和表结构"""
        settings = get_settings()
        dsn = self._dsn or settings.mysql_dsn
        echo = self._echo or settings.mysql_echo

        self._engine = create_async_engine(
            dsn,
            echo=echo,
            pool_size=settings.mysql_pool_size,
            pool_pre_ping=True,
            pool_recycle=3600,
        )

        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # 创建所有表（harness 的表由 SessionManager 创建）
        async with self._engine.begin() as conn:
            # 只创建 Base 下注册的表
            await conn.run_sync(Base.metadata.create_all)

        logger.info(f"Database initialized: {dsn.split('@')[-1] if '@' in dsn else dsn}")

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._engine

    def get_session(self) -> AsyncSession:
        """获取数据库 session"""
        if self._session_factory is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._session_factory()

    async def get_session_ctx(self) -> AsyncGenerator[AsyncSession, None]:
        """获取数据库 session（async context manager 风格）"""
        session = self.get_session()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def close(self) -> None:
        """关闭引擎"""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("Database closed")


# 单例
_db_manager: Optional[DatabaseManager] = None


async def get_db() -> DatabaseManager:
    """获取数据库管理器单例"""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
        await _db_manager.initialize()
    return _db_manager


async def close_db() -> None:
    """关闭数据库"""
    global _db_manager
    if _db_manager:
        await _db_manager.close()
        _db_manager = None
