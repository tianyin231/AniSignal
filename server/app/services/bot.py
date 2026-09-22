"""AstrBot 消息通道、只读查询工具和按会话调度。"""

import hashlib
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models.enums import COMMUNITY_LABELS
from app.models import BotDelivery, BotReminder, BotTarget, ContentSchedule, Event, Product, RawContent, RedeemCode
from app.services.notifications import group_candidates, is_empty_dynamic_item, load_candidates
from app.services.bot_briefs import build_brief, fallback_items, format_brief, single_item_brief
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


def format_daily_brief(items: list[dict], now: datetime, limit: int = 3) -> str:
    return format_brief({"title": "今日速报", "date": _fmt_bj(now, "%m/%d"),
                         "items": fallback_items(items[:limit])})


def format_merged_updates(items: list[dict], now: datetime) -> str:
    """合并窗口内多条 updates 压成一条列表，避免连环刷屏。"""
    return format_brief({"title": "刚刚的重点", "date": _fmt_bj(now), "items": fallback_items(items)})


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
    brief = None
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
        from app.services.calendar import calendar_entries

        items = []
        entries = await calendar_entries(session, community, now=now)
        entries.sort(key=lambda e: e["end_at"] or e["start_at"])
        lines = [f"{scope} · 活动日历", _fmt_bj(now, "更新于 %Y/%m/%d %H:%M（北京时间）")]
        count = 0
        for entry in entries:
            start, end = entry["start_at"], entry["end_at"]
            fmt = "%m/%d" if entry["all_day"] else "%m/%d %H:%M"
            if start > now:
                when = f"{_fmt_bj(start, fmt)} {'截止' if entry['kind'] == 'deadline' else '开始'}"
            elif end and end > now:
                when = f"进行中 · {_fmt_bj(end, fmt)} 结束"
            else:
                continue
            lines.append(f"\n{entry['title']}\n{when}")
            if entry["url"]:
                lines.append(entry["url"])
            count += 1
            if count >= 8:
                break
        if not count:
            lines.append("暂无可展示的日程。")
    elif tool == "codes":
        items = []
        conditions = [RawContent.hidden.is_(False), RedeemCode.status == "active", RedeemCode.expires_at.is_(None) | (RedeemCode.expires_at > now)]
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
            expiry = f"（{_fmt_bj(code.expires_at)} 前）" if code.expires_at else ""
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
        if not collected_after:
            brief = await build_brief(session, items, now, mode="daily" if tool == "digest" else "updates")
            items = brief["items"]
            lines = [format_brief(brief)]
    return {"text": "\n".join(lines), "items": items, "instructions": config.get("ai_prompt", ""),
            "brief": brief,
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


async def create_delivery(session, target, kind, text, key, media=None, brief=None):
    row = await session.scalar(select(BotDelivery).where(BotDelivery.dedupe_key == key))
    if row:
        return row
    row = BotDelivery(target_id=target.id, kind=kind, body=text, media=list(media) if media else None,
                      brief=brief, dedupe_key=key)
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
    if not await prepare_delivery(session, row):
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


async def prepare_delivery(session: AsyncSession, row: BotDelivery) -> bool:
    """领取和直发共用兜底，阻止旧简报快照中的空动态进入 QQ。"""
    if row.kind not in {"daily", "updates", "critical"} or not row.brief:
        return True
    items = row.brief.get("items") or []
    usable = [item for item in items if not is_empty_dynamic_item(item)]
    if len(usable) == len(items):
        return True
    if usable:
        brief = {**row.brief, "items": usable}
        if brief.get("mode") == "single":
            item = dict(usable[0])
            if isinstance(item.get("published_at"), str):
                item["published_at"] = datetime.fromisoformat(item["published_at"])
            body = format_short_update(item)
        else:
            body = format_brief(brief)
        values = {"brief": brief, "body": body, "media": collect_images(usable, 1) or None}
    else:
        values = {"status": "cancelled", "receipt": None, "updated_at": utcnow(),
                  "error": "来源动态正文和图片均为空，已取消本次推送"}
    result = await session.execute(update(BotDelivery).where(
        BotDelivery.id == row.id, BotDelivery.status.in_(["pending", "failed"]),
    ).values(**values))
    await session.commit()
    await session.refresh(row)
    return bool(result.rowcount and usable)


async def _flush_merge_buffer(session, target, buffer: dict, now: datetime, *, quiet: bool, quiet_critical: bool,
                              hourly_limit: int, max_images: int) -> None:
    if not (buffer.get("items") and buffer.get("flush_at")):
        return
    flush_at = datetime.fromisoformat(buffer["flush_at"])
    if now < flush_at:
        return
    can_send = True
    if quiet:
        can_send = False
    elif hourly_limit and await _hourly_updates_count(session, target.id, now) >= hourly_limit:
        can_send = False
    if can_send:
        brief = await build_brief(session, buffer["items"], now)
        images = collect_images(brief["items"], 1)
        text = format_brief(brief)
        window_key = hashlib.sha256(
            (f"merge:{target.id}:{buffer['flush_at']}:" + "".join(i["dedupe"] for i in buffer["items"])).encode()
        ).hexdigest()[:32]
        if brief["items"]:
            await create_delivery(session, target, "updates", text, f"updates:{target.id}:{window_key}", media=images, brief=brief)
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
        merge_minutes = int(config.get("merge_minutes", 30) or 0)
        hourly_limit = int(config.get("hourly_limit", 2) or 0)
        max_images = 1
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
                                brief=single_item_brief(item),
                            )
                    else:
                        brief = payload.get("brief") or await build_brief(session, items, due, mode="daily")
                        await create_delivery(session, target, "daily", format_brief(brief), key, media=covers, brief=brief)
        # 日历：默认静默跳过；quiet_critical 时穿透静默，避免临期事项丢失。
        if config.get("updates_enabled") and (not quiet or quiet_critical):
            target_community = config.get("community", "all")
            from app.services.calendar import calendar_entries

            for schedule in await calendar_entries(session, target_community, now=now):
                if schedule["all_day"]:
                    continue  # 没有具体时刻的公告不发送精确到分钟的提醒。
                stage_at: datetime | None = None
                start, end, kind = schedule["start_at"], schedule["end_at"], schedule["kind"]
                if kind in ("activity", "pool", "broadcast") and end and now < end <= now + timedelta(hours=2):
                    stage_at = end
                elif kind == "maintenance" and now < start <= now + timedelta(minutes=30):
                    stage_at = start
                if stage_at is None:
                    continue
                stage = kind if stage_at == end else "maintenance_start"
                due = _fmt_bj(stage_at, "%Y/%m/%d %H:%M")
                text = f"⏰ 日历提醒\n{schedule['title']}\n{due}（北京时间）{'结束' if stage_at == end else '开始'}"
                await create_delivery(session, target, "calendar", text, f"calendar:{target.id}:{schedule['id']}:{stage}")
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
            for item in critical_items:
                event = await session.get(Event, item["event_id"]) if item.get("event_id") else None
                if event is not None:
                    identity = f"event:{event.id}:{event.conclusion}"
                else:
                    identity = f"content:{item['id']}"
                digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
                await create_delivery(
                    session, target, "critical", format_short_update(item),
                    f"critical:{target.id}:{digest}",
                    media=[item["image"]] if item.get("image") else None,
                    brief=single_item_brief(item),
                )
            if normal_items and merge_minutes <= 0:
                for item in normal_items:
                    event = await session.get(Event, item["event_id"]) if item.get("event_id") else None
                    if event is not None:
                        identity = f"event:{event.id}:{event.conclusion}"
                    else:
                        identity = f"content:{item['id']}"
                    digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
                    await create_delivery(
                        session, target, "updates", format_short_update(item),
                        f"updates:{target.id}:{digest}",
                        media=[item["image"]] if item.get("image") else None,
                        brief=single_item_brief(item),
                    )
            elif normal_items:
                known = {entry.get("dedupe") for entry in buffer.get("items", [])}
                for item in normal_items:
                    event = await session.get(Event, item["event_id"]) if item.get("event_id") else None
                    if event is not None:
                        identity = f"event:{event.id}:{event.conclusion}"
                    else:
                        identity = f"content:{item['id']}"
                    digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
                    if digest in known:
                        continue
                    published = item.get("published_at")
                    buffer.setdefault("items", []).append({
                        "id": item.get("id"),
                        "content_ids": item.get("content_ids", []),
                        "dedupe": digest,
                        "title": item.get("title"),
                        "community": item.get("community"),
                        "summary": (item.get("summary") or item.get("text") or "")[:500],
                        "text": (item.get("text") or "")[:1600],
                        "source": item.get("source") or item.get("platform"),
                        "platform": item.get("platform"), "content_type": item.get("content_type"),
                        "identity": item.get("identity", "unknown"),
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
