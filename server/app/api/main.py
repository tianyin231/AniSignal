"""FastAPI 入口。"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.account import router as account_router
from app.api.admin import router as admin_router
from app.api.admin_ai import router as admin_ai_router
from app.api.admin_settings import router as admin_settings_router
from app.api.auth import router as auth_router
from app.api.public import router as public_router
from app.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    scheduler_task: asyncio.Task | None = None
    if settings.local_mode:
        # 本地测试模式：进程内队列 + 进程内调度循环（见 app/local.py 入口）
        from app.scheduler.loop import local_loop
        from app.services.queue import LocalQueue

        if getattr(app.state, "queue", None) is None:
            app.state.queue = LocalQueue()
        scheduler_task = asyncio.create_task(local_loop(app.state.queue))
        logger.info("local mode: in-process scheduler started")
    yield
    if scheduler_task is not None:
        scheduler_task.cancel()
    queue = getattr(app.state, "queue", None)
    if queue is not None:
        await queue.aclose()


app = FastAPI(title="AniSignal API", version="0.1.0", lifespan=lifespan)

# 跨域来源由 ANISIGNAL_CORS_ORIGINS 配置（本地 dev + 部署的 web 源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(public_router)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(admin_settings_router)
app.include_router(admin_ai_router)


@app.get("/api/healthz")
async def healthz():
    return {"status": "ok"}
