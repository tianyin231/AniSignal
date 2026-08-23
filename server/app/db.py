"""Async engine / session 工厂。"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def _engine_kwargs(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        # 本地测试用：等待锁而不是立刻报 database is locked
        return {"connect_args": {"timeout": 30}}
    return {"pool_pre_ping": True, "pool_recycle": 3600, "pool_size": 5, "max_overflow": 5}


engine = create_async_engine(
    get_settings().database_url, **_engine_kwargs(get_settings().database_url)
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖。"""
    async with SessionLocal() as session:
        yield session
