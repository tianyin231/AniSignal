"""账号屏蔽的游戏分区：只影响网站上的跨分区聚合列表，邮件、Bot 与关注流不受影响。

屏蔽列表保存在账号 feed_preferences.hidden_communities 中。浏览器另存一份镜像 Cookie，
公开接口和服务端渲染据此过滤，不必每次请求查库。镜像只决定本人看到的列表，
被改写也只会隐藏自己的内容；登录、退出与会话检查时按账号重新同步。
"""

from fastapi import Request

from app.communities import COMMUNITIES
from app.services.auth import SESSION_COOKIE

HIDDEN_COOKIE = "anisignal_hidden"


def normalize_hidden(values) -> list[str]:
    """只保留合法分区，去重并按分区顺序排列。"""
    wanted = set(values or [])
    return [key for key in COMMUNITIES if key in wanted]


def account_hidden(user) -> list[str]:
    preferences = (user.feed_preferences or {}) if user is not None else {}
    return normalize_hidden(preferences.get("hidden_communities"))


def hidden_cookie_value(hidden) -> str:
    return ".".join(hidden)


def request_hidden(request: Request) -> tuple[str, ...]:
    # 没有登录 Cookie 时不过滤：退出或会话过期后残留的镜像不能影响访客视图。
    if not request.cookies.get(SESSION_COOKIE):
        return ()
    return tuple(normalize_hidden((request.cookies.get(HIDDEN_COOKIE) or "").split(".")))
