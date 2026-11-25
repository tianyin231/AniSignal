"""兑换码引擎 v2 单测：中文/点号码校验、前置过滤、分游戏前瞻推算、多源交叉确认、官方截止回填与 14 天保鲜期。"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.clock import utcnow
from app.models import BotDelivery, BotTarget, ContentSchedule, ContentVersion, RawContent, RedeemCode, Source
from app.services import extract as extract_module
from app.services import redeem_codes as rc_module
from app.services.extract import extract_entities
from app.services.redeem_codes import (
    active_livestream_communities,
    backfill_expiry_from_official_deadline,
    extract_community_codes,
    infer_livestream_expiry,
    is_valid_redeem_code,
    looks_like_code_post,
    sweep_community_codes,
)


def _fake_config():
    return {"base_url": "http://x", "api_key": "k", "model": "test-model"}


def test_code_validation_and_community_prefilter():
    # 支持英数、带点国际服码、中文口令码
    assert is_valid_redeem_code("ENDFIELDSTEAM", "Redemption Code: ENDFIELDSTEAM")
    assert is_valid_redeem_code("INFINITYNIKKI3.0THISISART", "国际服：INFINITYNIKKI3.0THISISART")
    assert is_valid_redeem_code("为暖暖攒下生日祝福", "①生日兑换码/国服：为暖暖攒下生日祝福")
    assert is_valid_redeem_code("无限暖暖3.0马力全开", "②100钻国服：无限暖暖3.0马力全开")

    # 拒绝 BV 号、QQ 群号/纯数字、网址、带标点句子、原文不存在的幻觉码
    assert not is_valid_redeem_code("BV1Mcgp6ME1x", "BV1Mcgp6ME1x")
    assert not is_valid_redeem_code("784188099", "交流群：784188099")
    assert not is_valid_redeem_code("https://a.com", "https://a.com")
    assert not is_valid_redeem_code("请尽快兑换，过期作废", "请尽快兑换，过期作废")
    assert not is_valid_redeem_code("FAKECODE999", "正文里没有这个码")

    # 社区前置过滤：放行真有码的 UP 主帖，拦截空简介/置顶引导/非游戏 API 码
    assert looks_like_code_post("鸣潮3.8前瞻汇总", "兑换码：TRAINOFSOULS BLOOMINGFLOWERS SCHOOLLIFE")
    assert looks_like_code_post("无限暖暖最新兑换码", "①生日兑换码/国服🎁：为暖暖攒下生日祝福 过期时间：2026年12月31日")
    assert not looks_like_code_post("无限暖暖7个兑换码总结", "-")
    assert not looks_like_code_post("鸣潮前瞻干货", "喜欢请三连！兑换码请至置顶评论区复制！")
    assert not looks_like_code_post("鸣潮前瞻汇总", "探索交流群：784188099 欢迎大家加入交流")
    assert not looks_like_code_post("DeepSeek API额度兑换码教程", "输入兑换码 DEEPSEEKFREE 获取额度")


async def test_per_game_livestream_expiry_and_broadcast_anchor(session):
    official = Source(platform="bilibili", external_id="off-1", name="官方", identity="official")
    session.add(official)
    await session.flush()
    raw = RawContent(
        platform="bilibili",
        external_id="off-bcast-1",
        content_type="dynamic",
        community="genshin",
        source_id=official.id,
        published_at=datetime(2026, 11, 20, 10, 0),
        collected_at=datetime(2026, 11, 20, 10, 5),
    )
    session.add(raw)
    await session.flush()

    # 原神周五 20:00（北京时间 = 12:00 UTC）开前瞻直播
    session.add(
        ContentSchedule(
            raw_content_id=raw.id,
            community="genshin",
            title="原神新版本前瞻特别节目",
            kind="broadcast",
            start_at=datetime(2026, 11, 20, 12, 0),
            end_at=None,
            all_day=False,
            delayed=False,
        )
    )
    await session.commit()

    # UP 主次日早上 09:00（北京时间 = 01:00 UTC）才发汇总，应锚定周五直播日 -> 周六 12:00 北京（04:00 UTC）
    exp_gs, est_gs = await infer_livestream_expiry(session, "genshin", datetime(2026, 11, 21, 1, 0))
    assert est_gs is True
    assert exp_gs == datetime(2026, 11, 21, 4, 0)

    # 崩铁次日 23:59 北京（15:59 UTC）
    exp_hsr, est_hsr = await infer_livestream_expiry(session, "hsr", datetime(2026, 11, 20, 11, 30))
    assert est_hsr is True
    assert exp_hsr == datetime(2026, 11, 21, 15, 59)

    # 鸣潮 & 绝区零第 2 日 23:59 北京（15:59 UTC）
    exp_wuwa, est_wuwa = await infer_livestream_expiry(session, "wuwa", datetime(2026, 11, 6, 12, 44))
    assert est_wuwa is True
    assert exp_wuwa == datetime(2026, 11, 8, 15, 59)

    # 若同期官方动态已有兑换码截止日程，则直接采用官方精确时间（expiry_estimated=False）
    session.add(
        ContentSchedule(
            raw_content_id=raw.id,
            community="zzz",
            title="3.4特别节目兑换码失效",
            kind="deadline",
            start_at=datetime(2026, 11, 22, 15, 59, 59),
            end_at=None,
            all_day=False,
            delayed=False,
        )
    )
    await session.commit()
    exp_zzz, est_zzz = await infer_livestream_expiry(session, "zzz", datetime(2026, 11, 20, 12, 0))
    assert est_zzz is False
    assert exp_zzz == datetime(2026, 11, 22, 15, 59, 59)


async def test_community_code_cross_confirmation_and_bot_push_gate(client, session, monkeypatch):
    now = utcnow()
    src_a = Source(platform="bilibili", external_id="up-a", name="博主A", identity="unknown")
    src_b = Source(platform="bilibili", external_id="up-b", name="博主B", identity="unknown")
    session.add_all([src_a, src_b])
    session.add(
        BotTarget(
            name="测试群",
            umo="qq:GroupMessage:codes",
            enabled=True,
            config={"updates_enabled": True, "community": "wuwa", "delivery_mode": "astrbot"},
        )
    )
    await session.flush()

    async def _add_post(ext_id: str, src_id: int, title: str, text: str) -> int:
        raw = RawContent(
            platform="bilibili",
            external_id=ext_id,
            content_type="video",
            community="wuwa",
            source_id=src_id,
            url=f"https://www.bilibili.com/video/{ext_id}",
            published_at=now - timedelta(hours=1),
            collected_at=now,
        )
        session.add(raw)
        await session.flush()
        ver = ContentVersion(raw_content_id=raw.id, version=1, title=title, text=text, parsed_at=now)
        session.add(ver)
        await session.flush()
        raw.latest_version_id = ver.id
        return raw.id

    p1 = await _add_post("BV1aaa111111", src_a.id, "鸣潮前瞻汇总", "前瞻兑换码：TRAINOFSOULS 速领！")
    p1_dup = await _add_post("BV1aaa222222", src_a.id, "鸣潮前瞻复盘", "再次提醒兑换码：TRAINOFSOULS")
    future_iso = (now + timedelta(days=2)).replace(microsecond=0).isoformat() + "Z"
    p2 = await _add_post(
        "BV1bbb111111",
        src_b.id,
        "鸣潮前瞻300星声兑换码",
        f"国服兑换码：TRAINOFSOULS（100星声），截止 {future_iso}",
    )
    await session.commit()

    monkeypatch.setattr(rc_module, "get_ai_config", AsyncMock(return_value=_fake_config()))
    monkeypatch.setattr(rc_module, "record_usage", AsyncMock(return_value=None))

    # 博主 A 首次发布（无明确有效期 -> 自动按鸣潮规则推算；单来源不触发 Bot 推送）
    monkeypatch.setattr(
        rc_module,
        "chat",
        AsyncMock(
            return_value=(
                '{"codes":[{"code":"TRAINOFSOULS","expires_at":null,"is_livestream":true,"evidence":"前瞻兑换码：TRAINOFSOULS"}]}',
                None,
            )
        ),
    )
    res1 = await extract_community_codes(session, p1)
    assert res1 == {"status": "ok", "codes": 1, "confirmed": 0}
    row = (await session.execute(select(RedeemCode).where(RedeemCode.code == "TRAINOFSOULS"))).scalar_one()
    assert row.source_kind == "community"
    assert row.source_count == 1
    assert row.expiry_estimated is True
    assert row.expires_at is not None
    assert (await session.execute(select(BotDelivery))).scalars().all() == []

    # 同一博主 A 复读不增加 source_count，也不触发推送
    res1_dup = await extract_community_codes(session, p1_dup)
    assert res1_dup["codes"] == 0
    await session.refresh(row)
    assert row.source_count == 1
    assert (await session.execute(select(BotDelivery))).scalars().all() == []

    # 独立博主 B 交叉确认并给出精确有效期 + 区服 + 奖励 -> 自动回填并触发一次 Bot 推送
    monkeypatch.setattr(
        rc_module,
        "chat",
        AsyncMock(
            return_value=(
                f'{{"codes":[{{"code":"trainofsouls","expires_at":"{future_iso}","is_livestream":true,"server":"cn","reward":"100星声","evidence":"国服兑换码：TRAINOFSOULS"}}]}}',
                None,
            )
        ),
    )
    res2 = await extract_community_codes(session, p2)
    assert res2 == {"status": "ok", "codes": 0, "confirmed": 1}
    await session.refresh(row)
    assert row.source_count == 2
    assert row.expiry_estimated is False
    assert row.server == "cn"
    assert row.reward == "100星声"
    deliveries = (await session.execute(select(BotDelivery))).scalars().all()
    assert len(deliveries) == 1
    assert "2 位博主确认" in deliveries[0].body
    assert "100星声" in deliveries[0].body

    # 公开 /api/codes 展示完整元数据
    api_resp = await client.get("/api/codes", params={"community": "wuwa"})
    assert api_resp.status_code == 200
    items = api_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["code"] == "TRAINOFSOULS"
    assert items[0]["source_kind"] == "community"
    assert items[0]["source_count"] == 2
    assert items[0]["server"] == "cn"
    assert items[0]["reward"] == "100星声"
    assert items[0]["expiry_estimated"] is False


async def test_unknown_expiry_14d_ttl_hides_zombie_codes_and_official_deadline_backfills(client, session):
    now = utcnow()
    src = Source(platform="youtube", external_id="yt-off", name="Official", identity="official")
    session.add(src)
    await session.flush()

    old_raw = RawContent(
        platform="youtube",
        external_id="old-video",
        content_type="video",
        community="genshin",
        source_id=src.id,
        published_at=now - timedelta(days=25),
        collected_at=now - timedelta(days=25),
    )
    recent_raw = RawContent(
        platform="bilibili",
        external_id="recent-video",
        content_type="video",
        community="wuwa",
        source_id=src.id,
        published_at=now - timedelta(days=2),
        collected_at=now - timedelta(days=2),
    )
    session.add_all([old_raw, recent_raw])
    await session.flush()

    session.add_all([
        # 25 天前且无有效期 -> 超过 14 天保鲜期，应自动隐藏
        RedeemCode(
            raw_content_id=old_raw.id,
            community="genshin",
            code="ZOMBIECODE1",
            status="active",
            expires_at=None,
            created_at=now - timedelta(days=25),
        ),
        # 25 天前但有明确未来有效期 -> 仍应展示
        RedeemCode(
            raw_content_id=old_raw.id,
            community="genshin",
            code="LONGTERMCODE",
            status="active",
            expires_at=now + timedelta(days=30),
            created_at=now - timedelta(days=25),
        ),
        # 2 天前且无有效期 -> 仍在保鲜期内，应展示且可被官方截止日程回填
        RedeemCode(
            raw_content_id=recent_raw.id,
            community="wuwa",
            code="RECENTCODE",
            status="active",
            expires_at=None,
            source_kind="community",
            source_count=1,
            created_at=now - timedelta(days=2),
        ),
    ])
    await session.commit()

    resp = await client.get("/api/codes", params={"community": "all"})
    assert resp.status_code == 200
    returned_codes = {item["code"] for item in resp.json()["items"]}
    assert "ZOMBIECODE1" not in returned_codes
    assert {"LONGTERMCODE", "RECENTCODE"} <= returned_codes

    # 官方前瞻动态公布了统一兑换码截止时间 -> 回填 RECENTCODE
    exact_deadline = now + timedelta(days=1)
    updated = await backfill_expiry_from_official_deadline(session, "wuwa", exact_deadline, now - timedelta(days=1))
    await session.commit()
    assert updated == 1
    refreshed = await session.scalar(select(RedeemCode).where(RedeemCode.code == "RECENTCODE"))
    assert refreshed.expires_at == exact_deadline
    assert refreshed.expiry_estimated is False


async def test_sweep_community_codes_skips_ai_and_empty_bodies(session, monkeypatch):
    now = utcnow()
    src = Source(platform="bilibili", external_id="up-sweep", name="UP主", identity="unknown")
    session.add(src)
    await session.flush()

    async def _seed(ext_id: str, comm: str, title: str, text: str) -> int:
        raw = RawContent(
            platform="bilibili",
            external_id=ext_id,
            content_type="video",
            community=comm,
            source_id=src.id,
            published_at=now - timedelta(hours=2),
            collected_at=now,
        )
        session.add(raw)
        await session.flush()
        ver = ContentVersion(raw_content_id=raw.id, version=1, title=title, text=text, parsed_at=now)
        session.add(ver)
        await session.flush()
        raw.latest_version_id = ver.id
        return raw.id

    ai_id = await _seed("BV1ai0000001", "ai", "DeepSeek免费兑换码教程", "输入兑换码 DEEPSEEK2026")
    empty_id = await _seed("BV1em0000002", "infinitynikki", "无限暖暖7个兑换码汇总", "-")
    good_id = await _seed(
        "BV1gd0000003",
        "infinitynikki",
        "无限暖暖3.0最新兑换码",
        "①生日兑换码/国服🎁：为暖暖攒下生日祝福 过期时间：2026年12月31日",
    )
    await session.commit()

    fake_chat = AsyncMock(
        return_value=(
            '{"codes":[{"code":"为暖暖攒下生日祝福","expires_at":"2026-12-31T23:59:00+08:00","is_livestream":false,"server":"cn","reward":"生日福利","evidence":"生日兑换码/国服🎁：为暖暖攒下生日祝福"}]}',
            None,
        )
    )
    monkeypatch.setattr(rc_module, "chat", fake_chat)
    monkeypatch.setattr(rc_module, "record_usage", AsyncMock(return_value=None))
    monkeypatch.setattr(rc_module, "get_ai_config", AsyncMock(return_value=_fake_config()))

    stats = await sweep_community_codes(session, notify=False, require_livestream_window=False)
    assert stats["codes"] == 1
    assert stats["skipped"] == 1  # empty_id 被前置过滤跳过，ai_id 被 domain != game 排除
    assert fake_chat.await_count == 1  # 只对真有码的 good_id 调了一次 LLM

    saved = await session.scalar(select(RedeemCode).where(RedeemCode.community == "infinitynikki"))
    assert saved is not None
    assert saved.code == "为暖暖攒下生日祝福"
    assert saved.server == "cn"
    assert saved.reward == "生日福利"
    assert (await session.get(RawContent, empty_id)).extracted_at is not None
    assert (await session.get(RawContent, ai_id)).extracted_at is None
    assert (await session.get(RawContent, good_id)).extracted_at is not None


async def test_sweep_community_codes_only_runs_inside_official_livestream_window(session, monkeypatch):
    now = utcnow()
    official_src = Source(platform="bilibili", external_id="off-live", name="官方", identity="official")
    up_src = Source(platform="bilibili", external_id="up-live", name="UP主", identity="unknown")
    session.add_all([official_src, up_src])
    await session.flush()

    async def _seed_up(ext_id: str, comm: str, code: str) -> int:
        raw = RawContent(
            platform="bilibili",
            external_id=ext_id,
            content_type="video",
            community=comm,
            source_id=up_src.id,
            published_at=now - timedelta(hours=1),
            collected_at=now,
        )
        session.add(raw)
        await session.flush()
        ver = ContentVersion(
            raw_content_id=raw.id,
            version=1,
            title=f"{comm}前瞻兑换码",
            text=f"前瞻兑换码：{code} 速领！",
            parsed_at=now,
        )
        session.add(ver)
        await session.flush()
        raw.latest_version_id = ver.id
        return raw.id

    wuwa_post = await _seed_up("BV1wuwa00001", "wuwa", "WUWALIVE99")
    hsr_post = await _seed_up("BV1hsr000002", "hsr", "HSRLIVE888")

    # 1. 没有任何官方前瞻/直播窗口（只有 5 天后的远期直播）-> 社区扫库直接休眠，零 LLM 调用
    off_raw = RawContent(
        platform="bilibili",
        external_id="off-notice-1",
        content_type="dynamic",
        community="hsr",
        source_id=official_src.id,
        published_at=now - timedelta(hours=2),
        collected_at=now,
    )
    session.add(off_raw)
    await session.flush()
    session.add(
        ContentSchedule(
            raw_content_id=off_raw.id,
            community="hsr",
            title="崩铁远期前瞻特别节目",
            kind="broadcast",
            start_at=now + timedelta(days=5),
        )
    )
    await session.commit()

    fake_chat = AsyncMock(
        return_value=(
            '{"codes":[{"code":"WUWALIVE99","expires_at":null,"is_livestream":true,"evidence":"前瞻兑换码：WUWALIVE99"}]}',
            None,
        )
    )
    monkeypatch.setattr(rc_module, "chat", fake_chat)
    monkeypatch.setattr(rc_module, "record_usage", AsyncMock(return_value=None))
    monkeypatch.setattr(rc_module, "get_ai_config", AsyncMock(return_value=_fake_config()))

    assert await active_livestream_communities(session, now) == set()
    idle = await sweep_community_codes(session, notify=False)
    assert idle["candidates"] == 0
    assert idle["active_communities"] == []
    assert fake_chat.await_count == 0
    assert (await session.get(RawContent, wuwa_post)).extracted_at is None
    assert (await session.get(RawContent, hsr_post)).extracted_at is None

    # 2. 鸣潮进入官方前瞻直播窗口（2 小时前开播）-> 仅唤醒 wuwa 社区抓取，hsr 依然保持休眠
    off_wuwa = RawContent(
        platform="bilibili",
        external_id="off-notice-wuwa",
        content_type="dynamic",
        community="wuwa",
        source_id=official_src.id,
        published_at=now - timedelta(days=1),
        collected_at=now - timedelta(days=1),
    )
    session.add(off_wuwa)
    await session.flush()
    session.add(
        ContentSchedule(
            raw_content_id=off_wuwa.id,
            community="wuwa",
            title="鸣潮前瞻特别节目",
            kind="broadcast",
            start_at=now - timedelta(hours=2),
        )
    )
    await session.commit()

    assert await active_livestream_communities(session, now) == {"wuwa"}
    active = await sweep_community_codes(session, notify=False)
    assert active["active_communities"] == ["wuwa"]
    assert active["codes"] == 1
    assert fake_chat.await_count == 1
    assert (await session.get(RawContent, wuwa_post)).extracted_at is not None
    assert (await session.get(RawContent, hsr_post)).extracted_at is None

