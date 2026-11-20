"""小黑盒公开作者发帖列表。每轮仅读首页。"""

import httpx

from app.collectors.base import USER_AGENT, CollectorError, get_json
from app.config import get_settings
from app.models.enums import Identity
from app.parsers.xiaoheihe import parse_account_post
from app.schemas.content import NormalizedAuthor, NormalizedContent

_ACCOUNT_POSTS_URL = "https://api.xiaoheihe.cn/bbs/app/profile/user/link/list_v2"
_ACCOUNT_POSTS_LIMIT = 20


async def _public_posts(uid: str, limit: int) -> tuple[NormalizedAuthor, list[dict]]:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        payload = await get_json(client, _ACCOUNT_POSTS_URL,
                                 params={"userid": uid, "offset": 0, "limit": limit},
                                 retries=0, timeout=get_settings().http_timeout_seconds)
    result = payload.get("result") if isinstance(payload, dict) and payload.get("status") == "ok" else None
    user = result.get("user") if isinstance(result, dict) else None
    author_id = str(user.get("userid") or "") if isinstance(user, dict) else ""
    posts = result.get("post_links") if isinstance(result, dict) else None
    if not (author_id.isascii() and author_id.isdecimal()) or not isinstance(posts, list):
        raise CollectorError("小黑盒账号暂时无法读取，请检查 UID 或稍后重试")
    author = NormalizedAuthor(external_id=author_id, name=str(user.get("username") or "")[:64] or None,
                              identity=Identity.USER)
    return author, posts[:limit]


async def resolve_account_uid(target: str) -> str:
    author, _ = await _public_posts(target, 1)
    return author.external_id


async def collect_account_posts(uid: str) -> list[NormalizedContent]:
    author, posts = await _public_posts(uid, _ACCOUNT_POSTS_LIMIT)
    if author.external_id != uid:
        raise CollectorError("小黑盒返回的作者与监控 UID 不符")
    parsed = {}
    for raw in posts:
        if isinstance(raw, dict) and (post := parse_account_post(raw, author)) is not None:
            parsed.setdefault(post.external_id, post)
    return list(parsed.values())
