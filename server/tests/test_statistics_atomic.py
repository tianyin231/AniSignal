"""多个来源同时建立统计行时不冲突，也不丢失累加。"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import utcnow
from app.models import ContentDailyStat
from app.services.statistics import record_content_stat


async def test_content_statistics_accumulate_from_concurrent_sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'stats.sqlite').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(ContentDailyStat.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = utcnow()

    async def record_account(account):
        async with sessions() as session:
            for _ in range(10):
                await record_content_stat(session, collected_at=now, platform="x", identity="official",
                                          keywords=["AI", account], include_base=True)
            await session.commit()

    try:
        await asyncio.gather(record_account("OpenAI"), record_account("DeepSeek"))
        async with sessions() as session:
            rows = {row.keyword: row.contents_new for row in await session.scalars(select(ContentDailyStat))}
            assert rows == {"": 20, "AI": 20, "OpenAI": 10, "DeepSeek": 10}
    finally:
        await engine.dispose()
