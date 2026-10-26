"""原神与终末地公告映射；原始发布时刻、官网正文和原链均来自官方接口。"""

import html
import json
import re
from datetime import datetime, timedelta, timezone

from app.communities import get_community
from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "1"


def parse_notice(raw: dict) -> NormalizedContent:
    game, article = raw["game"], raw["article"]
    if game == "genshin":
        cid, title, text = str(article["iInfoId"]), article["sTitle"], article.get("sContent") or ""
        value = article.get("dtStartTime")
        published = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc) if value else None
        url = f"https://ys.mihoyo.com/main/news/detail/{cid}"
        extra = json.loads(article.get("sExt") or "{}")
        covers = [item["url"] for values in extra.values() if isinstance(values, list)
                  for item in values if isinstance(item, dict) and item.get("url")]
    else:
        cid, title, text = str(article["cid"]), article["title"], article.get("data") or article.get("brief") or ""
        published = datetime.fromtimestamp(int(article["displayTime"]), tz=timezone.utc) if article.get("displayTime") else None
        url = f"https://endfield.hypergryph.com/news/{cid}"
        covers = [article["cover"]] if article.get("cover") else []
    images = [html.unescape(value) for value in re.findall(r'<img[^>]+src=[\"\']([^\"\']+)', text, re.I)]
    images = ["https:" + value if value.startswith("//") else value for value in [*covers, *images]]
    return NormalizedContent(
        platform=Platform.GAME_OFFICIAL, content_type=ContentType.NOTICE,
        external_id=f"{game}:{cid}", title=title, text=text, url=url, published_at=published,
        author=NormalizedAuthor(external_id=game, name=f"{get_community(game).label}官网", identity=Identity.OFFICIAL,
                                official_title=f"{get_community(game).game_name}官方网站"),
        media=list(dict.fromkeys(value for value in images if value.startswith(("https://", "http://")))),
        metrics={}, extra={"language": "zh-cn", "region": "cn"},
    )
