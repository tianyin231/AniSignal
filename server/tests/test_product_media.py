from types import SimpleNamespace
from datetime import timedelta
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import ContentVersion, Product
from app.collectors.bilibili import BilibiliCollector
from app.services import product_discovery as discovery, curation, product_media
from app.services.product_media import backfill_product_images, merch_message, official_image_candidates
from tests.test_product_ai_tracking import ai, merch, response
from tests.test_product_discovery import _official


def test_official_images_include_body_and_media_but_reject_embedded_or_private_urls():
    version = SimpleNamespace(title="菲比手办", text='<p>菲比手办</p><img src="/goods.jpg" alt="官方主图"><img src="data:image/png;base64,abc"><img src="http://127.0.0.1/private.jpg">',
                              normalized_json={"media": ["https://example.com/goods.jpg", "https://example.com/banner.jpg"]})
    images = official_image_candidates(version, "https://example.com/notice")
    assert [image["url"] for image in images] == ["https://example.com/goods.jpg", "https://example.com/banner.jpg"]
    assert images[0]["caption"] == "官方主图"


async def test_ai_selected_official_picture_is_saved_as_a_url_and_returned_by_api(session, client, ai, monkeypatch):
    raw = await _official(session, title="菲比手办预售", text='<p>菲比手办开启预售</p><img src="https://example.com/figure.jpg"><img src="https://example.com/banner.jpg">')
    monkeypatch.setattr(curation, "_cover_data_url", AsyncMock(return_value=None))
    ai.return_value = (response(merch(image_ids=[1])), None)
    assert (await discovery.discover_merch_candidates(session))["created"] == 1
    product = await session.scalar(select(Product))
    assert product.discovery["images"] == ["https://example.com/figure.jpg"]
    data = (await client.get(f"/api/products/{product.slug}")).json()
    assert data["images"] == ["https://example.com/figure.jpg"]
    assert data["images_source_url"] == raw.url


async def test_unknown_picture_id_does_not_publish_a_fabricated_picture(session, ai):
    await _official(session, title="菲比手办预售")
    ai.return_value = (response(merch(image_ids=[99])), None)
    assert (await discovery.discover_merch_candidates(session))["created"] == 0
    assert not list(await session.scalars(select(Product)))


async def test_ai_uses_official_cdn_thumbnail_and_keeps_the_original_url(monkeypatch):
    original = "https://i0.hdslb.com/bfs/new_dyn/figure.jpg"
    fetch = AsyncMock(return_value="data:image/webp;base64,test")
    monkeypatch.setattr(curation, "_cover_data_url", fetch)
    images = [{"id": 1, "url": original, "caption": "官方主图"}]
    message = await merch_message({"title": "手办预售", "text": ""}, images)
    fetch.assert_awaited_once_with(original + "@960w.webp")
    assert original in message[0]["text"]
    assert images[0]["url"] == original


async def test_all_eighteen_official_pictures_are_available_to_the_model(monkeypatch):
    urls = [f"https://example.com/{i}.jpg" for i in range(20)]
    images = official_image_candidates(SimpleNamespace(title="手办预售", text="", normalized_json={"media": urls}), None)
    assert len(images) == 18
    fetch = AsyncMock(return_value="data:image/webp;base64,test")
    monkeypatch.setattr(curation, "_cover_data_url", fetch)
    message = await merch_message({"title": "手办预售", "text": ""}, images)
    assert fetch.await_count == 18
    assert len([part for part in message if part["type"] == "image_url"]) == 18


async def test_older_announcement_fills_images_without_overwriting_newer_product_details(session, ai, monkeypatch):
    product = Product(slug="figure", name="菲比手办", price_note="新价格", watch_terms=["菲比手办"],
                      discovery={"published_at": utcnow().isoformat(), "images": []})
    session.add(product)
    await session.commit()
    raw = await _official(session, title="菲比手办预售", text='<img src="https://example.com/old.jpg">')
    raw.published_at = utcnow() - timedelta(days=10)
    await session.commit()
    monkeypatch.setattr(curation, "_cover_data_url", AsyncMock(return_value=None))
    ai.return_value = (response(merch(existing_product_id=product.id, image_ids=[1])), None)
    assert (await discovery.discover_merch_candidates(session))["created"] == 0
    await session.refresh(product)
    assert product.price_note == "新价格"
    assert product.discovery["images"] == ["https://example.com/old.jpg"]
    assert product.discovery["images_source_url"] == raw.url


@pytest.fixture
def image_ai(monkeypatch):
    monkeypatch.setattr(product_media, "get_ai_config", AsyncMock(return_value={
        "base_url": "https://example.com", "api_key": "test", "model": "test"}))
    monkeypatch.setattr(curation, "_cover_data_url", AsyncMock(return_value="data:image/webp;base64,test"))
    chat = AsyncMock(return_value=(json.dumps({"image_ids": [1], "confidence": 95}), None))
    monkeypatch.setattr(product_media, "chat", chat)
    return chat


async def test_backfill_enriches_old_forward_without_rewriting_raw_or_product_facts(session, image_ai, monkeypatch):
    raw = await _official(session, title="菲比手办预售")
    raw.platform, raw.content_type = "bilibili", "dynamic"
    raw.raw_json = {"id": raw.external_id, "type": "DYNAMIC_TYPE_FORWARD", "orig_id": "original",
                    "text": "菲比手办预售", "author_mid": 1955897084, "author_name": "鸣潮"}
    original = dict(raw.raw_json)
    product = Product(slug="old-forward", name="菲比手办", price_note="399元", watch_terms=["菲比手办"],
                      discovery={"raw_content_id": raw.id, "images": []})
    session.add(product)
    await session.commit()
    fetch = AsyncMock(return_value={"text": "菲比手办已开启预售", "pics": ["https://example.com/figure.jpg"]})
    monkeypatch.setattr(BilibiliCollector, "_fetch_draw_detail", fetch)
    assert await backfill_product_images(session) == {"checked": 1, "updated": 1}
    fetch.assert_awaited_once_with("original")
    await session.refresh(product)
    await session.refresh(raw)
    assert product.discovery["images"] == ["https://example.com/figure.jpg"]
    assert product.price_note == "399元"
    assert raw.raw_json == original
    latest = await session.scalar(select(ContentVersion).where(ContentVersion.raw_content_id == raw.id)
                                  .order_by(ContentVersion.version.desc()).limit(1))
    assert latest.version == 2
    assert latest.normalized_json["media"] == product.discovery["images"]
    assert await backfill_product_images(session) == {"checked": 0, "updated": 0}
    assert image_ai.await_count == 1


async def test_backfill_rejects_unknown_image_and_waits_before_retrying(session, image_ai):
    raw = await _official(session, title="菲比手办预售", text='<img src="https://example.com/figure.jpg">')
    product = Product(slug="invalid-image", name="菲比手办", watch_terms=["菲比手办"],
                      discovery={"raw_content_id": raw.id, "images": []})
    session.add(product)
    await session.commit()
    image_ai.return_value = (json.dumps({"image_ids": [99], "confidence": 95}), None)
    now = utcnow()
    assert await backfill_product_images(session, now=now) == {"checked": 1, "updated": 0}
    await session.refresh(product)
    assert product.discovery["images"] == []
    assert await backfill_product_images(session, now=now + timedelta(minutes=1)) == {"checked": 0, "updated": 0}
    assert image_ai.await_count == 1
