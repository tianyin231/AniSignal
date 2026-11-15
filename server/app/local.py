"""本地测试入口：`uv run python -m app.local`

零依赖跑通面板全链路（不需要 Docker / MySQL / Redis）：
- SQLite（./server/local.db）自动建表；
- 采集任务在当前进程内执行（LocalQueue），调度循环由 API lifespan 启动；
- 与正式部署一样从空库开始，在管理看板导入需要的预设信源；
- B 站采集需安装 bili CLI 并放入 PATH，或设置 ANISIGNAL_BILI_BIN。

启动后打开 web（npm run dev）访问 http://localhost:3000/admin，
Token 默认 local-dev（可用 ANISIGNAL_ADMIN_TOKEN 覆盖）。
"""

import asyncio
import os

# 必须在导入任何 app 模块之前设置（Settings 有 lru_cache，engine 在模块级创建）
os.environ.setdefault("ANISIGNAL_LOCAL_MODE", "1")
os.environ.setdefault("ANISIGNAL_DATABASE_URL", "sqlite+aiosqlite:///./local.db")
os.environ.setdefault("ANISIGNAL_ADMIN_TOKEN", "local-dev")
os.environ.setdefault("ANISIGNAL_NEWS_UNIFIED_ENABLED", "true")

import uvicorn  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import Base  # noqa: E402


async def init_db() -> None:
    async with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            # 浏览页面的读事务不应阻塞后台采集写入。
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(Base.metadata.create_all)


def main() -> None:
    settings = get_settings()
    asyncio.run(init_db())
    print("=" * 56)
    print("AniSignal 本地测试模式")
    print(f"  API       http://127.0.0.1:8000  (docs: /docs)")
    print(f"  Admin     http://localhost:3000/admin  (web: npm run dev)")
    print(f"  Token     {settings.admin_token}")
    print(f"  数据库    {settings.database_url}")
    print("=" * 56)
    uvicorn.run("app.api.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
