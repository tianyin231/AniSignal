"""资讯编辑调用：先预约、先存回复，再处理业务；重启复用已收到的结果。"""

import hashlib
import json
import time
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.clock import utcnow
from app.models import AiUsage, EditorialReceipt, SiteSetting
from app.services.ai import ai_available, chat, get_ai_config
from app.services.settings import get_setting

POLICY_KEY = "news_editorial"
TASKS = ("news_group", "news_score")
DEFAULT_POLICY = {
    "enabled": True,
    "score_enabled": True,
    "first_party_threshold": 60,
    "media_threshold": 76,
    "calls_per_minute": 12,
    "calls_per_hour": 100,
    "calls_per_day": 300,
    "group_model": "",
    "score_model": "",
}


class EditorialDeferred(RuntimeError):
    """等待预算、在途请求或人工核对，保持原始内容可读。"""


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


async def get_policy(session) -> dict:
    stored = await get_setting(session, POLICY_KEY) or {}
    return {**DEFAULT_POLICY, **{k: v for k, v in stored.items() if k in DEFAULT_POLICY}}


async def _budget_row(session):
    row = await session.get(SiteSetting, POLICY_KEY)
    if row is None:
        try:
            async with session.begin_nested():
                session.add(SiteSetting(key=POLICY_KEY, value={}))
                await session.flush()
        except IntegrityError:
            pass
    # 与新回执、用量预约一起提交，多个 worker 共享预算行锁。
    return await session.scalar(select(SiteSetting).where(SiteSetting.key == POLICY_KEY).with_for_update())


async def call_json(session, *, task: str, ref_id: int, system: str, payload: dict, round_id: int = 0, validate=None) -> dict:
    config = await get_ai_config(session)
    policy = await get_policy(session)
    if not policy["enabled"] or not ai_available(config):
        raise EditorialDeferred("资讯编辑未启用或模型未配置")
    config = {**config, "model": policy["group_model" if task == "news_group" else "score_model"] or config["model"]}
    prompt_version = fingerprint(system)
    request_key = fingerprint({"task": task, "ref_id": ref_id, "model": config["model"],
        "provider": config["base_url"], "prompt": prompt_version, "payload": payload, "round": round_id})
    now = utcnow()
    await _budget_row(session)
    receipt = await session.scalar(select(EditorialReceipt).where(EditorialReceipt.request_key == request_key).with_for_update())
    if receipt and receipt.status == "received":
        await session.commit()
        return await _validated(session, receipt, validate)
    if receipt and receipt.status in {"pending", "unknown", "invalid"}:
        if receipt.status == "pending" and receipt.updated_at < now - timedelta(minutes=10):
            receipt.status, receipt.error, receipt.updated_at = "unknown", "请求中断，需核对后重试", now
            usage = await session.get(AiUsage, receipt.usage_id) if receipt.usage_id else None
            if usage:
                usage.status = "unknown"
        await session.commit()
        raise EditorialDeferred(f"请求等待处理：{receipt.status}")
    if receipt and (receipt.attempts >= 3 or receipt.updated_at > now - timedelta(minutes=10)):
        await session.commit()
        raise EditorialDeferred("请求处于失败冷却期")
    for name, window in (("minute", timedelta(minutes=1)), ("hour", timedelta(hours=1)), ("day", timedelta(days=1))):
        used = await session.scalar(select(func.count(AiUsage.id)).where(
            AiUsage.task.in_(TASKS), AiUsage.created_at > now - window)) or 0
        if used >= policy[f"calls_per_{name}"]:
            await session.commit()
            raise EditorialDeferred(f"资讯编辑请求额度已用完：{name}")
    usage = AiUsage(task=task, ref_id=ref_id, model=config["model"], status="pending")
    session.add(usage)
    await session.flush()
    if receipt is None:
        receipt = EditorialReceipt(request_key=request_key, task=task, ref_id=ref_id, model=config["model"],
            prompt_version=prompt_version, status="pending", usage_id=usage.id)
        session.add(receipt)
    else:
        receipt.status, receipt.error, receipt.usage_id = "pending", None, usage.id
        receipt.attempts += 1
        receipt.updated_at = now
    await session.commit()
    started = time.monotonic()
    try:
        response, tokens = await chat(config, system, json.dumps(payload, ensure_ascii=False, default=str), json_mode=True)
    except Exception as exc:
        # HTTP 明确拒绝可退避重试；传输中断可能已计费，不自动重发。
        receipt.status = "failed" if isinstance(exc, httpx.HTTPStatusError) else "unknown"
        receipt.error, receipt.updated_at = type(exc).__name__, utcnow()
        usage.status, usage.duration_ms = receipt.status, int((time.monotonic() - started) * 1000)
        await session.commit()
        raise EditorialDeferred("资讯编辑请求失败，已保存回执") from exc
    receipt.response, receipt.status, receipt.updated_at = response, "received", utcnow()
    usage.prompt_tokens = int((tokens or {}).get("prompt_tokens") or 0)
    usage.completion_tokens = int((tokens or {}).get("completion_tokens") or 0)
    usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
    usage.cost = (usage.prompt_tokens * config["price_in"] + usage.completion_tokens * config["price_out"]) / 1e6
    usage.duration_ms, usage.status = int((time.monotonic() - started) * 1000), "ok"
    await session.commit()
    return await _validated(session, receipt, validate)


async def _validated(session, receipt, validate):
    try:
        value = _parse(receipt.response)
        return validate(value) if validate else value
    except (ValueError, TypeError) as exc:
        receipt.status, receipt.error = "invalid", str(exc)[:500]
        await session.commit()
        raise EditorialDeferred("模型输出未通过校验，已保留原始回复") from exc


def _parse(response: str) -> dict:
    value = response.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0]
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("需要 JSON 对象")
    return result
