"""AstrBot 消息通道、只读查询工具和按会话调度。"""

import hashlib
import re
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models.enums import COMMUNITY_LABELS
from app.models import (
    BotDelivery,
    BotReminder,
    BotTarget,
    ContentSchedule,
    ContentVersion,
    Event,
    Evidence,
    Product,
    RawContent,
    RedeemCode,
)
from app.services.notifications import _bot_image_url, group_candidates, load_candidates
from app.services.settings import get_setting, set_setting
from app.services.views import product_to_out

TOOLS = {
    "digest": "今日速报",
    "news": "资讯查询",
    "products": "商品进展",
    "trending": "热度飙升",
    "calendar": "活动日历",
    "codes": "兑换码",
}

# 合并缓冲存 site_settings，避免为一期加表迁移；key 按目标隔离。
_MERGE_KEY_PREFIX = "bot_merge_"


def _community_label(community: str | None) -> str:
    return COMMUNITY_LABELS.get(community or "", "")


def _fmt_bj(dt: datetime, fmt: str = "%m/%d %H:%M") -> str:
    return (dt + timedelta(hours=8)).strftime(fmt)


def format_short_update(item: dict) -> str:
    """结构化短讯：分区徽章 + 标题 + 一句话 + 时间/链接。"""
    label = _community_label(item.get("community"))
    title = item.get("title") or "未命名动态"
    summary = (item.get("summary") or item.get("text") or "").replace("\n", " ").strip()
    if len(summary) > 80:
        summary = summary[:79] + "…"
    when = _fmt_bj(item.get("published_at") or utcnow())
    url = (item.get("url") or "").strip()
    lines = [f"[{label}] {title}" if label else title]
    if summary:
        lines.append(summary)
    lines.append(f"{when} · {url}" if url else when)
    return "\n".join(lines)


def format_daily_brief(items: list[dict], now: datetime, limit: int = 5) -> str:
    """QQ 早报短列表：标题+链接，细节留给站内；配套 collect_images 多图。"""
    lines = [f"今日速报 · {_fmt_bj(now, '%m/%d')}", f"共 {len(items)} 条，以下前 {min(limit, len(items))} 条"]
    for i, item in enumerate(items[:limit], 1):
        label = _community_label(item.get("community"))
        title = (item.get("title") or "未命名动态").replace("\n", " ")
        if len(title) > 40:
            title = title[:39] + "…"
        prefix = f"[{label}] " if label else ""
        lines.append(f"{i}. {prefix}{title}")
        url = (item.get("url") or "").strip()
        if url:
            lines.append(url)
    if not items:
        lines.append("当前范围内没有匹配的新资讯。")
    return "\n".join(lines)


def format_merged_updates(items: list[dict], now: datetime) -> str:
    """合并窗口内多条 updates 压成一条列表，避免连环刷屏。"""
    lines = [f"重要动态 ×{len(items)}", _fmt_bj(now, "%m/%d %H:%M 更新")]
    for i, item in enumerate(items[:8], 1):
        label = _community_label(item.get("community"))
        title = item.get("title") or "未命名动态"
        prefix = f"[{label}] " if label else ""
        lines.append(f"{i}. {prefix}{title}")
        url = (item.get("url") or "").strip()
        if url:
            lines.append(url)
    if len(items) > 8:
        lines.append(f"…共 {len(items)} 条")
    return "\n".join(lines)


def collect_images(items: list[dict], limit: int) -> list[str]:
    images: list[str] = []
    for item in items:
        image = item.get("image")
        if image and image not in images:
            images.append(image)
        if len(images) >= limit:
            break
    return images


def _merge_setting_key(target_id: int) -> str:
    return f"{_MERGE_KEY_PREFIX}{target_id}"


async def load_merge_buffer(session: AsyncSession, target_id: int) -> dict:
    return (await get_setting(session, _merge_setting_key(target_id))) or {"items": [], "flush_at": None}


async def save_merge_buffer(session: AsyncSession, target_id: int, buffer: dict) -> None:
    await set_setting(session, _merge_setting_key(target_id), buffer)


def _is_quiet(local_hour: int, config: dict) -> bool:
    start, end = config.get("quiet_start", 23), config.get("quiet_end", 8)
    if start == end:
        return False
    if start < end:
        return start <= local_hour < end
    return local_hour >= start or local_hour < end


