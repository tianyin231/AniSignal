"""周边发现、接受跟踪、安静归档，以及公开页的三件事。"""

from datetime import timedelta
import json
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.clock import utcnow
from app.models import CollectTarget, ContentVersion, MerchScan, Product, ProductCandidate, ProductSignal, ProductSignalEvidence, RawContent, Source
from app.services import product_discovery
from app.services.curation import _load_tracked_products
from app.services.product_discovery import (
    archive_quiet_products,
    build_search_keyword,
    build_watch_terms,
    discover_merch_candidates,
    public_brief,
)


def test_watch_terms_keep_the_product_category_and_use_one_search():
    assert build_watch_terms("爱弥斯联名机械键盘") == ["爱弥斯联名机械键盘", "爱弥斯键盘"]
    assert build_search_keyword("爱弥斯联名机械键盘") == "爱弥斯 键盘"
    assert build_watch_terms("爱弥斯") == []


def test_public_brief_does_not_call_unspecified_unboxing_a_purchase():
    product = Product(
        name="爱弥斯键盘", presale_at="2026-06-20", ship_note="8 月中旬起分批发货",
        price_note="¥749 起", monitor_status="archived",
    )
    brief = public_brief(product, {
        "consumer": {"verified_count": 0},
        "official": {"verified_count": 0, "state_label": "暂无官方履约公告"},
        "quality": {"verified_count": 3},
        "observation_count": 8,
        "sample_count": 0,
    }, [
        {"signal_type": "consumer_received", "review_status": "verified", "occurred_at": utcnow() - timedelta(days=40)},
        {"signal_type": "quality_issue", "review_status": "verified", "occurred_at": utcnow() - timedelta(days=8)},
    ])
    assert brief["archived"] is True
    assert brief["unboxing_accounts"] == 8
    assert brief["unboxing_note"] == ""
    assert brief["quality_accounts"] == 3
    assert brief["official_registered"] is True
    assert "分批发货" in brief["official_text"]
    assert "¥749 起" in brief["official_text"]


async def _official(session, *, title, text="", identity="official", community="wuwa", external="n1"):
    source = Source(platform="kuro_cn", external_id=external, name="官方", identity=identity)
    session.add(source)
    await session.flush()
    raw = RawContent(
        platform="kuro_cn", content_type="article", external_id=external, community=community,
        source_id=source.id, url=f"https://example.com/{external}", collected_at=utcnow(),
    )
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, version=1, title=title, text=text))
    await session.commit()
    return raw


async def test_official_notice_automatically_opens_one_search(session, client, admin_headers, monkeypatch):
    monkeypatch.setattr(product_discovery, "get_ai_config", AsyncMock(return_value={
        "base_url": "https://example.com", "api_key": "test", "model": "test"}))
    monkeypatch.setattr(product_discovery, "chat", AsyncMock(side_effect=[('{"products":[]}', None), (json.dumps({"products": [{
        "name": "菲比手办", "product_type": "手办", "confidence": 96, "quote": "菲比手办开启预售",
        "price_note": "¥399", "presale_note": "预售将于下月开启", "ship_note": "预计八月发货",
        "facts": [{"signal_type": "presale_open", "confidence": 96, "quote": "菲比手办开启预售"}],
    }]}, ensure_ascii=False), None)]))
    await _official(
        session,
        title="菲比手办开启预售",
        text="定价 ¥399。预售将于下月开启。预计八月发货。",
    )
    await _official(session, title="爱弥斯新衣装展示", external="n2")
    found = await discover_merch_candidates(session)
    assert found == {"scanned": 2, "created": 1}
    again = await discover_merch_candidates(session)
    assert again["scanned"] == 0

    listed = (await client.get("/api/admin/product-candidates?status=accepted", headers=admin_headers)).json()
    assert listed["items"][0]["name"] == "菲比手办"
    assert listed["items"][0]["search_keyword"] == "菲比 手办"
    assert "¥399" in (listed["items"][0]["price_note"] or "")

    body = (await client.get("/api/products")).json()["items"][0]
    assert body["monitor_status"] == "watching"
    assert body["watch_terms"] == ["菲比手办"]
    assert body["monitor"]["keyword"] == "菲比 手办"
    assert body["monitor"]["enabled"] is True
    assert body["price_note"] == "¥399"
    assert body["fulfillment"]["official"]["verified_count"] >= 1
    assert body["brief"]["official_registered"] is False
    target = await session.scalar(select(CollectTarget).where(CollectTarget.target_value == "菲比 手办"))
    assert target is not None and target.tier == "core"

    tracked = await _load_tracked_products(session)
    assert any(item["name"] == "菲比手办" for item in tracked)


