"""把小黑盒公开分享链接记录为一条玩家内容引用。"""

import re
from urllib.parse import parse_qs, urlsplit

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "1"
_POST_PATH = "/app/bbs/link/"


def parse_share(raw: dict) -> NormalizedContent:
    url = urlsplit(raw["url"].strip())
    link_id = ""
    if url.scheme == "https":
        if url.netloc in {"xiaoheihe.cn", "www.xiaoheihe.cn"} and url.path.startswith(_POST_PATH):
            link_id = url.path.removeprefix(_POST_PATH).rstrip("/")
        elif url.netloc == "api.xiaoheihe.cn" and url.path == "/v3/bbs/app/api/web/share":
            ids = parse_qs(url.query).get("link_id", [])
            if len(ids) == 1:
                link_id = ids[0]
    if not re.fullmatch(r"[a-zA-Z0-9]{1,48}", link_id):
        raise ValueError("请填写小黑盒公开帖子链接")
    return NormalizedContent(
        platform=Platform.XIAOHEIHE,
        content_type=ContentType.POST,
        external_id=link_id,
        url=f"https://www.xiaoheihe.cn{_POST_PATH}{link_id}",
        title=raw["title"],
        published_at=raw.get("published_at"),
        metrics={key: raw[key] for key in ("like", "reply") if raw.get(key) is not None},
        # 分享链接只提供作者昵称，引用条目不推断跨帖账号身份。
        author=NormalizedAuthor(external_id=f"share:{link_id}", name=raw["author"], identity=Identity.USER),
    )
