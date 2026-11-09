"""账号的网站浏览屏蔽：游戏分区与来源平台，邮件、Bot 与关注流不受影响。

- 分区屏蔽只作用于跨分区聚合列表；直接打开该分区照常显示。
- 来源平台屏蔽按「游戏分区」「AI 快讯」两组分别设置（同一平台在两边的内容质量差别很大），
  对聚合列表和单个分区都生效；在内容库里显式选中该平台时照常显示。

设置保存在账号 feed_preferences 的 hidden_communities / hidden_platforms 中。浏览器另存一份
镜像 Cookie（分区键与 scope:platform 令牌以点号连接），公开接口和服务端渲染据此过滤，
不必每次请求查库。镜像只决定本人看到的列表，被改写也只会隐藏自己的内容；
登录、退出与会话检查时按账号重新同步。
"""

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import or_

from app.communities import COMMUNITIES, is_all_community
from app.services.auth import SESSION_COOKIE

HIDDEN_COOKIE = "anisignal_hidden"
AI_COMMUNITY = "ai"

# 可屏蔽的来源平台：令牌 → 对应的 raw_contents.platform 取值。
BLOCKABLE_PLATFORMS: dict[str, dict[str, tuple[str, ...]]] = {
    "game": {
        "bilibili": ("bilibili",),
        "youtube": ("youtube",),
        "x": ("x",),
        "kuro": ("kuro", "kuro_cn", "kuro_global"),
        "miyoushe": ("miyoushe",),
        "game_official": ("game_official", "web"),
    },
    AI_COMMUNITY: {
        "bilibili": ("bilibili",),
        "x": ("x",),
        "rss": ("rss",),
    },
}
PLATFORM_TOKENS = tuple(f"{scope}:{key}" for scope, keys in BLOCKABLE_PLATFORMS.items() for key in keys)


def normalize_hidden(values) -> list[str]:
    """只保留合法分区，去重并按分区顺序排列。"""
    wanted = set(values or [])
    return [key for key in COMMUNITIES if key in wanted]


def normalize_platforms(values) -> list[str]:
    """只保留合法的 scope:platform 令牌，去重并按固定顺序排列。"""
    wanted = set(values or [])
    return [token for token in PLATFORM_TOKENS if token in wanted]


@dataclass(frozen=True)
class Blocks:
    communities: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.communities or self.platforms)

    def tokens(self) -> tuple[str, ...]:
        return self.communities + self.platforms

    def scoped(self, community: str | None) -> "Blocks":
        """按请求视角裁剪：单个分区不应用分区屏蔽，也只保留该分区所属一组的平台。"""
        if is_all_community(community):
            return self
        scope = AI_COMMUNITY if community == AI_COMMUNITY else "game"
        return Blocks((), tuple(t for t in self.platforms if t.startswith(scope + ":")))

    def raw_platforms(self, scope: str) -> tuple[str, ...]:
        mapping = BLOCKABLE_PLATFORMS[scope]
        return tuple(raw for token in self.platforms if token.startswith(scope + ":")
                     for raw in mapping[token.split(":", 1)[1]])

    def platform_conditions(self, platform_column, community_column) -> list:
        conditions = []
        if ai := self.raw_platforms(AI_COMMUNITY):
            conditions.append(or_(community_column != AI_COMMUNITY, platform_column.notin_(ai)))
        if game := self.raw_platforms("game"):
            conditions.append(or_(community_column == AI_COMMUNITY, platform_column.notin_(game)))
        return conditions


def parse_blocks(values) -> Blocks:
    values = list(values or [])
    return Blocks(tuple(normalize_hidden(values)), tuple(normalize_platforms(values)))


def account_blocks(user) -> Blocks:
    preferences = (user.feed_preferences or {}) if user is not None else {}
    return Blocks(tuple(normalize_hidden(preferences.get("hidden_communities"))),
                  tuple(normalize_platforms(preferences.get("hidden_platforms"))))


def account_hidden(user) -> list[str]:
    return list(account_blocks(user).communities)


def hidden_cookie_value(blocks: Blocks) -> str:
    return ".".join(blocks.tokens())


def request_blocks(request: Request | None) -> Blocks:
    # 没有登录 Cookie 时不过滤：退出或会话过期后残留的镜像不能影响访客视图。
    if request is None or not request.cookies.get(SESSION_COOKIE):
        return Blocks()
    return parse_blocks((request.cookies.get(HIDDEN_COOKIE) or "").split("."))


def request_hidden(request: Request | None) -> tuple[str, ...]:
    return request_blocks(request).communities
