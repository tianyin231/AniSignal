"""AstrBot 消息通道、只读查询工具和按会话调度。"""

import hashlib
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.communities import COMMUNITIES
from app.models.enums import COMMUNITY_LABELS
from app.models import BotDelivery, BotReminder, BotTarget, ContentSchedule, Event, Evidence, Product, RawContent, RedeemCode, Source
from app.services.notifications import group_candidates, is_empty_dynamic_item, load_candidates
from app.services.bot_briefs import (_source, build_brief, build_shared_brief, changed_points, fallback_items, format_brief,
                                    is_recent_item, news_points, point_key, prune_shared_copies, publication_label, single_item_brief)
from app.services.settings import get_setting, set_setting
from app.services.views import product_to_out
from app.services.bot_calendar import calendar_brief, upcoming_entries, refresh_calendar, scheduled_at, normalized_title
from app.services.bot_policy import reserve_delivery
from app.services import bot_channels, bot_custom

logger = logging.getLogger(__name__)

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
    """未合并的即时消息也使用同一短讯格式，来源链接保存在展开资料中。"""
    return format_brief({"mode": "updates", "items": fallback_items([item])})


def format_daily_brief(items: list[dict], now: datetime, limit: int = 3) -> str:
    return format_brief({"title": "今日速报", "date": _fmt_bj(now, "%m/%d"),
                         "items": fallback_items(items[:limit])})


def format_merged_updates(items: list[dict], now: datetime) -> str:
    """合并窗口内多条 updates 压成一条列表，避免连环刷屏。"""
    return format_brief({"mode": "updates", "items": fallback_items(items)})


def collect_images(items: list[dict], limit: int) -> list[str]:
    images: list[str] = []
    for item in items:
        for source in [item, *(item.get("sources") or [])]:
            image = source.get("image")
            if image and image not in images:
                images.append(image)
            if len(images) >= limit:
                return images[:limit]
    return images


def image_limit(config: dict) -> int:
    return max(1, min(9, int(config.get("max_images", 1))))


def _merge_setting_key(target_id: int) -> str:
    return f"{_MERGE_KEY_PREFIX}{target_id}"


async def load_merge_buffer(session: AsyncSession, target_id: int) -> dict:
    stored = (await get_setting(session, _merge_setting_key(target_id))) or {"items": [], "flush_at": None}
    # JSON 字段原地改列表不会触发 SQLAlchemy 更新，返回独立缓冲后再显式保存。
    return {**stored, "items": list(stored.get("items", []))}


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
            BotDelivery.kind.in_(["daily", "updates", "critical", "calendar"]),
            BotDelivery.status.in_(["sending", "accepted", "unknown"]),
            BotDelivery.updated_at >= now - timedelta(hours=1),
        )
    ) or 0


def classify_item(item: dict) -> str:
    """critical 立即推（可穿透静默）；normal 进合并窗口。"""
    if item.get("importance"):
        return "critical"
    return "normal"


def subscribed_communities(target: BotTarget) -> list[str]:
    if target.config.get("channels"):
        return bot_channels.selected_communities(target.config["channels"])
    if selected := target.config.get("communities"):
        return selected
    # 兼容多选上线前的单分区配置。
    community = target.config.get("community", "all")
    return list(COMMUNITIES) if community == "all" else [community]


def matches_target_scope(target: BotTarget, item: dict) -> bool:
    if item.get("custom_feed"):
        return bot_custom.matches_scope(target, item)
    if target.config.get("channels"):
        sources = item.get("sources") or [item]
        selected = set(target.config["channels"])
        return all(selected.intersection(bot_channels.item_channels(source)) for source in sources)
    community = item.get("community")
    if not community:
        return not target.config.get("communities") and target.config.get("community", "all") == "all"
    return community in subscribed_communities(target)


