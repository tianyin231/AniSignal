"""kuro / miyoushe / youtube 解析器单测（样例结构按 data-sources.md 与接口文档整理）。"""

from app.parsers.kuro import parse_notice
from app.parsers.miyoushe import parse_post
from app.parsers.youtube import parse_rss_item

KURO_ITEM = {
    "platform": "kuro_global",
    "entry": {
        "id": "50553",
        "tabTitle": {
            "en": "Version Update Notice",
            "zh-Hans": "版本更新维护公告",
        },
        "tabBanner": {"en": "https://example.com/banner.png"},
        "startTimeMs": 1786000000000,
    },
    "detail": {"noticeId": 50553, "textContent": "<p>html body</p>"},
    "list_url": "https://aki-gm-resources-back.aki-game.net/gamenotice/G153/x/notice.json",
    "detail_url": "https://aki-gm-resources-back.aki-game.net/gamenotice/content/G153/x/50553/en.json",
}

MIYOUSHE_ITEM = {
    "entry": {
        "post": {
            "post_id": 58957682,
            "subject": "胡桃同人图",
            "content": "正文摘要",
            "created_at": 1786000000,
            "images": ["https://upload-bbs.miyoushe.com/a.jpg"],
        },
        "user": {"uid": 123456, "nickname": "旅行者"},
        "forum": {"name": "同人图"},
        "stat": {"view": 1000, "like": 50, "reply": 10, "favorite": 5, "share": 2},
    },
    "detail": None,
}

YOUTUBE_ITEM = {
    "id": "dQw4w9WgXcQ",
    "channel_id": "UC0Bi5KMcECRVYis5Gb_ZYZQ",
    "title": "Wuthering Waves — Patch Trailer",
    "link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "published": "2026-08-13T12:00:00+00:00",
    "updated": "2026-08-13T12:00:00+00:00",
    "author": "Wuthering Waves",
    "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    "summary": "Take a look at the patch!",
}


def test_parse_kuro_notice():
    c = parse_notice(KURO_ITEM)
    assert c.platform == "kuro_global"
    assert c.content_type == "notice"
    assert c.external_id == "50553"
    assert c.author.identity == "official"
    # 国际服取 en 语言标题
    assert c.title == "Version Update Notice"
    assert c.text == "<p>html body</p>"
    assert c.published_at is not None
    assert c.media == ["https://example.com/banner.png"]
    assert c.metrics == {}


def test_parse_kuro_notice_cn_language_fallback():
    item = dict(KURO_ITEM, platform="kuro_cn")
    c = parse_notice(item)
    # 国服首选 zh-Hans
    assert c.title == "版本更新维护公告"


def test_parse_kuro_notice_without_detail():
    item = dict(KURO_ITEM, detail=None)
    c = parse_notice(item)
    assert c.external_id == "50553"
    assert c.text is None
    assert c.extra["has_detail"] is False


def test_parse_miyoushe_post():
    c = parse_post(MIYOUSHE_ITEM)
    assert c.platform == "miyoushe"
    assert c.external_id == "58957682"
    assert c.author.external_id == "123456"
    assert c.author.name == "旅行者"
    assert c.title == "胡桃同人图"
    assert c.url.endswith("/article/58957682")
    assert c.metrics == {"view": 1000, "like": 50, "reply": 10, "favorite": 5, "share": 2}
    assert c.media == ["https://upload-bbs.miyoushe.com/a.jpg"]


def test_parse_youtube_rss_item():
    c = parse_rss_item(YOUTUBE_ITEM)
    assert c.platform == "youtube"
    assert c.content_type == "rss_item"
    assert c.external_id == "dQw4w9WgXcQ"
    assert c.published_at is not None
    assert c.author.name == "Wuthering Waves"
    assert c.author.external_id == "UC0Bi5KMcECRVYis5Gb_ZYZQ"
    assert c.author.identity == "official"
    assert c.media[0].endswith("hqdefault.jpg")
    assert c.metrics == {}
