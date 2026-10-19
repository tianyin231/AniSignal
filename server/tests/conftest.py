"""测试基座：SQLite 内存库 + FastAPI ASGI 客户端。

注意：必须在导入 app 模块之前设置环境变量（Settings 有 lru_cache）。
"""

import os

os.environ.setdefault("ANISIGNAL_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("ANISIGNAL_DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("ANISIGNAL_REDIS_URL", "redis://localhost:6379/0")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    from tests.test_admin_api import _FakeQueue

    return _FakeQueue()


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
def _public_dns(monkeypatch, request):
    """出站请求的 DNS 复核默认不依赖真实解析。

    沙箱/CI 的网络会把所有外部域名解析到 198.18.0.0/15 保留段（`is_private=True`），
    真实解析会让每个走 SSRF 收敛的用例都被判内网而拒发，失败信息还很难懂。这里统一
    桩成公网 IP；需要真实 getaddrinfo 的用例（非点分 IP 字面量等绕过写法）打
    `@pytest.mark.real_dns` 跳过本桩。
    """
    if request.node.get_closest_marker("real_dns"):
        return
    from app.services import webhooks as webhooks_module

    async def _fake(host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    monkeypatch.setattr(webhooks_module, "_resolved_addresses", _fake)
