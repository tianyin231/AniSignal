import pytest
from sqlalchemy import select

from app.communities import COMMUNITIES, crosstalk_names
from app.models import CollectTarget
from app.models.enums import Identity
from app.parsers.bilibili import parse_dynamic
from app.seeds import import_preset_targets
from app.services.collect import filter_relevant, text_communities
from app.services.ingest import ingest_items


@pytest.mark.parametrize("community,title", [
    ("zzz", "绝区零新版本前瞻"),
    ("petitplanet", "Petit Planet Official Trailer"),
    ("hna", "崩坏：因缘精灵测试招募"),
    ("roco", "洛克王国：世界赛季更新"),
    ("promilia", "蓝色星原：旅谣测试公告"),
    ("nte", "Neverness to Everness Version Preview"),
    ("infinitynikki", "无限暖暖新版本公告"),
])
async def test_new_game_official_content_is_visible_in_its_own_channel(session, client, community, title):
    meta = COMMUNITIES[community]
    assert text_communities(title) == {community}
    assert meta.bare_name not in crosstalk_names(community)
    raw = {"id": f"new-{community}", "type": "DYNAMIC_TYPE_WORD", "text": title,
           "author_mid": int(meta.official_bili_mid_default), "author_name": meta.label}
    assert parse_dynamic(raw).author.identity == Identity.OFFICIAL
    await ingest_items(session, "bilibili_dynamics", [raw], community=community)
    await session.commit()
    response = await client.get("/api/contents", params={"community": community, "include_chat": True})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["community"] == community
    assert (await client.get("/api/contents", params={"community": "wuwa"})).json()["total"] == 0


async def test_seed_preserves_existing_settings_and_filters_roco(session):
    manual = CollectTarget(
        platform="bilibili",
        target_type="account_dynamics",
        target_value="1636034895",
        community="zzz",
        enabled=False,
        interval_minutes=75,
        config={"manual": True},
    )
    session.add(manual)
    await session.commit()

    communities = ["zzz", "petitplanet", "hna", "roco", "promilia", "nte"]
    await import_preset_targets(session, communities)
    await import_preset_targets(session, communities)
    rows = list(await session.scalars(select(CollectTarget)))
    assert len(rows) == 12
    await session.refresh(manual)
    assert not manual.enabled and manual.interval_minutes == 75 and manual.config == {"manual": True}
    for row in rows:
        if row.target_type == "keyword_search":
            assert row.min_views == 10000 and row.tier == "general" and row.co_terms
        elif row.id != manual.id:
            assert row.min_views == 0 and row.tier == "core"

    roco = next(row for row in rows if row.community == "roco" and row.target_type == "keyword_search")
    items = [
        {"title": "洛克王国：世界新赛季", "view_count": 10000},
        {"title": "洛克王国页游本周活动", "view_count": 200000},
        {"title": "洛克王国世界新手攻略", "view_count": 9999},
    ]
    kept, filtered = filter_relevant(items, roco)
    assert kept == items[:1] and filtered == 2