async def load_target_candidates(session, target, keywords, start_at, end_at, *, collected_after=None):
    selected = subscribed_communities(target)
    games = [key for key in selected if COMMUNITIES[key].domain == "game"]
    channels = target.config.get("channels") or []
    candidates = await load_candidates(session, keywords, start_at, end_at,
        preferences={"communities": games}, collected_after=collected_after, text_limit=None,
        extra_conditions=(bot_channels.channel_condition(channels),) if channels else ()) if games else []
    if any(COMMUNITIES[key].domain == "news" for key in selected):
        from app.services.ai_news import load_bot_news

        candidates += await load_bot_news(session, keywords, start_at, end_at, collected_after=collected_after)
    if channels:
        from app.services import bot_products
        candidates = await bot_channels.annotate_candidates(session, candidates)
        revisions = await bot_products.candidates(session, target, keywords, start_at, end_at, collected_after=collected_after)
        # 同一公告同时形成履约修订时只保留状态变化，避免官方频道和商品频道各发一次。
        covered = {cid for item in revisions for cid in item["fulfillment_evidence_ids"]}
        candidates = [item for item in candidates if item["id"] not in covered] + revisions
    candidates += await bot_custom.candidates(session, target, keywords, start_at, end_at, collected_after=collected_after)
    return candidates