async def _hourly_updates_count(session: AsyncSession, target_id: int, now: datetime) -> int:
    return await session.scalar(
        select(func.count()).select_from(BotDelivery).where(
            BotDelivery.target_id == target_id,
            BotDelivery.kind.in_(["updates", "critical", "calendar"]),
            BotDelivery.created_at >= now - timedelta(hours=1),
        )
    ) or 0


def classify_item(item: dict) -> str:
    """critical 立即推（可穿透静默）；normal 进合并窗口。"""
    if item.get("importance"):
        return "critical"
    return "normal"


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


async def _events_for_contents(session: AsyncSession, content_ids: list[int]) -> dict[int, Event]:
    """内容 → 关联事件（与周期扫描同一套归属：保证 critical 去重键完全一致）。"""
    if not content_ids:
        return {}
    rows = await session.execute(
        select(Evidence.raw_content_id, Event)
        .join(Event, Event.id == Evidence.event_id)
        .where(
            Evidence.raw_content_id.in_(content_ids),
            Evidence.role != "excluded",
            Event.is_active.is_(True),
        )
        .order_by(Event.importance.desc(), Event.last_updated_at.desc())
    )
    events: dict[int, Event] = {}
    for content_id, event in rows:
        events.setdefault(content_id, event)
    return events


def _critical_key(target_id: int, identity: str) -> str:
    """critical 投递去重键：与周期扫描同一套算法，直推与扫描天然互斥。"""
    return f"critical:{target_id}:{hashlib.sha256(identity.encode()).hexdigest()[:32]}"


async def _event_for(session: AsyncSession, item: dict):
    return await session.get(Event, item["event_id"]) if item.get("event_id") else None


def _item_identity(event, item: dict) -> str:
    """内容身份：绑定事件时随结论变化，否则按内容 id 稳定去重。"""
    return f"event:{event.id}:{event.conclusion}" if event is not None else f"content:{item['id']}"


async def _contents_for_push(session: AsyncSession, content_ids: list[int]) -> list[dict]:
    """按内容 id 取最新版本，组装成 format_short_update 需要的短讯结构。"""
    latest_sq = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    rows = await session.execute(
        select(RawContent, ContentVersion)
        .join(latest_sq, latest_sq.c.rid == RawContent.id)
        .join(
            ContentVersion,
            (ContentVersion.raw_content_id == latest_sq.c.rid)
            & (ContentVersion.version == latest_sq.c.version),
        )
        .where(RawContent.id.in_(content_ids), RawContent.hidden.is_(False))
        .order_by(RawContent.id)
    )
    found = list(rows)
    events = await _events_for_contents(session, [raw.id for raw, _ in found])
    items: list[dict] = []
    for raw, version in found:
        normalized = version.normalized_json or {}
        media = normalized.get("media") if isinstance(normalized, dict) else []
        image = next(
            (
                safe
                for value in (media if isinstance(media, list) else [])
                if isinstance(value, str) and (safe := _bot_image_url(value))
            ),
            None,
        )
        items.append({
            "id": raw.id,
            "community": raw.community,
            "title": version.title or raw.external_id,
            "summary": re.sub(r"\s+", " ", (version.text or "")).strip()[:200],
            "url": raw.url,
            "image": image,
            "published_at": raw.published_at or raw.collected_at,
            # 身份与周期扫描一致：绑事件时随结论变化，否则按内容 id 稳定去重
            "identity": _item_identity(events.get(raw.id), {"id": raw.id}),
        })
    return items


async def push_important_contents(session: AsyncSession, content_ids: list[int]) -> int:
    """意图打标为 important 的内容 → 为每个启用的机器人目标建 critical 投递。

    dedupe key 按内容去重（与周期扫描的 critical 语义一致），静默时段默认穿透
    （quiet_critical 默认开启）；实际发送仍由 run_bot_notifications 的投递循环负责。
    """
    if not content_ids:
        return 0
    items = await _contents_for_push(session, content_ids)
    if not items:
        return 0
    targets = list(
        await session.scalars(select(BotTarget).where(BotTarget.enabled.is_(True)))
    )
    created = 0
    for target in targets:
        for item in items:
            key = _critical_key(target.id, item["identity"])
            if await session.scalar(select(BotDelivery.id).where(BotDelivery.dedupe_key == key)):
                continue  # 已投递过：按内容去重，不重复打扰
            await create_delivery(
                session, target, "critical", format_short_update(item), key,
                media=[item["image"]] if item.get("image") else None,
            )
            created += 1
    return created


