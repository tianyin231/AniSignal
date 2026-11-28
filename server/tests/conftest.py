"""测试基座：SQLite 内存模板快克隆 + FastAPI ASGI 客户端。

注意：必须在导入 app 模块之前设置环境变量（Settings 有 lru_cache）。
"""

import os
import sqlite3

os.environ.setdefault("ANISIGNAL_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("ANISIGNAL_DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("ANISIGNAL_REDIS_URL", "redis://localhost:6379/0")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base
from tests.factories import FakeQueue


@pytest.fixture(scope="session")
def _sqlite_schema_template():
    """整轮测试会话只执行一次全表建库，后续用例通过 sqlite3.backup 内存页克隆（~1ms/例且完全隔离）。"""
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    sync_engine = create_engine("sqlite://", creator=lambda: raw, poolclass=StaticPool)
    Base.metadata.create_all(sync_engine)
    yield raw
    sync_engine.dispose()
    raw.close()


@pytest_asyncio.fixture
async def engine(_sqlite_schema_template):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.connect() as conn:
        await conn.run_sync(
            lambda sync_conn: _sqlite_schema_template.backup(sync_conn.connection.driver_connection._conn)
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def fake_queue():
    return FakeQueue()


@pytest_asyncio.fixture
async def client(session_factory, fake_queue):
    from app.api import main as api_main

    async def _override():
        async with session_factory() as s:
            yield s

    api_main.app.dependency_overrides[get_session] = _override
    # 队列走 app.state 注入（get_queue 优先读它），测试用进程内桩
    api_main.app.state.queue = fake_queue
    transport = ASGITransport(app=api_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    api_main.app.dependency_overrides.clear()
    api_main.app.state.queue = None


@pytest.fixture
def admin_headers():
    return {"X-Admin-Token": "test-admin-token"}


@pytest.fixture(autouse=True)
def _reset_dynamic_communities():
    from app.communities import reset_communities
    from app.services import auth as auth_module

    reset_communities()
    auth_module._smtp_send_times.clear()
    yield
    reset_communities()
    auth_module._smtp_send_times.clear()
