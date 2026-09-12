"""AstrBot 消息通道、只读查询工具和按会话调度。"""

import hashlib
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models.enums import COMMUNITY_LABELS
from app.models import BotDelivery, BotReminder, BotTarget, ContentSchedule, Event, Product, RawContent, RedeemCode
from app.services.notifications import group_candidates, load_candidates
from app.services.settings import get_setting
from app.services.views import product_to_out

TOOLS = {
    "digest": "今日速报",
    "news": "资讯查询",
    "products": "商品进展",
    "trending": "热度飙升",
    "calendar": "活动日历",
    "codes": "兑换码",
}


async def query_target(session: AsyncSession, target: BotTarget, tool: str = "digest", query: str = "", now=None, collected_after=None) -> dict:
    now = now or utcnow()
    config = target.config
    community = config.get("community", "all")
    keywords = config.get("keywords", [])
    scope = COMMUNITY_LABELS.get(community, "全站")
    if tool == "trending":
        from app.services.curation import select_trending

        items = await select_trending(session, 5, community=community, hours=24)
        lines = [
            f"{scope} · 热度飙升（24 小时播放增量）",
            (now + timedelta(hours=8)).strftime("更新于 %Y/%m/%d %H:%M（北京时间）"),
        ]
        for i, it in enumerate(items, 1):
            label = COMMUNITY_LABELS.get(it.get("community"), "")
            views = int((it.get("metrics") or {}).get("view") or 0)
            lines.extend(
                [
                    f"\n{i}. [{label}] {it['title']}",
                    f"24 小时播放 +{it['delta_view']:,} · 当前 {views:,}",
                ]
            )
            if it.get("url"):
                lines.append(it["url"])
        if not items:
            lines.append("窗口内还没有明显的热度变化。")
    elif tool == "calendar":
        items = []
        conditions = [] if community == "all" else [ContentSchedule.community == community]
        conditions.append(RawContent.hidden.is_(False))
        rows = await session.execute(
            select(ContentSchedule, RawContent.url)
            .join(RawContent, RawContent.id == ContentSchedule.raw_content_id)
            .where(*conditions)
            .order_by(ContentSchedule.start_at)
            .limit(40)
        )
        kind_labels = {"activity": "活动", "maintenance": "维护", "pool": "卡池", "broadcast": "直播", "delay": "变更"}
        fmt = lambda dt: (dt + timedelta(hours=8)).strftime("%m/%d %H:%M")  # noqa: E731
        lines = [
            f"{scope} · 活动日历",
            (now + timedelta(hours=8)).strftime("更新于 %Y/%m/%d %H:%M（北京时间）"),
        ]
        count = 0
        for schedule, url in rows:
            end = schedule.end_at
            if schedule.start_at <= now and (end is None or end >= now):
                when = f"进行中 · {fmt(end) if end else '长期'} 结束"
            elif schedule.start_at > now:
                when = f"{fmt(schedule.start_at)} 开始"
            else:
                continue
            delayed = "（已延后）" if schedule.delayed else ""
            kind = kind_labels.get(schedule.kind, schedule.kind)
            lines.append(f"\n[{kind}]{delayed} {schedule.title}\n{when}")
            if url:
                lines.append(url)
            count += 1
            if count >= 8:
                break
        if not count:
            lines.append("暂无可展示的日程。")
    elif tool == "codes":
        items = []
        conditions = [RedeemCode.status == "active", RedeemCode.expires_at.is_(None) | (RedeemCode.expires_at > now)]
        if community != "all":
            conditions.append(RedeemCode.community == community)
        rows = await session.execute(
            select(RedeemCode, RawContent.url)
            .join(RawContent, RawContent.id == RedeemCode.raw_content_id)
            .where(*conditions)
            .order_by(RedeemCode.created_at.desc())
            .limit(8)
        )
        lines = [f"{scope} · 可用兑换码", (now + timedelta(hours=8)).strftime("更新于 %Y/%m/%d %H:%M（北京时间）")]
        found = 0
        for code, url in rows:
            expiry = f"（{fmt(code.expires_at)} 前）" if code.expires_at else ""
            lines.append(f"\n{code.code}{expiry}")
            if url:
                lines.append(url)
            found += 1
        if not found:
            lines.append("当前没有可用的兑换码。")
    elif tool == "products":
        stmt = select(Product).order_by(Product.id)
        if community != "all":
            stmt = stmt.where(Product.community == community)
        products = list(await session.scalars(stmt))
        products = [p for p in products if (not query or query.casefold() in (p.name + " " + " ".join(p.watch_terms or [])).casefold())
                    and (not keywords or any(k.casefold() in p.name.casefold() for k in keywords))]
        items = [(await product_to_out(session, p)).model_dump(mode="json") for p in products[:5]]
        lines = [f"{scope} · 商品进展"]
        for p in items:
            lines.extend([p["name"], f"官方：{p['official_status']}", f"用户到货：{p['consumer_status']}"])
            if p.get("latest_candidate"):
                lines.append(f"待核验：{p['latest_candidate']['summary']}")
            for evidence in p.get("evidences", [])[:2]:
                if evidence.get("url"):
                    lines.append(evidence["url"])
        if not items:
            lines.append("没有找到匹配的商品档案，请使用商品名称或更短的关键词。")
    else:
        candidates = await load_candidates(
            session, keywords or None, now - timedelta(hours=24 if tool == "digest" else 168), now,
            community=community, collected_after=collected_after,
        )
        if query:
            candidates = [c for c in candidates if query.casefold() in (c["title"] + c["text"]).casefold()]
        candidates = [c for c in candidates if c["strength"] == "strong"]
        items = await group_candidates(session, candidates)
        if collected_after:
            items = [i for i in items if i["identity"] == "official" or i["importance"]]
        lines = [f"{scope} · {'重要动态' if collected_after else TOOLS[tool]}", (now + timedelta(hours=8)).strftime("更新于 %Y/%m/%d %H:%M（北京时间）")]
        for i, item in enumerate(items[:5], 1):
            label = COMMUNITY_LABELS.get(item.get("community"), "")
            lines.extend([f"\n{i}. [{label}] {item['title']}", (item.get("summary") or item["text"])[:240]])
            lines.append(f"来源：{item.get('source') or item['platform']} · {(item['published_at'] + timedelta(hours=8)).strftime('%m/%d %H:%M')}")
            if item.get("url"):
                lines.append(item["url"])
        if len(items) > 5:
            lines.append(f"\n本次共 {len(items)} 条，以上为前 5 条精选。")
        if not items:
            lines.append("当前范围内没有匹配的新资讯。")
    return {"text": "\n".join(lines), "items": items, "instructions": config.get("ai_prompt", ""),
            "grounding": "仅根据查询结果回答，保留来源和时间；待核验消息不得表述为已证实。检索不到不能凭记忆补写。"}


