"""B 站解析器单测：样例字段来自 data-sources.md 的实测返回（2026-08-13）。"""

from app.parsers.bilibili import parse_dynamic, parse_video

VIDEO_RAW = {
    "bvid": "BV1Mcgp6ME1x",
    "aid": 117088291265434,
    "cid": 40878802947,
    "title": "视频标题",
    "description": "视频简介",
    "owner_mid": 29769961,
    "owner_name": "UP主名称",
    "view_count": 680,
    "danmaku_count": 0,
    "reply_count": 14,
    "favorite_count": 3,
    "coin_count": 6,
    "share_count": 1,
    "like_count": 26,
    "pubdate": 1786625704,
    "pubdate_text": "2026-08-13 12:55:04",
    "duration_seconds": 251,
    "cover_url": "https://i1.hdslb.com/bfs/archive/example.jpg",
    "url": "https://www.bilibili.com/video/BV1Mcgp6ME1x",
    "tags": ["鸣潮", "爱弥斯"],
    "pages": [],
}

DYNAMIC_RAW = {
    "id": "1235883715731652807",
    "type": "DYNAMIC_TYPE_ARTICLE",
    "author_mid": 1955897084,
    "author_name": "鸣潮",
    "pub_ts": 1786590001,
    "pub_text": "10小时前",
    "text": "",
    "pics": [],
    "video_bvid": "",
    "stat_like": 7110,
    "stat_reply": 1714,
    "stat_forward": 49,
}


def test_parse_video_full_mapping():
    c = parse_video(VIDEO_RAW)
    assert c.platform == "bilibili"
    assert c.content_type == "video"
    assert c.external_id == "BV1Mcgp6ME1x"
    assert c.author.external_id == "29769961"
    assert c.author.name == "UP主名称"
    assert c.title == "视频标题"
    assert c.text == "视频简介"
    assert c.url.endswith("BV1Mcgp6ME1x")
    assert c.published_at is not None
    assert c.published_at.year == 2026
    assert c.media == ["https://i1.hdslb.com/bfs/archive/example.jpg"]
    assert c.metrics["view"] == 680
    assert c.metrics["like"] == 26
    assert c.metrics["reply"] == 14
    assert c.extra["tags"] == ["鸣潮", "爱弥斯"]


def test_parse_dynamic_official_author_and_empty_text():
    c = parse_dynamic(DYNAMIC_RAW)
    assert c.content_type == "dynamic"
    assert c.external_id == "1235883715731652807"
    assert c.author.identity == "official"  # 鸣潮官方 mid（配置兜底值）
    # 已知限制：部分动态 text 为空，不能假设正文已展开
    assert c.text is None
    assert c.metrics == {"like": 7110, "reply": 1714, "forward": 49}
    assert c.url == "https://t.bilibili.com/1235883715731652807"


def test_parse_dynamic_derives_title_from_text():
    raw = dict(
        DYNAMIC_RAW,
        type="DYNAMIC_TYPE_AV",
        text="#鸣潮# #鸣潮3.6版本#\n《鸣潮》剧情动画 | 仙尘\n“今月曾经照古人。”",
        video_bvid="BV1kp8z6REgg",
    )
    c = parse_dynamic(raw)
    # 跳过话题标签行，取第一句正文作为展示标题
    assert c.title == "《鸣潮》剧情动画 | 仙尘"
    assert c.extra["video_bvid"] == "BV1kp8z6REgg"


def test_parse_dynamic_image_only_gets_fallback_title():
    raw = dict(DYNAMIC_RAW, text="", pics=["https://i0.hdslb.com/bfs/a.jpg"])
    c = parse_dynamic(raw)
    assert c.text is None
    assert c.title == "图文动态"
    assert c.media == ["https://i0.hdslb.com/bfs/a.jpg"]


def test_parse_video_minimal_fields():
    c = parse_video({"bvid": "BV1abc", "owner_mid": 1, "owner_name": "x", "pubdate": 0})
    assert c.external_id == "BV1abc"
    assert c.published_at is None  # pubdate=0 视为缺失
    assert c.metrics["view"] == 0
    assert c.media == []
