"""AI 内容复核与首页精选单测。"""

from copy import deepcopy
from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import AiUsage, ContentReview, RawContent
from app.services import curation
from app.services.classify import seed_defaults
from app.services.ingest import ingest_items
from app.services.settings import set_setting
from tests.test_parsers_bilibili import VIDEO_RAW
from tests.test_parsers_others import KURO_ITEM


async def test_ai_review_is_structured_and_recorded(session, monkeypatch):
    await set_setting(
        session,
        "ai",
        {"base_url": "https://api.example.com/v1", "model": "review-model", "api_key": "x"},
    )
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    raw = await session.scalar(select(RawContent))

    async def fake_chat(config, system, user, *, json_mode=False):
        assert "quality_score" in system
        return (
            '{"reviews":[{"id":%d,"relevant":true,"promotional":false,'
            '"quality_score":88,"category_hint":"review","duplicate_of":null,'
            '"reason":"主题明确且有实质内容"}]}' % raw.id,
            {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100},
        )

    monkeypatch.setattr(curation, "chat", fake_chat)
    stats = await curation.review_contents(session, [raw.id])

    assert stats.reviewed == 1
    review = await session.get(ContentReview, raw.id)
    assert review.relevant is True
    assert review.promotional is False
    assert review.quality_score == 88
    assert review.category_hint == "review"
    assert review.model == "review-model"
    usage = await session.scalar(select(AiUsage).order_by(AiUsage.id.desc()))
    assert usage.task == "review_content"
    assert usage.total_tokens == 100


async def test_highlights_mix_official_and_hot_community(client, session):
    await seed_defaults(session)
    official = deepcopy(KURO_ITEM)
    official["entry"] = deepcopy(KURO_ITEM["entry"])
    official["entry"]["id"] = "hero-official"
    official["entry"]["tabTitle"] = {"en": "New Resonator Preview"}

    promotion = deepcopy(KURO_ITEM)
    promotion["entry"] = deepcopy(KURO_ITEM["entry"])
    promotion["entry"]["id"] = "hero-promo"
    promotion["entry"]["tabTitle"] = {"en": "关注并转发抽奖"}

    community = dict(
        VIDEO_RAW,
        bvid="BVheroCommunity",
        title="鸣潮新角色机制深度解析",
        view_count=86_000,
        owner_mid=9001,
        owner_name="社区作者",
    )
    noise = dict(
        VIDEO_RAW,
        bvid="BVheroNoise",
        title="鸣潮无关标题碰瓷",
        view_count=200_000,
        owner_mid=9002,
        owner_name="另一作者",
    )
    await ingest_items(session, "kuro_notices", [official, promotion])
    await ingest_items(session, "bilibili_search", [community, noise])

    rows = list(await session.scalars(select(RawContent).order_by(RawContent.id)))
    by_external = {row.external_id: row for row in rows}
    for row in rows:
        row.published_at = utcnow() - timedelta(hours=row.id)
    session.add_all(
        [
            ContentReview(
                raw_content_id=by_external["hero-official"].id,
                relevant=True,
                promotional=False,
                quality_score=94,
                model="test",
            ),
            ContentReview(
                raw_content_id=by_external["BVheroCommunity"].id,
                relevant=True,
                promotional=False,
                quality_score=85,
                model="test",
            ),
            ContentReview(
                raw_content_id=by_external["BVheroNoise"].id,
                relevant=False,
                promotional=False,
                quality_score=99,
                model="test",
            ),
        ]
    )
    await session.commit()

    response = await client.get("/api/highlights", params={"limit": 5})
    assert response.status_code == 200
    body = response.json()
    ids = {item["external_id"] for item in body["items"]}
    assert "hero-official" in ids
    assert "BVheroCommunity" in ids
    assert "hero-promo" not in ids
    assert "BVheroNoise" not in ids
    assert {item["identity"] for item in body["items"]} >= {"official", "unknown"}
    assert body["items"][0]["identity"] == "official"


def test_official_similarity_candidates_cover_all_cross_platforms():
    from app.services.similarity import MatchContent, _candidate_pairs

    now = utcnow()
    rows = [
        MatchContent(1, "kuro_cn", now, "角色预告", "", "official_announce"),
        MatchContent(2, "bilibili", now + timedelta(minutes=2), "角色预告视频", "", None),
        MatchContent(3, "kuro_cn", now + timedelta(minutes=3), "同平台公告", "", None),
    ]
    pairs = _candidate_pairs(rows, {1})
    assert [pair.key for pair in pairs] == [(1, 2)]