async def send_message(bridge: dict, umo: str, content: str) -> tuple[str, str | None]:
    if not bridge.get("endpoint") or not bridge.get("api_key"):
        return "failed", "请先配置 AstrBot 通道"
    try:
        async with httpx.AsyncClient(timeout=25, trust_env=False) as client:
            response = await client.post(bridge["endpoint"], headers={"X-API-Key": bridge["api_key"]},
                                         json={"umo": umo, "message": content})
        if response.status_code >= 500:
            return "unknown", f"网关 HTTP {response.status_code}，请核对是否收到后再重发"
        if response.status_code != 200:
            return "failed", f"网关 HTTP {response.status_code}"
        data = response.json()
        if data.get("status") != "ok":
            # 不回显远端异常，避免响应中混入带密钥的 URL。
            return "failed", "AstrBot 拒绝发送，请查看机器人端日志及会话权限"
        return "accepted", None
    except httpx.TimeoutException:
        return "unknown", "请求超时，可能已发送；请核对收件端后再重发"
    except (httpx.HTTPError, ValueError):
        return "unknown", "网关连接或响应异常；请核对收件端后再重发"


async def create_delivery(session, target, kind, text, key, media=None):
    row = await session.scalar(select(BotDelivery).where(BotDelivery.dedupe_key == key))
    if row:
        return row
    row = BotDelivery(target_id=target.id, kind=kind, body=text, media=list(media) if media else None, dedupe_key=key)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        row = await session.scalar(select(BotDelivery).where(BotDelivery.dedupe_key == key))
    return row


async def deliver(session, target, row, *, force=False):
    if not target.enabled and not force:
        return row
    # 人格播报由 AstrBot 插件领取，使用它实际配置的人格与模型。
    if target.config.get("delivery_mode") == "astrbot":
        return row
    result = await session.execute(update(BotDelivery).where(
        BotDelivery.id == row.id, BotDelivery.status.in_(["pending", "failed"]), BotDelivery.attempts < 3,
    ).values(status="sending", attempts=BotDelivery.attempts + 1, updated_at=utcnow()))
    await session.commit()
    if not result.rowcount:
        await session.refresh(row)
        return row
    status, error = await send_message(await get_setting(session, "bot_bridge") or {}, target.umo, row.body)
    row.status, row.error, row.updated_at = status, error, utcnow()
    await session.commit()
    return row


