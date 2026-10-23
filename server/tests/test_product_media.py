from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import ContentVersion, Product
from app.services import product_discovery as discovery, curation
from app.services.product_media import official_image_candidates
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
