"""鉴权只隔离接入实例和真实操作者；订阅策略由用户插件维护。"""


async def target_error(session, target) -> str | None:
    from app.models import BotClient, SiteSetting, User

    client = await session.get(BotClient, target.client_id, populate_existing=True)
    if client:
        user = await session.get(User, client.user_id, populate_existing=True)
        if not client.enabled or not user or user.status != "active" or not user.is_verified:
            return "用户 Token 已撤销或所属账号不可用"
        return None
    row = await session.get(SiteSetting, "bot_clients", populate_existing=True)
    clients = row.value if row else {}
    client = clients.get(target.client_id, {})
    if (not client and target.client_id != "legacy") or not client.get("enabled", True):
        return "插件接入已停用或不存在"
    return None


def actor_error(umo: str, actor_id: str) -> str | None:
    if ":FriendMessage:" in umo and actor_id != umo.split(":", 2)[2]:
        return "只能操作自己的私聊会话"
    return None


def matches_keywords(config: dict, item: dict) -> bool:
    sources = [item, *(item.get("sources") or [])]
    text = " ".join(str(source.get(key) or "") for source in sources for key in ("title", "text", "summary", "name")).casefold()
    if any(word.casefold() in text for word in config.get("exclude_keywords", [])):
        return False
    return not config.get("keywords") or any(word.casefold() in text for word in config["keywords"])
