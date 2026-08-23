"""本地测试入口：`uv run python -m app.local`

零依赖跑通面板全链路（不需要 Docker / MySQL / Redis）：
- SQLite（./server/local.db）自动建表；
- 采集任务在当前进程内执行（LocalQueue），调度循环由 API lifespan 启动；
- 首次启动播种两个演示目标：
  * 鸣潮官网公告（kuro_cn，真实公开 CDN 数据，开箱即采）
  * B 站关键词搜索（本机没有 Linux 版 bili 二进制，默认停用；
    手动触发会失败并体现在健康状态里——这也是面板功能的一部分）

启动后打开 web（npm run dev）访问 http://localhost:3000/admin，
Token 默认 local-dev（可用 ANISIGNAL_ADMIN_TOKEN 覆盖）。
"""

import asyncio
import os

# 必须在导入任何 app 模块之前设置（Settings 有 lru_cache，engine 在模块级创建）
os.environ.setdefault("ANISIGNAL_LOCAL_MODE", "1")
os.environ.setdefault("ANISIGNAL_DATABASE_URL", "sqlite+aiosqlite:///./local.db")
os.environ.setdefault("ANISIGNAL_ADMIN_TOKEN", "local-dev")

import uvicorn  # noqa: E402

from app.clock import utcnow  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base, CollectTarget  # noqa: E402

SEED_TARGETS = [
    dict(
        platform="kuro_cn",
        target_type="notices",
        target_value="default",
        display_name="鸣潮官网公告（国服·真实数据）",
        interval_minutes=30,
        enabled=True,
    ),
    dict(
        platform="bilibili",
        target_type="keyword_search",
        target_value="鸣潮",
        display_name="B站搜索·鸣潮（需 Docker 内运行）",
        interval_minutes=60,
        enabled=False,
    ),
]


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy import func, select

    async with SessionLocal() as session:
        count = await session.scalar(select(func.count(CollectTarget.id)))
        if not count:
            session.add_all(
                [CollectTarget(**spec, next_run_at=utcnow()) for spec in SEED_TARGETS]
            )
            await session.commit()
            print("已播种演示目标：鸣潮官网公告（启用）+ B站搜索·鸣潮（停用）")


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
