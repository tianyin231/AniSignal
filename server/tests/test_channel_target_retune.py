"""预设信源校准：Xlice 覆盖原神、终末地与鸣潮，停用的低信噪比源不进默认预设。"""

from sqlalchemy import select

from app.models import CollectTarget
from app.seeds import import_preset_targets, load_preset_packs
from app.services.collect import filter_relevant

XLICE = "UCnRqDZs2TrRxqPAnafCJGaA"
REXLENT = "UCX42emUG96mELubKENIvv-Q"


async def test_presets_include_xlice_games_and_exclude_retired_targets(session):
    packs = load_preset_packs()
    for retired_community in ("blhx", "hsr", "arknights"):
        values = {(t["platform"], t["target_value"]) for t in packs[retired_community]}
        assert ("youtube", REXLENT) not in values
    assert ("bilibili", "508103429") not in {(t["platform"], t["target_value"]) for t in packs["hsr"]}

    await import_preset_targets(session, ["wuwa", "genshin", "endfield"])
    rows = list(await session.scalars(select(CollectTarget).where(CollectTarget.target_value == XLICE)))
    assert {r.community for r in rows} == {"wuwa", "genshin", "endfield"}
    assert all(r.enabled and r.interval_minutes == 60 for r in rows)


def test_new_xlice_targets_split_videos_by_game():
    titles = [
        "MASSIVE VIEWER SUMMONS! Over 4,500+ Pulls For MITYA | Genshin Impact",
        "COMPLETE ARGENT FLOW GUIDE! Best Builds & P0 Showcase | Arknights Endfield",
        "MASSIVE VIEWER SUMMONS! Over 1,500+ Pulls for Suoming! | Wuthering Waves",
        "CookieRun Is About to Be BIGGER Than EVER!",
    ]
    items = [{"title": title, "url": f"https://youtu.be/{i}"} for i, title in enumerate(titles)]
    for community, terms, expected in [
        ("genshin", ["原神", "Genshin Impact", "Genshin"], titles[0]),
        ("endfield", ["终末地", "Arknights Endfield", "Endfield"], titles[1]),
    ]:
        target = CollectTarget(platform="youtube", target_type="channel_rss", target_value=XLICE,
                               community=community, tier="general", co_terms=terms, min_views=0)
        kept, _ = filter_relevant([dict(item) for item in items], target)
        assert [item["title"] for item in kept] == [expected]
