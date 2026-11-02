"""登录用户管理自己的 AstrBot 专用 Token。"""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db import get_session
from app.models import BotClient, User
from app.services import bot_clients
from app.services.account_limits import quota_view
from app.services.custom_policy import user_limits

router = APIRouter(prefix="/api/account/bot-clients", dependencies=[Depends(get_current_user)])


class ClientBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("请填写接入名称")
        return value.strip()


async def owned_client(session, user_id, client_id):
    row = await session.scalar(select(BotClient).where(BotClient.id == client_id, BotClient.user_id == user_id))
    if not row:
        raise HTTPException(404, "接入不存在")
    return row


@router.get("")
async def list_clients(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await bot_clients.user_client_views(session, user.id)


@router.get("/quota")
async def client_quota(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await quota_view(session, user.id)


@router.post("")
async def create_client(body: ClientBody, response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await session.scalar(select(User).where(User.id == user.id).with_for_update())
    limit = (await user_limits(session, user.id))["max_bot_clients"]
    active = list(await session.scalars(select(BotClient.id).where(
        BotClient.user_id == user.id, BotClient.enabled.is_(True)).with_for_update()))
    if len(active) >= limit:
        raise HTTPException(409, f"当前账号最多保留 {limit} 个有效接入，请先撤销不用的 Token")
    row, token = await bot_clients.create_user_client(session, user, body.name)
    response.headers["Cache-Control"] = "no-store"
    return {"id": row.id, "name": row.name, "token": token}


@router.post("/{client_id}/rotate")
async def rotate_client(client_id: str, response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    row = await owned_client(session, user.id, client_id)
    if not row.enabled:
        raise HTTPException(409, "此 Token 已撤销，请创建新的接入")
    token = bot_clients.user_token(row)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"id": row.id, "name": row.name, "token": token}


@router.delete("/{client_id}")
async def revoke_client(client_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await bot_clients.revoke_user_client(session, await owned_client(session, user.id, client_id))
    return {"ok": True}
