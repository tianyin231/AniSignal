"""已认证周边号的身份、分区过滤及上线种子。"""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.communities import official_merch_bilibili_sources
from app.models import CollectTarget
from app.parsers.bilibili import parse_dynamic, parse_video
from app.services.collect import filter_relevant
from tests.test_parsers_bilibili import DYNAMIC_RAW, VIDEO_RAW


@pytest.mark.parametrize("mid,name", [
    ("3493082584582776", "库洛游戏周边"),
    ("508103429", "帕姆的收藏夹"),
    ("3546864272017883", "朝陇山播报站"),
])
def test_verified_merch_authors_are_official_in_both_feeds(mid, name):
    assert parse_dynamic(dict(DYNAMIC_RAW, author_mid=int(mid), author_name=name)).author.identity == "official"
    assert parse_video(dict(VIDEO_RAW, owner_mid=int(mid), owner_name=name)).author.identity == "official"


def test_same_name_does_not_make_an_unverified_account_official():
    assert parse_dynamic(dict(DYNAMIC_RAW, author_mid=370717213, author_name="库洛游戏周边")).author.identity == "unknown"
    assert parse_video(dict(VIDEO_RAW, owner_mid=491478489, owner_name="朝陇山")).author.identity == "unknown"


@pytest.mark.parametrize("mid,community,body", [
    ("3493082584582776", "wuwa", "新品预售\n鸣潮茶歇时光手办"),
    ("508103429", "hsr", "新品预售\n#崩坏：星穹铁道# 徽章上新"),
    ("3546864272017883", "arknights", "新品预售\n#明日方舟# 角色立牌上新"),
])
def test_core_merch_target_filters_by_the_complete_dynamic_body(mid, community, body):
    target = CollectTarget(platform="bilibili", target_type="account_dynamics", target_value=mid,
                           community=community, tier="core", co_terms=[], min_views=0)
    items = [{"text": body}, {"text": "#战双帕弥什# 手办预售"}, {"text": "其他商品预售"},
             {"text": "#鸣潮# × #崩坏星穹铁道# 联名商品"}, {"text": "#明日方舟：终末地# 角色立牌"}]
    kept, filtered = filter_relevant(items, target)
    assert kept == [items[0]]
    assert filtered == 4


def test_merch_account_cannot_be_bound_to_another_game():
    target = CollectTarget(platform="bilibili", target_type="account_dynamics", target_value="3493082584582776",
                           community="hsr", tier="core", min_views=0)
    assert filter_relevant([{"text": "#崩坏星穹铁道# 商品预售"}], target) == ([], 1)


def test_pure_lottery_and_fan_art_are_not_merchandise_announcements():
    target = CollectTarget(platform="bilibili", target_type="account_dynamics", target_value="508103429",
                           community="hsr", tier="core", min_views=0)
    items = [{"text": "#崩坏星穹铁道# 同人绘画展示，转发抽奖送出随机角色立牌。"},
             {"text": "#崩坏星穹铁道# 新品手办开启预售，售价399元。转发抽奖送出该手办。"},
             {"text": "#崩坏星穹铁道# 手办发货时间延后，预计下月陆续发出。"}]
    assert filter_relevant(items, target) == (items[1:], 1)


def test_original_game_official_target_keeps_its_existing_behavior():
    target = CollectTarget(platform="bilibili", target_type="account_dynamics", target_value="1955897084",
                           community="wuwa", tier="core", min_views=0)
    assert filter_relevant([{"text": "新角色预告"}], target)[1] == 0


async def test_merch_seed_matches_registry_and_preserves_existing_settings(engine):
    path = Path("alembic/versions/0049_official_merch_sources.py")
    spec = importlib.util.spec_from_file_location("seed_merch_sources", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def upgrade(conn):
        with patch.object(migration.op, "get_bind", return_value=conn):
            migration.upgrade()

    async with engine.begin() as conn:
        await conn.run_sync(upgrade)
        rows = (await conn.execute(select(CollectTarget.__table__))).mappings().all()
        assert {r["target_value"]: (r["community"], r["display_name"].split("·")[0]) for r in rows} == official_merch_bilibili_sources()
        assert all(r["enabled"] and r["interval_minutes"] == 30 and r["tier"] == "core"
                   and r["min_views"] == 0 and r["next_run_at"] for r in rows)
        await conn.execute(CollectTarget.__table__.update().where(CollectTarget.target_value == "3493082584582776")
                           .values(interval_minutes=75, enabled=False))
        await conn.run_sync(upgrade)
        rows = (await conn.execute(select(CollectTarget.__table__))).mappings().all()
        assert len(rows) == 3
        kuro = next(r for r in rows if r["target_value"] == "3493082584582776")
        assert kuro["interval_minutes"] == 75 and not kuro["enabled"]