async def _intent_ignored_ids(session: AsyncSession, content_ids: list[int]) -> set[int]:
    """意图打标记为 ignore 的内容 id：机器人不再推送它们。"""
    if not content_ids:
        return set()
    rows = await session.scalars(
        select(RawContent.id).where(
            RawContent.id.in_(content_ids), RawContent.intent_tier == "ignore"
        )
    )
    return set(rows)


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


async def _flush_merge_buffer(session, target, buffer: dict, now: datetime, *, quiet: bool, quiet_critical: bool,
                              hourly_limit: int, max_images: int) -> None:
    if not (buffer.get("items") and buffer.get("flush_at")):
        return
    flush_at = datetime.fromisoformat(buffer["flush_at"])
    if now < flush_at:
        return
    can_send = True
    if quiet and not quiet_critical:
        can_send = False
    elif hourly_limit and await _hourly_updates_count(session, target.id, now) >= hourly_limit:
        can_send = False
    if can_send:
        images = collect_images(buffer["items"], max_images)
        text = format_merged_updates(buffer["items"], now)
        window_key = hashlib.sha256(
            (f"merge:{target.id}:{buffer['flush_at']}:" + "".join(i["dedupe"] for i in buffer["items"])).encode()
        ).hexdigest()[:32]
        await create_delivery(session, target, "updates", text, f"updates:{target.id}:{window_key}", media=images)
        await save_merge_buffer(session, target.id, {"items": [], "flush_at": None})
    else:
        buffer["flush_at"] = (now + timedelta(minutes=5)).isoformat()
        await save_merge_buffer(session, target.id, buffer)


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
        quiet = _is_quiet(local.hour, config)
        quiet_critical = bool(config.get("quiet_critical", True))
        merge_minutes = int(config.get("merge_minutes", 5) or 0)
        hourly_limit = int(config.get("hourly_limit", 6) or 0)
        max_images = max(1, min(9, int(config.get("max_images", 3) or 3)))
        if config.get("reminders_enabled"):
            reminder_ids = list(await session.scalars(select(BotReminder.id).where(
                BotReminder.target_id == target.id, BotReminder.status == "pending", BotReminder.due_at <= now,
            ).order_by(BotReminder.due_at).limit(20)))
            for reminder_id in reminder_ids:
                reminder = await session.scalar(select(BotReminder).where(BotReminder.id == reminder_id,
                    BotReminder.status == "pending").with_for_update())
                if not reminder:
                    continue
                due = _fmt_bj(reminder.due_at, "%Y/%m/%d %H:%M")
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
                    items = payload.get("items") or []
                    style = config.get("daily_style", "compact")
                    # 早报封面只带 1 张：多图会被 QQ 官方拆成多条消息。
                    covers = collect_images(items, 1)
                    if style == "split" and items:
                        head = payload["text"].split("\n", 2)[0]
                        await create_delivery(session, target, "daily", f"{head}\n共 {len(items)} 条重点，详情如下", key, media=covers[:1])
                        for idx, item in enumerate(items[:2], 1):
                            await create_delivery(
                                session, target, "updates",
                                format_short_update(item),
                                f"daily:{target.id}:{local.date()}:{idx}",
                                media=[item["image"]] if item.get("image") else None,
                            )
                    else:
                        brief = format_daily_brief(items, due)
                        await create_delivery(session, target, "daily", brief, key, media=covers)
        # 日历：默认静默跳过；quiet_critical 时穿透静默，避免临期事项丢失。
        if config.get("updates_enabled") and (not quiet or quiet_critical):
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
                due = _fmt_bj(stage_at, "%Y/%m/%d %H:%M")
                text = (
                    f"⏰ 日历提醒\n{schedule.title}\n"
                    f"{due}（北京时间）"
                    f"{'结束' if stage_at == schedule.end_at else '开始'}"
                )
                await create_delivery(session, target, "calendar", text, f"calendar:{schedule.id}:{stage}")
        buffer = await load_merge_buffer(session, target.id)
        if config.get("updates_enabled"):
            payload = await query_target(session, target, now=now, collected_after=target.last_scan_at)
            critical_items = []
            normal_items = []
            for item in payload["items"]:
                if classify_item(item) == "critical":
                    critical_items.append(item)
                else:
                    normal_items.append(item)
            # 守卫：意图打标记为 ignore 的内容不再推送（采集阶段已判定为噪声）
            ignored_ids = await _intent_ignored_ids(
                session, [item["id"] for item in payload["items"]]
            )
            if ignored_ids:
                critical_items = [i for i in critical_items if i["id"] not in ignored_ids]
                normal_items = [i for i in normal_items if i["id"] not in ignored_ids]
            # 守卫：已打标/已直推过的内容不重复推送（critical 去重键与采集直推一致）
            critical_keys: dict[int, str] = {}
            for item in critical_items:
                critical_keys[item["id"]] = _critical_key(
                    target.id, _item_identity(await _event_for(session, item), item)
                )
            delivered: set[str] = set()
            if critical_keys:
                delivered = set(
                    await session.scalars(
                        select(BotDelivery.dedupe_key).where(
                            BotDelivery.dedupe_key.in_(list(critical_keys.values()))
                        )
                    )
                )
            for item in critical_items:
                key = critical_keys[item["id"]]
                if key in delivered:
                    continue
                delivered.add(key)
                await create_delivery(
                    session, target, "critical", format_short_update(item), key,
                    media=[item["image"]] if item.get("image") else None,
                )
            if normal_items and merge_minutes <= 0:
                for item in normal_items:
                    event = await _event_for(session, item)
                    digest = hashlib.sha256(_item_identity(event, item).encode()).hexdigest()[:32]
                    await create_delivery(
                        session, target, "updates", format_short_update(item),
                        f"updates:{target.id}:{digest}",
                        media=[item["image"]] if item.get("image") else None,
                    )
            elif normal_items:
                known = {entry.get("dedupe") for entry in buffer.get("items", [])}
                for item in normal_items:
                    event = await _event_for(session, item)
                    digest = hashlib.sha256(_item_identity(event, item).encode()).hexdigest()[:32]
                    if digest in known:
                        continue
                    published = item.get("published_at")
                    buffer.setdefault("items", []).append({
                        "dedupe": digest,
                        "title": item.get("title"),
                        "community": item.get("community"),
                        "summary": (item.get("summary") or item.get("text") or "")[:200],
                        "url": item.get("url"),
                        "image": item.get("image"),
                        "published_at": published.isoformat() if isinstance(published, datetime) else None,
                    })
                if buffer["items"] and not buffer.get("flush_at"):
                    buffer["flush_at"] = (now + timedelta(minutes=merge_minutes)).isoformat()
                # 新条目进缓冲后立刻落库，进程重启也不丢。
                await save_merge_buffer(session, target.id, buffer)
            target.last_scan_at = now
            await session.commit()
            await _flush_merge_buffer(
                session, target, buffer, now,
                quiet=quiet, quiet_critical=quiet_critical, hourly_limit=hourly_limit, max_images=max_images,
            )
        elif buffer.get("items"):
            await save_merge_buffer(session, target.id, {"items": [], "flush_at": None})
        pending = list(await session.scalars(select(BotDelivery).where(
            BotDelivery.target_id == target.id,
            BotDelivery.kind.in_(["daily", "updates", "critical", "reminder", "calendar"]),
            BotDelivery.status.in_(["pending", "failed"]), BotDelivery.attempts < 3,
        ).order_by(BotDelivery.id).limit(10)))
        for row in pending:
            if row.kind == "reminder" and not config.get("reminders_enabled"):
                continue
            if row.kind == "updates" and quiet:
                continue
            if row.kind == "calendar" and quiet and not quiet_critical:
                continue
            if row.kind == "critical" and quiet and not quiet_critical:
                continue
            if row.status == "failed" and row.updated_at > now - timedelta(minutes=5):
                continue
            await deliver(session, target, row)
            processed += 1
    return {"targets": len(ids), "processed": processed}
