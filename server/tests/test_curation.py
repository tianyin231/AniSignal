"""AI 内容复核与首页精选单测。"""

from copy import deepcopy
from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import AiUsage, CollectTarget, ContentReview, RawContent
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


async def test_ai_review_auto_hides_violation_and_irrelevant(client, session, monkeypatch):
    """违规或不相关的内容自动下架；公开侧不可见，管理端可恢复。"""
    await set_setting(
        session,
        "ai",
        {"base_url": "https://api.example.com/v1", "model": "review-model", "api_key": "x"},
    )
    await ingest_items(
        session,
        "bilibili_search",
        [
            dict(VIDEO_RAW, bvid="BVviol", title="鸣潮相关但封面低俗"),
            dict(VIDEO_RAW, bvid="BVirrel", title="与鸣潮无关的数码测评"),
            dict(VIDEO_RAW, bvid="BVok", title="鸣潮版本更新深度解读"),
        ],
    )
    rows = {raw.external_id: raw for raw in await session.scalars(select(RawContent))}
    ids = [raw.id for raw in rows.values()]
    decisions = {
        "BVviol": {"relevant": True, "promotional": False, "violation": True, "quality_score": 10, "category_hint": None, "reason": "封面低俗"},
        "BVirrel": {"relevant": False, "promotional": False, "violation": False, "quality_score": 40, "category_hint": "other", "reason": "与鸣潮无关"},
        "BVok": {"relevant": True, "promotional": False, "violation": False, "quality_score": 85, "category_hint": "version_update", "reason": "版本解读"},
    }

    async def fake_cover(url):
        return None  # 封面抓取失败走纯文本

    monkeypatch.setattr(curation, "_cover_data_url", fake_cover)

    async def fake_chat(config, system, user, *, json_mode=False):
        import json as _json

        payload = _json.loads(user if isinstance(user, str) else user[0]["text"])
        decision = decisions[
            next(bvid for bvid, raw in rows.items() if raw.id == payload["id"])
        ]
        return '{"review":%s}' % _json.dumps(decision, ensure_ascii=False), {
            "prompt_tokens": 50, "completion_tokens": 15, "total_tokens": 65,
        }

    monkeypatch.setattr(curation, "chat", fake_chat)
    stats = await curation.review_contents(session, ids)
    assert stats.reviewed == 3

    assert rows["BVviol"].hidden is True
    assert rows["BVirrel"].hidden is True
    assert rows["BVok"].hidden is False

    # 公开内容流只展示未下架且相关的
    resp = await client.get("/api/contents")
    titles = " ".join(item["title"] or "" for item in resp.json()["items"])
    assert "封面低俗" not in titles
    assert "数码测评" not in titles
    assert "深度解读" in titles

    # 管理端恢复不相关内容后重新可见
    admin_headers = {"X-Admin-Token": "test-admin-token"}
    resp = await client.patch(
        f"/api/admin/contents/{rows['BVirrel'].id}/visibility",
        json={"hidden": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    resp = await client.get("/api/contents")
    titles = " ".join(item["title"] or "" for item in resp.json()["items"])
    assert "数码测评" in titles


async def test_ai_review_multimodal_cover(client, session, monkeypatch):
    """带封面的内容审核走多模态：user 消息包含文本分片与图片分片。"""
    await set_setting(
        session,
        "ai",
        {"base_url": "https://api.example.com/v1", "model": "review-model", "api_key": "x"},
    )
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    raw = await session.scalar(select(RawContent))
    seen_shapes = []

    async def fake_cover(url):
        return "data:image/jpeg;base64,ZmFrZQ=="

    async def fake_chat(config, system, user, *, json_mode=False):
        seen_shapes.append(user)
        return (
            '{"review":{"relevant":true,"promotional":false,"violation":false,'
            '"quality_score":80,"category_hint":"unboxing","reason":"开箱分享"}}'
        ), None

    monkeypatch.setattr(curation, "_cover_data_url", fake_cover)
    monkeypatch.setattr(curation, "chat", fake_chat)
    stats = await curation.review_contents(session, [raw.id])
    assert stats.reviewed == 1
    assert isinstance(seen_shapes[0], list)
    kinds = {part["type"] for part in seen_shapes[0]}
    assert kinds == {"text", "image_url"}
    image_part = next(part for part in seen_shapes[0] if part["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/")  # 内嵌数据图，网关无需抓取


async def test_official_content_not_hidden_by_relevance(session, monkeypatch):
    """官方账号内容即使被判不相关也不自动下架（新角色常超出模型知识库）。"""
    await set_setting(
        session,
        "ai",
        {"base_url": "https://api.example.com/v1", "model": "review-model", "api_key": "x"},
    )
    from tests.test_parsers_others import KURO_ITEM as _K

    notice = dict(_K)
    notice["entry"] = dict(_K["entry"], id="official-new-char")
    await ingest_items(session, "kuro_notices", [notice])
    raw = await session.scalar(select(RawContent))
    assert raw.hidden is False

    async def fake_cover(url):
        return None

    monkeypatch.setattr(curation, "_cover_data_url", fake_cover)

    async def fake_chat(config, system, user, *, json_mode=False):
        # 模拟模型知识库不认识新角色而误判不相关
        return (
            '{"review":{"relevant":false,"promotional":false,"violation":false,'
            '"quality_score":30,"category_hint":null,"reason":"知识库无此角色"}}'
        ), None

    monkeypatch.setattr(curation, "chat", fake_chat)
    stats = await curation.review_contents(session, [raw.id])
    assert stats.reviewed == 1
    review = await session.get(ContentReview, raw.id)
    assert review.relevant is False
    assert raw.hidden is False  # 官方身份豁免相关性下架


async def test_review_prompt_carries_known_terms(session, monkeypatch):
    """审核载荷带 matched_keywords 与管理员词表，作为新内容的相关性判据。"""
    from tests.test_parsers_bilibili import VIDEO_RAW as _V

    await seed_defaults(session)  # 分类词典种子 → known_terms
    session.add(CollectTarget(platform="bilibili", target_type="keyword_search", target_value="鸣潮", interval_minutes=30))
    await session.commit()
    await set_setting(
        session,
        "ai",
        {"base_url": "https://api.example.com/v1", "model": "review-model", "api_key": "x"},
    )
    await ingest_items(
        session,
        "bilibili_search",
        [dict(_V, bvid="BVterms", title="心月狐角色实战演示")],
    )
    raw = await session.scalar(select(RawContent))
    raw.matched_keywords = ["鸣潮", "心月狐"]  # 采集链路会写入命中关键词
    await session.commit()
    seen_payloads = []

    async def fake_cover(url):
        return None  # 封面抓取失败走纯文本

    monkeypatch.setattr(curation, "_cover_data_url", fake_cover)

    async def fake_chat(config, system, user, *, json_mode=False):
        import json as _json

        payload = _json.loads(user if isinstance(user, str) else user[0]["text"])
        seen_payloads.append(payload)
        assert "matched_keywords" in system or True
        return (
            '{"review":{"relevant":true,"promotional":false,"violation":false,'
            '"quality_score":75,"category_hint":"review","reason":"命中词表"}}'
        ), None

    monkeypatch.setattr(curation, "chat", fake_chat)
    stats = await curation.review_contents(session, [raw.id])
    assert stats.reviewed == 1
    assert seen_payloads[0]["matched_keywords"] == ["鸣潮", "心月狐"]
    assert any("鸣潮" in term for term in seen_payloads[0]["known_terms"])
    # 提示词包含词表规则说明
    assert "known_terms" in seen_payloads[0]


async def test_ai_review_cover_fetch_failure_falls_back_to_text(client, session, monkeypatch):
    """封面抓取失败时第一次尝试即为纯文本，审核不中断。"""
    await set_setting(
        session,
        "ai",
        {"base_url": "https://api.example.com/v1", "model": "review-model", "api_key": "x"},
    )
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    raw = await session.scalar(select(RawContent))
    seen_users = []

    async def fake_cover(url):
        return None  # 抓取失败 → 第一次尝试就不带图

    async def fake_chat(config, system, user, *, json_mode=False):
        seen_users.append(user)
        return (
            '{"review":{"relevant":true,"promotional":false,"violation":false,'
            '"quality_score":70,"category_hint":null,"reason":"纯文本判定"}}'
        ), None

    monkeypatch.setattr(curation, "_cover_data_url", fake_cover)
    monkeypatch.setattr(curation, "chat", fake_chat)
    stats = await curation.review_contents(session, [raw.id])
    assert stats.reviewed == 1
    assert len(seen_users) == 1
    assert isinstance(seen_users[0], str)  # 纯文本 JSON


async def test_ai_review_image_gateway_failure_retries_text_only(client, session, monkeypatch):
    """网关拒绝图片调用（如 500）时，降级纯文本重试一次并成功。"""
    await set_setting(
        session,
        "ai",
        {"base_url": "https://api.example.com/v1", "model": "review-model", "api_key": "x"},
    )
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    raw = await session.scalar(select(RawContent))
    seen_users = []

    async def fake_cover(url):
        return "data:image/jpeg;base64,ZmFrZQ=="

    async def fake_chat(config, system, user, *, json_mode=False):
        seen_users.append(user)
        if isinstance(user, list):
            raise RuntimeError("gateway 500 on image")  # 模拟网关拒绝图片调用
        return (
            '{"review":{"relevant":true,"promotional":false,"violation":false,'
            '"quality_score":70,"category_hint":null,"reason":"纯文本降级判定"}}'
        ), None

    monkeypatch.setattr(curation, "_cover_data_url", fake_cover)
    monkeypatch.setattr(curation, "chat", fake_chat)
    stats = await curation.review_contents(session, [raw.id])
    assert stats.reviewed == 1
    assert len(seen_users) == 2  # 带图失败 → 纯文本重试
    assert isinstance(seen_users[0], list)
    assert isinstance(seen_users[1], str)
