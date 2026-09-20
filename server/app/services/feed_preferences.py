"""关注流和个人邮件共用过滤条件，空游戏列表表示全部游戏。"""

from sqlalchemy import func

from app.models import ContentVersion, RawContent


def preference_conditions(preferences: dict | None) -> list:
    preferences = preferences or {}
    conditions = []
    if communities := preferences.get("communities"):
        conditions.append(RawContent.community.in_(communities))
    title = func.lower(func.coalesce(ContentVersion.title, ""))
    for keyword in preferences.get("exclude_keywords", []):
        conditions.append(~title.contains(keyword.lower(), autoescape=True))
    return conditions
