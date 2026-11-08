"""0057：按频道实际内容补齐和停用采集目标，且不覆盖管理员的设置。"""

import importlib.util
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app.models import CollectTarget
from app.services.collect import filter_relevant

XLICE = "UCnRqDZs2TrRxqPAnafCJGaA"
REXLENT = "UCX42emUG96mELubKENIvv-Q"


def _migration():
    spec = importlib.util.spec_from_file_location("retune_targets", Path("alembic/versions/0057_retune_channel_targets.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target(community, platform, kind, value, *, enabled=True, config=None):
    return CollectTarget(community=community, platform=platform, target_type=kind, target_value=value,
                         display_name=f"{value}-{community}", interval_minutes=30, enabled=enabled,
                         co_terms=[], config=config or {}, min_views=0, tier="general")


async def test_retune_adds_xlice_games_and_retires_only_targets_it_disabled(engine, session):
    session.add_all([
        _target("blhx", "youtube", "channel_rss", REXLENT, config={"note": "keep"}),
        _target("wuwa", "youtube", "channel_rss", REXLENT),
        _target("arknights", "youtube", "channel_rss", XLICE, enabled=False),
        _target("wuwa", "youtube", "channel_rss", XLICE),
        _target("hsr", "bilibili", "account_dynamics", "508103429"),
    ])
    await session.commit()
    migration = _migration()

    def run(step):
        def call(conn):
            with patch.object(migration.op, "get_bind", return_value=conn):
                step()
        return call

    async def rows():
        async with engine.connect() as conn:
            result = await conn.execute(select(CollectTarget.__table__))
            return {(r["target_value"], r["community"]): r for r in result.mappings().all()}

    async with engine.begin() as conn:
        await conn.run_sync(run(migration.upgrade))
        await conn.run_sync(run(migration.upgrade))
    after = await rows()
    assert len(after) == 7
    assert not after[(REXLENT, "blhx")]["enabled"]
    assert after[(REXLENT, "blhx")]["config"] == {"note": "keep", "retired_by": "0057"}
    assert not after[("508103429", "hsr")]["enabled"]
    assert after[(REXLENT, "wuwa")]["enabled"] and after[(XLICE, "wuwa")]["enabled"]
    assert after[(XLICE, "arknights")]["config"] == {}  # 管理员本来就停用的，不打标记。
    for community in ("genshin", "endfield"):
        row = after[(XLICE, community)]
        assert row["enabled"] and row["interval_minutes"] == 60 and row["config"] == {"seed": "0057"}

    async with engine.begin() as conn:
        await conn.run_sync(run(migration.downgrade))
    back = await rows()
    assert back[(REXLENT, "blhx")]["enabled"] and back[(REXLENT, "blhx")]["config"] == {"note": "keep"}
    assert back[("508103429", "hsr")]["enabled"]
    assert not back[(XLICE, "arknights")]["enabled"]
    assert not back[(XLICE, "genshin")]["enabled"] and not back[(XLICE, "endfield")]["enabled"]


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
