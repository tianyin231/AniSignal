"""Token 所属账号的可选来源，以及每种通知实际使用的订阅范围。"""

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.communities import COMMUNITIES
from app.custom_presets import CUSTOM_PRESETS, promote_subscriptions
from app.models import BotClient, CustomFeed, User, UserKeyword
from app.services.bot_channels import channel_options, selected_communities


async def client_user(session, client_id):
    client = await session.get(BotClient, client_id)
    return await session.get(User, client.user_id, populate_existing=True) if client else None


def accessible_feeds(user_id=None):
    access = CustomFeed.is_public.is_(True)
    if user_id is not None:
        access = or_(access, CustomFeed.user_id == user_id)
    return select(CustomFeed).where(access, CustomFeed.enabled.is_(True), CustomFeed.status == "active")


async def catalog(session, client_id):
    user = await client_user(session, client_id)
    feeds = list(await session.scalars(accessible_feeds(user.id if user else None).order_by(CustomFeed.id)))
    # 站点预设有稳定标识，不再重复出现在个人/公共监控列表中。
    presets = {(p["platform"], p["kind"], p["target_value"]) for p in CUSTOM_PRESETS.values()}
    feeds = [f for f in feeds if f.user_id is not None or (f.platform, f.kind, f.target_value) not in presets]
    preferences = (user.feed_preferences or {}) if user else {}
    keywords = list(await session.scalars(select(UserKeyword.keyword).where(
        UserKeyword.user_id == user.id, UserKeyword.enabled.is_(True)))) if user else []
    return {
        "communities": {key: meta.label for key, meta in COMMUNITIES.items()},
        "channels": channel_options(),
        "custom_presets": {key: p["name"] for key, p in CUSTOM_PRESETS.items() if not p.get("community")},
        "custom_feeds": [{"id": f.id, "name": f.name, "platform": f.platform,
                          "owned": bool(user and f.user_id == user.id), "public": f.is_public} for f in feeds],
        "preferences": {"communities": preferences.get("communities", []),
                        "exclude_keywords": preferences.get("exclude_keywords", []), "keywords": keywords},
        "account_bound": user is not None,
    }


async def validate_sources(session, client_id, targets):
    ids = {fid for target in targets for fid in target.custom_feed_ids}
    ids.update(int(key[5:]) for target in targets for key in target.daily_sources + target.updates_sources if key.startswith("feed:"))
    if not ids:
        return
    user = await client_user(session, client_id)
    allowed = set(await session.scalars(accessible_feeds(user.id if user else None).with_only_columns(CustomFeed.id).where(CustomFeed.id.in_(ids))))
    if ids - allowed:
        raise HTTPException(403, "定制监控不存在、已停用或不属于此账号的可访问范围")


class ScopedTarget:
    def __init__(self, target, config):
        self.target, self.config = target, config

    def __getattr__(self, name):
        return getattr(self.target, name)


async def resolve_target(session, target, notification=None):
    """不改插件原始配置；账号偏好改变后，查询和发送前立即按新设置收敛。"""
    config = promote_subscriptions(target.config)
    channels = config.get("channels", [])
    mode = config.get("scope_mode", "legacy")
    if mode == "custom":
        communities, channels = [], []
    elif channels:
        communities = selected_communities(channels)
    elif mode == "channels":
        communities = []  # 明确勾选模式下，全部取消不会退回全站。
    else:
        communities = config.get("communities") or ([config["community"]] if config.get("community", "all") != "all" else list(COMMUNITIES))
    user = await client_user(session, target.client_id)
    if user and config.get("inherit_preferences"):
        preferences = user.feed_preferences or {}
        if selected := preferences.get("communities"):
            communities = [key for key in communities if key in selected]
            channels = [key for key in channels if key.split(":", 1)[0] in selected]
        config["exclude_keywords"] = list(dict.fromkeys(config.get("exclude_keywords", []) + preferences.get("exclude_keywords", [])))
    if user and config.get("inherit_keywords"):
        keywords = list(await session.scalars(select(UserKeyword.keyword).where(UserKeyword.user_id == user.id, UserKeyword.enabled.is_(True))))
        config["keywords"] = list(dict.fromkeys(config.get("keywords", []) + keywords))
    selected_sources = config.get(f"{notification}_sources", []) if notification in {"daily", "updates"} else []
    if selected_sources:
        base = channels if channels else ([key for key in channel_options() if key.split(":", 1)[0] in communities] if mode == "legacy" else [])
        channels = [key for key in base if key in selected_sources]
        communities = selected_communities(channels)
        mode = "channels"
        config["custom_feeds"] = [key for key in config.get("custom_feeds", []) if f"preset:{key}" in selected_sources]
        config["custom_feed_ids"] = [fid for fid in config.get("custom_feed_ids", []) if f"feed:{fid}" in selected_sources]
    config.update(channels=channels, scope_mode=mode, _communities=communities)
    return ScopedTarget(target, config)
