"""跨测试共享的公共测试替身与样例载荷（单一真相源）。

避免测试文件之间互相导入内部 helper，也避免 conftest.py 反向依赖业务单测文件。
"""

from __future__ import annotations

from datetime import timedelta

from app.clock import utcnow


class FakeQueue:
    """进程内队列桩：供 API 与服务单测断言入队记录，不依赖真实 Redis/arq。"""

    def __init__(self) -> None:
        self.enqueued: list[int] = []
        self.notifications: list[int] = []
        self.reviewed: list[int] = []
        self.admin_emails: list[int] = []

    async def enqueue_collect(self, target_id: int) -> str | None:
        self.enqueued.append(target_id)
        return "fake-job-id"

    async def pending_count(self) -> int | None:
        return 3

    async def enqueue_notification(self, delivery_id: int) -> str | None:
        self.notifications.append(delivery_id)
        return "fake-notification-job"

    async def enqueue_morning(self) -> str | None:
        return "fake-morning-job"

    async def enqueue_review(self, content_ids: list[int]) -> str | None:
        self.reviewed.extend(content_ids)
        return "fake-review-job"

    async def enqueue_admin_email(self, delivery_ids: list[int]) -> str | None:
        self.admin_emails.extend(delivery_ids)
        return "fake-admin-email-job"

    async def enqueue_custom_sweep(self) -> str | None:
        return "fake-custom-sweep"


# B 站实测结构样例
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

# 发布时间取「昨天」，避免 30 天窗口类测试随日期漂移
_KURO_START_MS = int((utcnow() - timedelta(days=1)).timestamp() * 1000)

KURO_ITEM = {
    "platform": "kuro_global",
    "entry": {
        "id": "50553",
        "tabTitle": {
            "en": "Version Update Notice",
            "zh-Hans": "版本更新维护公告",
        },
        "tabBanner": {"en": "https://example.com/banner.png"},
        "startTimeMs": _KURO_START_MS,
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