async def query_target(session: AsyncSession, target: BotTarget, tool: str = "digest", query: str = "", now=None, collected_after=None,
                       *, shared_copy=False) -> dict:
    now = now or utcnow()
    config = target.config
    selected = subscribed_communities(target)
    community = selected[0] if len(selected) == 1 else "all"
    keywords = config.get("keywords", [])
    scope = "全站" if len(selected) == len(COMMUNITIES) else " / ".join(COMMUNITY_LABELS[key] for key in selected)
    brief = None
    if tool == "trending":
        from app.services.curation import select_trending

        items = await select_trending(session, 5, community=community, hours=24)
        items = [item for item in items if matches_target_scope(target, item)]
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
        entries = await calendar_entries(session, community, now=now,
            extra_conditions=(bot_channels.channel_condition(config["channels"]),) if config.get("channels") else ())
        entries = [entry for entry in entries if entry["community"] in selected]
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
        conditions.append(RedeemCode.community.in_(selected))
        if config.get("channels"):
            conditions.append(bot_channels.channel_condition(config["channels"]))
        rows = await session.execute(
            select(RedeemCode, RawContent.url)
            .join(RawContent, RawContent.id == RedeemCode.raw_content_id)
            .outerjoin(Source, Source.id == RawContent.source_id)
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
        stmt = stmt.where(Product.community.in_(selected))
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
        candidates = await load_target_candidates(
            session, target, keywords or None, now - timedelta(hours=24 if tool == "digest" else 168), now,
            collected_after=collected_after,
        )
        if tool == "digest" and not collected_after:
            # 晚采到的官方资料最多补一条，发布时间仍取原文，不回灌即时推送。
            supplements = await load_target_candidates(session, target, keywords or None, now - timedelta(days=7),
                                                      now - timedelta(hours=24), collected_after=now - timedelta(hours=24))
            candidates = [c for c in candidates if is_recent_item(c, now)]
            candidates.extend({**c, "is_supplement": True} for c in supplements
                              if c.get("published_at") and c["collected_at"] < now and c["identity"] == "official")
            if any(COMMUNITIES[key].domain == "news" for key in selected):
                from app.services.news_publication import edition_bot_items
                edition = await edition_bot_items(session, now=now, keywords=keywords)
                if edition is not None:
                    candidates = [c for c in candidates if not c.get("news")] + edition
        if query:
            candidates = [c for c in candidates if query.casefold() in (c["title"] + c["text"]).casefold()]
        candidates = [c for c in candidates if matches_target_scope(target, c)]
        candidates = [c for c in candidates if c["strength"] == "strong"]
        items = await group_candidates(session, [item for item in candidates if not item.get("news") and not item.get("fulfillment_revision") and not item.get("custom_feed")],
                                       use_event_summary=not bool(config.get("channels")))
        items += [item for item in candidates if item.get("news") or item.get("fulfillment_revision") or item.get("custom_feed")]
        if collected_after:
            community_channels = {key for key in config.get("channels", []) if ":community:" in key}
            items = [i for i in items if is_recent_item(i, now) and (
                i.get("news") or i["identity"] == "official" or i["importance"]
                or community_channels.intersection(bot_channels.item_channels(i)))]
        elif tool == "digest":
            known = await known_notification_keys(session, target.id, now)
            supplements = [await identify_item(session, i) for i in items if i.get("is_supplement")]
            items = [i for i in items if not i.get("is_supplement")]
            items += [i for i in supplements if not item_was_notified(i, known)][:1]
        lines = [f"{scope} · {'重要动态' if collected_after else TOOLS[tool]}"]
        for i, item in enumerate(items[:5], 1):
            label = COMMUNITY_LABELS.get(item.get("community"), "")
            lines.extend([f"\n{i}. [{label}] {item['title']}", (item.get("summary") or item["text"])[:240]])
            when = publication_label(item)
            lines.append(f"来源：{item.get('source') or item['platform']}" + (f" · {when}" if when else ""))
            if item.get("url"):
                lines.append(item["url"])
        if len(items) > 5:
            lines.append(f"\n本次共 {len(items)} 条，以上为前 5 条精选。")
        if not items:
            lines.append("当前范围内没有匹配的新资讯。")
        if not collected_after:
            editor = build_shared_brief if shared_copy else build_brief
            brief = await editor(session, items, now, mode="daily" if tool == "digest" else "updates")
            items = brief["items"]
            lines = [format_brief(brief)]
    return {"text": "\n".join(lines), "items": items,
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


async def _with_repost_keys(session, items: list[dict]) -> list[dict]:
    """按 B 站原帖关联官方动态；旧投递快照也能直接使用已保存的 orig_id。"""
    ids = {item["id"] for item in items if isinstance(item.get("id"), int)
           and item.get("identity") == "official" and not item.get("repost_key")
           and not item.get("news") and not item.get("fulfillment_revision")}
    if not ids:
        return items
    rows = await session.execute(select(RawContent.id, RawContent.community, RawContent.external_id,
        RawContent.raw_json["orig_id"].as_string()).where(
            RawContent.id.in_(ids), RawContent.platform == "bilibili", RawContent.content_type == "dynamic"))
    keys = {cid: (community, f"repost:{community}:bilibili:{original or external}")
            for cid, community, external, original in rows}
    return [{**item, "repost_key": keys[item["id"]][1]}
            if item.get("identity") == "official" and not item.get("news") and not item.get("fulfillment_revision")
            and item.get("id") in keys and item.get("community") == keys[item["id"]][0]
            else item for item in items]


async def known_notification_keys(session, target_id: int, now=None, *, before_delivery_id=None) -> set[str]:
    now = now or utcnow()
    rows = list(await session.scalars(select(BotDelivery).where(BotDelivery.target_id == target_id,
        BotDelivery.kind.in_(["updates", "critical", "daily"]),
        BotDelivery.status.in_(["pending", "failed", "sending", "accepted", "unknown"]),
        *([or_(BotDelivery.id < before_delivery_id, BotDelivery.status.in_(["sending", "accepted", "unknown"]))]
          if before_delivery_id is not None else []))))
    keys = set()
    sources = [source for row in rows if row.created_at >= now - timedelta(days=7)
               for item in (row.brief or {}).get("items", []) for source in (item.get("sources") or [item])]
    for source in await _with_repost_keys(session, sources):
        if source.get("repost_key"):
            keys.add((await identify_item(session, source))["dedupe"])
    legacy_content_ids = set()
    legacy_event_ids = set()
    for row in rows:
        brief = row.brief or {}
        recent = row.created_at >= now - timedelta(hours=72)
        keys.update(key for key in brief.get("notification_keys", []) if not key.startswith("topic:") or recent)
        if not brief.get("notification_keys"):
            for item in brief.get("items", []):
                # 用来源原始标题，不把 AI 编辑后的短标题当作主题身份。
                if recent:
                    keys.update(topic for source in (item.get("sources") or [item]) if (topic := notification_topic(source)))
                for source in [item, *item.get("sources", [])]:
                    if source.get("dedupe"):
                        keys.add(source["dedupe"])
                        continue
                    # 旧快照没有结论指纹，按精确事件/内容身份降噪；新快照始终按结论指纹。
                    if source.get("event_id") and recent:
                        legacy_event_ids.add(source["event_id"])
                    ids = source.get("content_ids") or ([source["id"]] if source.get("id") else [])
                    if recent:
                        legacy_content_ids.update(cid for cid in ids if isinstance(cid, int))
                    keys.update(hashlib.sha256(f"content:{cid}".encode()).hexdigest()[:32] for cid in ids)
        if row.dedupe_key.startswith((f"updates:{target_id}:", f"critical:{target_id}:")):
            keys.add(row.dedupe_key.rsplit(":", 1)[-1])
    if legacy_content_ids:
        events = await session.scalars(select(Evidence.event_id).join(Event, Event.id == Evidence.event_id).where(
            Evidence.raw_content_id.in_(legacy_content_ids), Evidence.role != "excluded", Event.is_active.is_(True)))
        legacy_event_ids.update(events)
    if legacy_event_ids:
        # 商品履约的结论会真正推进，不能用旧快照永久屏蔽整个商品事件。
        events = await session.scalars(select(Event.id).where(Event.id.in_(legacy_event_ids), Event.identity == "official", Event.product_id.is_(None)))
        keys.update(f"legacy-event:{eid}" for eid in events)
    return keys


async def identify_item(session, item: dict) -> dict:
    facts = point_key(item.get("source_title") or item.get("title") or "") + "\n" + "\n".join(sorted(point_key(point) for point in news_points(item)))
    # 事件结论常是固定模板；用实际来源事实辨认修订，改写摘要本身不触发续报。
    identity = f"event:{item['event_id']}:{facts}" if item.get("event_id") else f"content:{item['id']}"
    if item.get("repost_key"):
        # 原帖和转发的标题、账号不同，但同一原帖的相同事实只推一次。
        identity = item["repost_key"] + ":" + "\n".join(sorted(point_key(point) for point in news_points(item)))
    if item.get("news", {}).get("engine") == "aihot" and item["news"].get("fact_id"):
        identity = f"aihot-fact:{item['news']['fact_id']}"
    return {**item, "dedupe": hashlib.sha256(identity.encode()).hexdigest()[:32],
            "topic_key": item.get("repost_key") or notification_topic(item)}


def notification_topic(item: dict) -> str | None:
    """官方同标题转发的主题键；使用方只保留 72 小时，短泛化标题不用。"""
    title = normalized_title(item.get("source_title") or item.get("title") or "")
    if item.get("identity") != "official" or len(title) < 12:
        return None
    identity = f"topic:{item.get('community')}:{title}"
    return "topic:" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def item_was_notified(item: dict, known: set[str]) -> bool:
    return (item.get("dedupe") in known or (not item.get("is_followup") and (
            item.get("topic_key") in known or notification_topic(item) in known
            or (item.get("event_id") and f"legacy-event:{item['event_id']}" in known))))


def update_topic(item: dict) -> str:
    return update_topics(item)[0]


def update_topics(item: dict) -> list[str]:
    return list(dict.fromkeys(filter(None, (
        item.get("repost_key"),
        f"{item.get('community', '')}:event:{item['event_id']}" if item.get("event_id") else None,
        notification_topic(item), f"{item.get('community', '')}:content:{item.get('id')}",
    ))))


async def prepare_update_items(session, target_id: int, items: list[dict], now: datetime, known: set[str]) -> list[dict]:
    """按本群已送达的来源快照对比；其他群、失败消息和人工查询不充当已知信息。"""
    if not items:
        return []
    briefs = await session.scalars(select(BotDelivery.brief).where(
        BotDelivery.target_id == target_id, BotDelivery.status == "accepted",
        BotDelivery.kind.in_(["daily", "updates", "critical"]),
        BotDelivery.updated_at >= now - timedelta(days=7),
    ).order_by(BotDelivery.updated_at.desc(), BotDelivery.id.desc()).limit(200))
    sources = [source for brief in briefs for item in (brief or {}).get("items", [])
               for source in (item.get("sources") or [item])]
    previous, history_points = {}, {}
    for source in await _with_repost_keys(session, sources):
        for topic in update_topics(source):
            previous.setdefault(topic, source)
            points = history_points.setdefault(topic, {})
            for point in [source.get("title") or "", *news_points(source)]:
                points.setdefault(point_key(point), point)
    prepared = []
    seen = set(known)
    for item in await _with_repost_keys(session, items):
        item = await identify_item(session, item)
        topic = next((topic for topic in update_topics(item) if topic in previous), None)
        old = previous.get(topic)
        if old:
            if item.get("repost_key") and item["repost_key"] == old.get("repost_key"):
                # 转发导语或标题变化不算新增事实，也不能把早先发过的事实重新带回来。
                changes = [point for point in news_points(item) if point_key(point) not in history_points[topic]]
            else:
                changes = changed_points(item, old)
            if not changes:
                continue
            item = {**item, "is_followup": True, "key_points": news_points(item),
                    "new_points": changes, "previous_points": list(history_points[topic].values())[:18]}
        if not item_was_notified(item, seen):
            prepared.append(item)
            seen.add(item["dedupe"])
    return prepared


def notification_brief(brief: dict, items: list[dict]) -> dict:
    """主题指纹独立于编辑后的标题；不因补来源或缩写标题重复播报。"""
    sources = [source for item in brief.get("items", []) for source in (item.get("sources") or [item])]
    selected_ids = {cid for source in sources for cid in (source.get("content_ids") or ([source["id"]] if source.get("id") else []))}
    selected_keys = {source["dedupe"] for source in sources if source.get("dedupe")}
    selected = [item for item in items if item.get("id") in selected_ids or item.get("dedupe") in selected_keys]
    keys = [item["dedupe"] for item in selected if item.get("dedupe")]
    keys.extend(topic for source in sources if (topic := notification_topic(source)))
    return {**brief, "notification_keys": list(dict.fromkeys(keys))}


async def deliver(session, target, row, *, force=False, now=None):
    if not target.enabled and not force:
        return row
    # 人格播报由 AstrBot 插件领取，使用它实际配置的人格与模型。
    if target.config.get("delivery_mode") == "astrbot":
        return row
    if not await reserve_delivery(session, target, row, now=now, force=force):
        return row
    status, error = await send_message(await get_setting(session, "bot_bridge") or {}, target.umo, row.body)
    row.status, row.error, row.updated_at = status, error, now or utcnow()
    await session.commit()
    return row


async def prepare_delivery(session: AsyncSession, row: BotDelivery, *, target=None, now=None, commit=True) -> bool:
    """发送前重新检查开关、时效和日程，不补发过期快照。"""
    now = now or utcnow()
    target = target or await session.get(BotTarget, row.target_id)
    config = target.config
    brief = dict(row.brief or {})
    reason = None
    if row.kind in {"updates", "critical", "calendar"} and not config.get("updates_enabled"):
        reason = "此会话已关闭自动动态，取消积压消息"
    elif row.kind == "daily" and not config.get("daily_enabled", True):
        reason = "此会话已关闭早报"
    elif row.kind == "calendar" and not config.get("calendar_enabled", True):
        reason = "此会话已关闭日程提醒"
    elif row.kind in {"updates", "critical"} and row.created_at <= now - timedelta(hours=12):
        reason = "动态已超过 12 小时发送窗口"
    elif row.kind == "daily":
        due = datetime.fromisoformat(brief["scheduled_at"]) if brief.get("scheduled_at") else scheduled_at(row.created_at, config.get("daily_time", "08:00"))
        if not due <= now < due + timedelta(hours=2):
            reason = "已错过早报发送窗口，不补发"
    if brief.get("expires_at") and datetime.fromisoformat(brief["expires_at"]) <= now:
        reason = "消息已过有效期，不再补发"
    if not reason and row.kind == "critical" and (brief.get("code") or row.dedupe_key.startswith("code:")):
        code_value = brief.get("code") or row.dedupe_key.split(":")[2]
        community = row.dedupe_key.split(":")[1]
        code = await session.scalar(select(RedeemCode).where(RedeemCode.code == code_value, RedeemCode.community == community))
        if not code or code.status != "active" or (code.expires_at and code.expires_at <= now):
            reason = "兑换码已失效，不再补发"
    if not reason and row.kind == "calendar":
        if brief.get("mode") == "calendar":
            due = datetime.fromisoformat(brief["scheduled_at"])
            refreshed = await refresh_calendar(session, brief, now, channels=config.get("channels", [])) if due <= now < due + timedelta(hours=2) else None
            if refreshed:
                row.body, row.brief = refreshed
            else:
                reason = "清单已过期或日程已变更，不再补发"
        else:
            # 兼容部署前已入队的 calendar:target:schedule:stage。
            parts = row.dedupe_key.split(":")
            schedule = await session.get(ContentSchedule, int(parts[2])) if len(parts) == 4 and parts[2].isdigit() else None
            due = (schedule.start_at if parts[-1] == "maintenance_start" else schedule.end_at) if schedule else None
            if not due or due <= now or schedule.kind == "broadcast":
                reason = "旧日历事项已过期，不再补发"
    if reason:
        row.status, row.receipt, row.error, row.updated_at = "cancelled", None, reason, now
        if commit:
            await session.commit()
        return False
    if row.kind not in {"daily", "updates", "critical"} or not row.brief:
        return True
    items = row.brief.get("items") or []
    scoped = [item for item in items if matches_target_scope(target, item)]
    if config.get("channels"):
        def ids(item):
            return {cid for source in (item.get("sources") or [item])
                    for cid in (source.get("content_ids") or [source.get("id")]) if isinstance(cid, int)}
        raw_ids = list({cid for item in scoped for cid in ids(item)})
        allowed = await bot_channels.allowed_content_ids(session, config["channels"], raw_ids)
        scoped = [item for item in scoped if ids(item) <= allowed]
    usable = [item for item in scoped if not is_empty_dynamic_item(item)]
    from app.services.bot_products import valid_item
    verified_items = []
    for item in usable:
        revisions = [source for source in (item.get("sources") or [item]) if source.get("fulfillment_revision") or source.get("custom_feed")]
        valid = True
        for source in revisions:
            check = bot_custom.valid_item if source.get("custom_feed") else valid_item
            if not await check(session, source):
                valid = False
                break
        if valid:
            verified_items.append(item)
    usable = verified_items
    invalid_review = False
    if any(item.get("news") for item in usable):
        from app.services.ai_news import approved_news_query, news_review_identity
        from app.services import aihot

        native = [i for i in usable if i.get("news", {}).get("engine") == "aihot"]
        if native:
            try:
                native_approved = await aihot.validate_bot_items(native) if aihot.enabled() else set()
            except Exception:
                return False
        else:
            native_approved = set()
        query = (await approved_news_query(session)).where(RawContent.id.in_([item["id"] for item in usable if isinstance(item["id"], int)]))
        approved = {raw.id: review.news for raw, version, source, review in (await session.execute(query)).all()}
        def same_review(item):
            if item.get("news", {}).get("engine") == "aihot":
                return item["id"] in native_approved
            if aihot.enabled():
                return False
            return item["id"] in approved and news_review_identity(item["news"]) == news_review_identity(approved[item["id"]])
        invalid_review = any(item.get("news") and not same_review(item) for item in usable)
        usable = [item for item in usable if not item.get("news") or same_review(item)]
    empty_sources = any(is_empty_dynamic_item(item) for item in scoped)
    # 早报拆条和兑换码各自有有效期；普通即时消息不能用刚入队的时间续命。
    if row.kind in {"updates", "critical"} and not brief.get("scheduled_at") and brief.get("mode") != "notice":
        usable = [item for item in usable if is_recent_item(item, now)]
    duplicate_reposts = False
    if row.kind in {"updates", "critical"} and not brief.get("scheduled_at"):
        sources = [source for item in usable for source in (item.get("sources") or [item])]
        linked = await _with_repost_keys(session, sources)
        if any(source.get("repost_key") for source in linked):
            known = await known_notification_keys(session, target.id, now, before_delivery_id=row.id)
            duplicates = {id(source) for source, enriched in zip(sources, linked)
                          if enriched.get("repost_key") and (await identify_item(session, enriched))["dedupe"] in known}
            filtered = [item for item in usable if not all(id(source) in duplicates for source in (item.get("sources") or [item]))]
            duplicate_reposts = len(filtered) < len(usable)
            usable = filtered
    if any(item.get("news") for item in usable) and not brief.get("news_dedupe_checked"):
        # 去重需要模型比较事实，在 worker 中完成，不能占着领取行锁等待模型。
        return False
    if len(usable) == len(items) and (usable or row.brief.get("mode") == "notice"):
        # 已排队的早报也使用当前事实模板；不把旧交互提示继续送给插件。
        if brief.get("mode") == "daily":
            row.body = format_brief(brief)
            if commit:
                await session.commit()
        return True
    if usable:
        brief = notification_brief({**row.brief, "items": usable},
                                   [source for item in usable for source in (item.get("sources") or [item])])
        if brief.get("mode") == "single":
            item = dict(usable[0])
            if isinstance(item.get("published_at"), str):
                item["published_at"] = datetime.fromisoformat(item["published_at"])
            body = format_short_update(item)
        else:
            body = format_brief(brief)
        values = {"brief": brief, "body": body, "media": collect_images(usable, image_limit(config)) or None}
    else:
        values = {"status": "cancelled", "receipt": None, "updated_at": now,
                  "error": "同一原帖的相同事实已推送或已排队，取消重复转发" if duplicate_reposts else
                           "消息已不符合此会话订阅范围或审核结果" if invalid_review or len(scoped) < len(items) else
                           "来源动态正文和图片均为空，已取消本次推送" if empty_sources else "来源发布时间未知或已超过 24 小时，不再作为新动态推送"}
    for key, value in values.items():
        setattr(row, key, value)
    if commit:
        await session.commit()
    return bool(usable)


async def _flush_merge_buffer(session, target, buffer: dict, now: datetime, *, quiet: bool, quiet_critical: bool,
                              hourly_limit: int, max_images: int) -> None:
    known = await known_notification_keys(session, target.id, now)
    previously_known = set(known)
    usable = []
    for item in buffer.get("items", []):
        if not matches_target_scope(target, item):
            continue
        buffered_at = datetime.fromisoformat(item.get("buffered_at") or item.get("published_at") or now.isoformat())
        if item.get("dedupe") in known or buffered_at <= now - timedelta(hours=12) or not is_recent_item(item, now):
            continue
        usable.append(item)
        known.update(key for key in (item.get("dedupe"),) if key)
    buffer = {**buffer, "items": await prepare_update_items(session, target.id, usable, now, previously_known)}
    if not buffer["items"]:
        await save_merge_buffer(session, target.id, {"items": [], "flush_at": None})
        return
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
        items = buffer["items"]
        fresh_official = [item for item in items if item.get("identity") == "official" and not item.get("is_followup")]
        brief = await build_shared_brief(session, items, now)
        brief = notification_brief(brief, items)
        if brief["items"]:
            selected_keys = set(brief["notification_keys"])
            window_key = hashlib.sha256(
                (f"merge:{target.id}:" + "".join(sorted(i["dedupe"] for i in items if i["dedupe"] in selected_keys))).encode()
            ).hexdigest()[:32]
            await create_delivery(session, target, "updates", format_brief(brief), f"updates:{target.id}:{window_key}",
                                  media=collect_images(brief["items"], max_images), brief=brief)
            # 每批文案有条数上限，只移除实际入队的来源，其余消息留待下一批。
            remaining = [item for item in items if item["dedupe"] not in selected_keys]
        else:
            # 社区内容和重复续报仍可被编辑筛掉；无法生成原文短讯的官方新消息保留重试。
            remaining = fresh_official
        logger.info("Bot merge target=%s candidates=%d selected=%d remaining=%d",
                    target.id, len(items), len(brief["items"]), len(remaining))
        await save_merge_buffer(session, target.id, {
            "items": remaining, "flush_at": (now + timedelta(minutes=5)).isoformat() if remaining else None,
        })
    else:
        buffer["flush_at"] = (now + timedelta(minutes=5)).isoformat()
        await save_merge_buffer(session, target.id, buffer)


async def run_bot_notifications(session: AsyncSession, now=None) -> dict:
    now = now or utcnow()
    local = now + timedelta(hours=8)
    # 进程在发送中退出时无法判定远端结果，绝不盲目自动重发。
    await session.execute(update(BotDelivery).where(BotDelivery.status == "sending", BotDelivery.updated_at < now - timedelta(minutes=5)).values(
        status="unknown", error="发送过程中进程中断，请核对收件端后再重发", updated_at=now))
    await prune_shared_copies(session, now)
    await session.commit()
    ids = list(await session.scalars(select(BotTarget.id).where(BotTarget.enabled.is_(True))))
    processed = 0
    for target_id in ids:
        target = await session.get(BotTarget, target_id)
        config = target.config
        quiet = _is_quiet(local.hour, config)
        quiet_critical = bool(config.get("quiet_critical", False))
        merge_minutes = int(config.get("merge_minutes", 0) or 0)
        hourly_limit = int(config.get("hourly_limit", 2) or 0)
        max_images = image_limit(config)
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
            if due <= now < due + timedelta(hours=2) and target.created_at <= due:
                key = f"daily:{target.id}:{local.date()}"
                if not await session.scalar(select(BotDelivery.id).where(BotDelivery.dedupe_key == key)):
                    payload = await query_target(session, target, now=due, shared_copy=True)
                    items = payload.get("items") or []
                    style = config.get("daily_style", "compact")
                    covers = collect_images(items, max_images)
                    if style == "split" and items:
                        head = payload["text"].split("\n", 2)[0]
                        lifespan = {"scheduled_at": due.isoformat(), "expires_at": (due + timedelta(hours=2)).isoformat()}
                        await create_delivery(session, target, "daily", f"{head}\n共 {len(items)} 条重点，详情如下", key, media=covers,
                                              brief={**lifespan, "mode": "notice", "items": []})
                        for idx, item in enumerate(items[:2], 1):
                            await create_delivery(
                                session, target, "updates",
                                format_short_update(item),
                                f"daily:{target.id}:{local.date()}:{idx}",
                                media=[item["image"]] if item.get("image") else None,
                                brief={**single_item_brief(item), **lifespan},
                            )
                    else:
                        brief = payload.get("brief") or await build_shared_brief(session, items, due, mode="daily")
                        identified = [await identify_item(session, item) for item in items if item.get("id")]
                        brief = notification_brief(brief, identified)
                        brief = {**brief, "scheduled_at": due.isoformat(), "expires_at": (due + timedelta(hours=2)).isoformat()}
                        if brief["items"]:
                            await create_delivery(session, target, "daily", format_brief(brief), key, media=covers, brief=brief)
        if config.get("updates_enabled") and config.get("calendar_enabled", True) and not quiet:
            due = scheduled_at(now, config.get("calendar_time", "20:00"))
            key = f"calendar-digest:{target.id}:{local.date()}"
            if due <= now < due + timedelta(hours=2) and not await session.scalar(select(BotDelivery.id).where(BotDelivery.dedupe_key == key)):
                selected = subscribed_communities(target)
                entries = await upcoming_entries(session, selected[0] if len(selected) == 1 else "all", now, channels=config.get("channels", []))
                entries = [entry for entry in entries if entry["community"] in selected]
                if entries:
                    text, brief = calendar_brief(entries, due)
                    await create_delivery(session, target, "calendar", text, key, brief=brief)
        buffer = await load_merge_buffer(session, target.id)
        if config.get("updates_enabled"):
            payload = await query_target(session, target, now=now, collected_after=target.last_scan_at)
            critical_items = []
            normal_items = []
            already = await known_notification_keys(session, target.id, now)
            candidates = [item for item in payload["items"] if is_recent_item(item, now)]
            for item in await prepare_update_items(session, target.id, candidates, now, already):
                if classify_item(item) == "critical":
                    critical_items.append(item)
                else:
                    normal_items.append(item)
            for kind, immediate in (("critical", critical_items), ("updates", normal_items if merge_minutes <= 0 else [])):
                for item in immediate:
                    brief = notification_brief(await build_shared_brief(session, [item], now), [item])
                    if brief["items"]:
                        await create_delivery(session, target, kind, format_brief(brief),
                                              f"{kind}:{target.id}:{item['dedupe']}",
                                              media=collect_images(brief["items"], max_images), brief=brief)
            if normal_items and merge_minutes > 0:
                known = {key for entry in buffer.get("items", []) for key in (entry.get("dedupe"), entry.get("topic_key") or notification_topic(entry)) if key}
                for item in normal_items:
                    digest = item["dedupe"]
                    older = next((entry for entry in buffer.get("items", []) if update_topic(entry) == update_topic(item)), None)
                    if older:
                        if not changed_points(item, older) or _source(item)["published_at"] < older["published_at"]:
                            continue
                        buffer["items"].remove(older)
                        known = {key for entry in buffer["items"] for key in (entry.get("dedupe"), entry.get("topic_key")) if key}
                    if item_was_notified(item, known):
                        continue
                    known.update(key for key in (digest, item.get("topic_key")) if key)
                    buffer.setdefault("items", []).append({**_source(item), "topic_key": item.get("topic_key"),
                        "buffered_at": older["buffered_at"] if older else now.isoformat()})
                if buffer["items"] and not buffer.get("flush_at"):
                    buffer["flush_at"] = (now + timedelta(minutes=merge_minutes)).isoformat()
                # 新条目进缓冲后立刻落库，进程重启也不丢。
                await save_merge_buffer(session, target.id, buffer)
            target.last_scan_at = now
            await session.commit()
            if merge_minutes <= 0 and buffer.get("items"):
                buffer["flush_at"] = now.isoformat()
            await _flush_merge_buffer(
                session, target, buffer, now,
                quiet=quiet, quiet_critical=quiet_critical, hourly_limit=hourly_limit, max_images=max_images,
            )
        elif buffer.get("items"):
            await save_merge_buffer(session, target.id, {"items": [], "flush_at": None})
        from app.services.bot_news import dedupe_pending_news

        await dedupe_pending_news(session, target.id, now)
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
            if row.kind == "calendar" and quiet:
                continue
            if row.kind == "critical" and quiet and not quiet_critical:
                continue
            if row.status == "failed" and row.updated_at > now - timedelta(minutes=5):
                continue
            await deliver(session, target, row, now=now)
            processed += 1
    return {"targets": len(ids), "processed": processed}
