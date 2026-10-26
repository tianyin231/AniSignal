"""会话频道：先按来源筛选，再进入事件归并与共用通知管线。"""

from sqlalchemy import and_, exists, or_, select

from app.communities import COMMUNITIES
from app.models import Event, Evidence, RawContent, Source

PLATFORMS = {
    "bilibili": ("B", ("bilibili",)),
    "youtube": ("YB", ("youtube",)),
    "x": ("X", ("x",)),
    "web": ("官网", ("kuro_cn", "kuro_global", "game_official")),
    "miyoushe": ("米游社", ("miyoushe",)),
}


def channel_options() -> dict[str, str]:
    options = {}
    for community, meta in COMMUNITIES.items():
        if meta.domain == "news":
            options[f"{community}:news"] = f"{meta.label}_快讯"
            continue
        for identity, label in (("official", "官方"), ("community", "社区")):
            for platform, (suffix, _) in PLATFORMS.items():
                options[f"{community}:{identity}:{platform}"] = f"{meta.label}_{label}_{suffix}"
        options[f"{community}:product"] = f"{meta.label}_商品"
    return options


def selected_communities(channels: list[str]) -> list[str]:
    return list(dict.fromkeys(key.split(":", 1)[0] for key in channels))


def product_content_condition():
    return exists(select(Evidence.id).join(Event, Event.id == Evidence.event_id).where(
        Evidence.raw_content_id == RawContent.id, Evidence.role != "excluded",
        Event.product_id.is_not(None), Event.is_active.is_(True), Event.community == RawContent.community,
    ))


def channel_condition(channels: list[str]):
    conditions = []
    for key in channels:
        community, kind, *platform = key.split(":")
        scope = [RawContent.community == community]
        if kind == "product":
            scope.append(product_content_condition())
        elif kind != "news":
            scope += [RawContent.platform.in_(PLATFORMS[platform[0]][1]),
                      Source.identity == "official" if kind == "official" else or_(Source.identity != "official", Source.id.is_(None))]
        conditions.append(and_(*scope))
    return or_(*conditions)


def item_channels(item: dict) -> list[str]:
    if item.get("notification_channels") is not None:
        return item["notification_channels"]
    community = item.get("community")
    if not community or community not in COMMUNITIES:
        return []
    if COMMUNITIES[community].domain == "news":
        return [f"{community}:news"]
    identity = "official" if item.get("identity") == "official" else "community"
    platform = next((key for key, (_, members) in PLATFORMS.items() if item.get("platform") in members), None)
    return [f"{community}:{identity}:{platform}"] if platform else []


async def annotate_candidates(session, candidates: list[dict]) -> list[dict]:
    missing = {item["id"]: item for item in candidates if isinstance(item.get("id"), int) and not item.get("platform")}
    if missing:
        rows = await session.execute(select(RawContent.id, RawContent.platform, Source.identity, RawContent.community)
            .outerjoin(Source, Source.id == RawContent.source_id).where(RawContent.id.in_(missing)))
        for rid, platform, identity, community in rows:
            missing[rid].update(platform=platform, identity=identity or "unknown", community=community)
    ids = [item["id"] for item in candidates if isinstance(item.get("id"), int)]
    products = set(await session.scalars(select(RawContent.id).where(RawContent.id.in_(ids), product_content_condition()))) if ids else set()
    for item in candidates:
        item["notification_channels"] = list(item_channels(item))
        if item.get("id") in products and f"{item['community']}:product" not in item["notification_channels"]:
            item["notification_channels"].append(f"{item['community']}:product")
    return candidates


async def allowed_content_ids(session, channels: list[str], content_ids: list[int]) -> set[int]:
    if not channels:
        return set(content_ids)
    return set(await session.scalars(select(RawContent.id).outerjoin(Source, Source.id == RawContent.source_id).where(
        RawContent.id.in_(content_ids), RawContent.hidden.is_(False), channel_condition(channels),
    )))