async def run_bot_notifications(session: AsyncSession, now=None) -> dict:
    now = now or utcnow()
    local = now + timedelta(hours=8)
    # 进程在发送中退出时无法判定远端结果，绝不盲目自动重发。
    await session.execute(update(BotDelivery).where(BotDelivery.status == "sending", BotDelivery.updated_at < now - timedelta(minutes=5)).values(
        status="unknown", error="发送过程中进程中断，请核对收件端后再重发", updated_at=now))
    await session.commit()
    ids = list(await session.scalars(select(BotTarget.id).where(BotTarget.enabled.is_(True))))
    processed = 0
    for target_id in ids:
        target = await session.get(BotTarget, target_id)
        config = target.config
        if config.get("reminders_enabled"):
            reminder_ids = list(await session.scalars(select(BotReminder.id).where(
                BotReminder.target_id == target.id, BotReminder.status == "pending", BotReminder.due_at <= now,
            ).order_by(BotReminder.due_at).limit(20)))
            for reminder_id in reminder_ids:
                reminder = await session.scalar(select(BotReminder).where(BotReminder.id == reminder_id,
                    BotReminder.status == "pending").with_for_update())
                if not reminder:
                    continue
                due = (reminder.due_at + timedelta(hours=8)).strftime("%Y/%m/%d %H:%M")
                reminder.status = "queued"
                await create_delivery(session, target, "reminder", f"约定提醒 #{reminder.id} · {due}（北京时间）\n{reminder.body}", f"reminder:{reminder.id}")
            await session.commit()
        if config.get("daily_enabled"):
            hour, minute = map(int, config.get("daily_time", "08:00").split(":"))
            due = local.replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(hours=8)
            if now >= due and target.created_at <= due:
                key = f"daily:{target.id}:{local.date()}"
                if not await session.scalar(select(BotDelivery.id).where(BotDelivery.dedupe_key == key)):
                    payload = await query_target(session, target, now=due)
                    covers = [i["image"] for i in payload["items"] if i.get("image")][:1]
                    await create_delivery(session, target, "daily", payload["text"], key, media=covers)
        quiet_start, quiet_end = config.get("quiet_start", 23), config.get("quiet_end", 8)
        quiet = (quiet_start <= local.hour < quiet_end) if quiet_start < quiet_end else (local.hour >= quiet_start or local.hour < quiet_end) if quiet_start != quiet_end else False
        if config.get("updates_enabled") and not quiet:
            # 日历提醒：活动/卡池/直播结束前 2 小时、维护开始前 30 分钟（静默时段直接跳过该时刻）
            target_community = config.get("community", "all")
            sched_conditions = [RawContent.hidden.is_(False)]
            if target_community != "all":
                sched_conditions.append(ContentSchedule.community == target_community)
            schedule_rows = await session.execute(
                select(ContentSchedule)
                .join(RawContent, RawContent.id == ContentSchedule.raw_content_id)
                .where(*sched_conditions, ContentSchedule.start_at <= now + timedelta(days=45))
            )
            for schedule in schedule_rows.scalars():
                stage_at: datetime | None = None
                if schedule.kind in ("activity", "pool", "broadcast") and schedule.end_at and now < schedule.end_at <= now + timedelta(hours=2):
                    stage_at = schedule.end_at
                elif schedule.kind == "maintenance" and now < schedule.start_at <= now + timedelta(minutes=30):
                    stage_at = schedule.start_at
                if stage_at is None:
                    continue
                stage = schedule.kind if stage_at == schedule.end_at else "maintenance_start"
                due = (stage_at + timedelta(hours=8)).strftime("%Y/%m/%d %H:%M")
                text = (
                    f"⏰ 日历提醒\n{schedule.title}\n"
                    f"{(stage_at + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M')}（北京时间）"
                    f"{'结束' if stage_at == schedule.end_at else '开始'}"
                )
                await create_delivery(session, target, "calendar", text, f"calendar:{schedule.id}:{stage}")
        if config.get("updates_enabled") and not quiet:
            payload = await query_target(session, target, now=now, collected_after=target.last_scan_at)
            for item in payload["items"]:
                event = await session.get(Event, item["event_id"]) if item.get("event_id") else None
                # 同一公告跨平台补证据不重复推送；履约结论改变时才再次通知。
                identity = f"event:{event.id}:{event.conclusion}" if event else f"content:{item['id']}"
                digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
                text = (f"{COMMUNITY_LABELS.get(item.get('community'), '')} · 重要动态\n{item['title']}\n"
                        f"{(item.get('summary') or item['text'])[:500]}\n"
                        f"来源：{item.get('source') or item['platform']} · {(item['published_at'] + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M')}\n"
                        f"{item.get('url') or ''}")
                await create_delivery(session, target, "updates", text, f"updates:{target.id}:{digest}",
                                      media=[item["image"]] if item.get("image") else None)
            target.last_scan_at = now
            await session.commit()
        pending = list(await session.scalars(select(BotDelivery).where(
            BotDelivery.target_id == target.id,
            BotDelivery.kind.in_(["daily", "updates", "reminder", "calendar"]),
            BotDelivery.status.in_(["pending", "failed"]), BotDelivery.attempts < 3,
        ).order_by(BotDelivery.id).limit(10)))
        for row in pending:
            if row.kind == "reminder" and not config.get("reminders_enabled"):
                continue
            if row.kind in ("updates", "calendar") and quiet:
                continue
            if row.status == "failed" and row.updated_at > now - timedelta(minutes=5):
                continue
            await deliver(session, target, row)
            processed += 1
    return {"targets": len(ids), "processed": processed}