async def test_ignore_and_archive_leave_the_page_but_stop_the_search(session, client, admin_headers):
    raw = await _official(session, title="秧秧立牌开启预订", text="预售本周开启。", external="n3")
    candidate = ProductCandidate(community="wuwa", name="秧秧立牌", watch_terms=["秧秧立牌"],
                                 search_keyword="秧秧 立牌", raw_content_id=raw.id)
    session.add(candidate)
    await session.commit()
    ignored = await client.post(f"/api/admin/product-candidates/{candidate.id}/ignore", headers=admin_headers)
    assert ignored.status_code == 200
    assert ignored.json()["status"] == "ignored"
    again = await client.post(f"/api/admin/product-candidates/{candidate.id}/accept", headers=admin_headers)
    assert again.status_code == 409

    product = Product(
        slug="old-badge", name="旧徽章", community="wuwa", watch_terms=["旧徽章"],
        monitor_status="watching", created_at=utcnow() - timedelta(days=40),
    )
    session.add(product)
    await session.flush()
    target = CollectTarget(
        platform="bilibili", target_type="keyword_search", target_value="旧 徽章",
        community="wuwa", tier="core", enabled=True, interval_minutes=30,
    )
    session.add(target)
    await session.flush()
    product.monitor_target_id = target.id
    signal = ProductSignal(product_id=product.id, signal_type="shipping_started", actor_type="official",
                           review_status="verified", summary="旧徽章已发货", occurred_at=utcnow() - timedelta(days=40))
    session.add(signal)
    await session.flush()
    session.add(ProductSignalEvidence(signal_id=signal.id, raw_content_id=raw.id, independent_source_key="official"))
    await session.commit()

    result = await archive_quiet_products(session)
    assert result["archived"] == 1
    await session.refresh(product)
    await session.refresh(target)
    assert product.monitor_status == "archived"
    assert target.enabled is False
    tracked = await _load_tracked_products(session)
    assert any(item["name"] == "旧徽章" for item in tracked)  # 归档仅停专属搜索，匹配后的新反馈仍能审核。
    visible = (await client.get("/api/products/old-badge")).json()
    assert visible["monitor_status"] == "archived"
    assert visible["brief"]["archived"] is True


def test_watch_terms_gain_the_casual_form_from_the_search_keyword():
    terms = build_watch_terms(
        "鸣潮 茶歇时光系列 Q版手办 长离", search_keyword="鸣潮 长离 手办", community="wuwa",
    )
    assert terms[0] == "鸣潮茶歇时光系列Q版手办长离"
    assert "长离手办" in terms
    assert build_watch_terms("爱弥斯联名机械键盘", search_keyword=None, community=None) == [
        "爱弥斯联名机械键盘", "爱弥斯键盘",
    ]


def test_casual_terms_match_the_titles_monitors_actually_fetch():
    from app.services.product_matching import matches_product_text

    terms = build_watch_terms(
        "鸣潮 茶歇时光系列 Q版手办 长离", search_keyword="鸣潮 长离 手办", community="wuwa",
    )
    assert matches_product_text("长离手办开箱！699到底值不值？", "", terms)
    seek = build_watch_terms(
        "鸣潮 × APEX-TOYS 寻迹系列 Q版手办", search_keyword="鸣潮 寻迹 Q版手办", community="wuwa",
    )
    assert matches_product_text("【手办推荐】鸣潮寻迹Q版手办鉴赏！卡提希娅&守岸人双萌登场", "", seek)
    assert not matches_product_text("鸣潮新版本剧情解说", "", seek)


def test_search_keyword_drops_the_game_name_for_every_community():
    terms = build_watch_terms(
        "MOONDROP Sparxie Edition collaboration earphones",
        product_type="耳机", search_keyword="崩坏星穹铁道 Sparxie 耳机", community="hsr",
    )
    assert "Sparxie耳机" in terms
    assert not any("崩坏星穹铁道" in term for term in terms)


async def test_rebuild_terms_merges_the_casual_form_for_existing_products(session, client, admin_headers):
    raw = await _official(session, title="长离 Q版手办 预售即将开启", text="茶歇时光系列。", external="n4")
    product = Product(
        slug="merch-casual", name="鸣潮 茶歇时光系列 Q版手办 长离", community="wuwa",
        watch_terms=["鸣潮茶歇时光系列Q版手办长离"], monitor_status="watching",
    )
    candidate = ProductCandidate(
        community="wuwa", name="鸣潮 茶歇时光系列 Q版手办 长离",
        aliases=["长离 Q版手办"], watch_terms=["鸣潮茶歇时光系列Q版手办长离"],
        search_keyword="鸣潮 长离 手办", details={"aliases": ["长离 Q版手办"], "product_type": "手办"},
        raw_content_id=raw.id, status="accepted",
    )
    session.add(product)
    session.add(candidate)
    await session.flush()
    candidate.product_id = product.id
    target = CollectTarget(
        platform="bilibili", target_type="keyword_search", target_value="鸣潮 长离 手办",
        community="wuwa", tier="core", enabled=True, interval_minutes=30,
    )
    session.add(target)
    await session.flush()
    product.monitor_target_id = target.id
    await session.commit()

    response = await client.post(f"/api/admin/products/{product.id}/rebuild-terms", headers=admin_headers)
    assert response.status_code == 200
    terms = response.json()["watch_terms"]
    assert "鸣潮茶歇时光系列Q版手办长离" in terms
    assert "长离手办" in terms
