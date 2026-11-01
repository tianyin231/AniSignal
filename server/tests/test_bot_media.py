from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image, ImageChops

from app.clock import utcnow
from app.models import ContentVersion, RawContent
from app.services import bot, bot_briefs, bot_media, notifications


async def dynamic(session, media):
    now = utcnow()
    raw = RawContent(platform="bilibili", content_type="dynamic", external_id="nine-grid", community="wuwa",
                     url="https://t.bilibili.com/123", collected_at=now, published_at=now - timedelta(minutes=1))
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, version=1, title="新角色九宫格", text="全新角色正式登场。",
                               category="announcement", normalized_json={"media": media}))
    await session.commit()
    return raw


def cdn_images(count=9):
    return [f"https://i0.hdslb.com/bfs/new_dyn/{index}.jpg" for index in range(count)]


def grid_tiles(rows=3, width=800, height=800):
    size = (width * 3, height * rows)
    vertical = Image.linear_gradient("L").resize(size)
    horizontal = Image.linear_gradient("L").transpose(Image.Transpose.ROTATE_90).resize(size)
    full = Image.merge("RGB", (horizontal, vertical, ImageChops.add(horizontal, vertical, scale=2)))
    tiles = [full.crop((column * width, row * height, (column + 1) * width, (row + 1) * height))
             for row in range(rows) for column in range(3)]
    return full, tiles


def mock_cdn(monkeypatch, handler):
    bot_media._CACHE.clear()
    original = httpx.AsyncClient
    monkeypatch.setattr(bot_media.httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))


async def test_nine_grid_survives_candidate_brief_and_one_image_limit(session, monkeypatch):
    monkeypatch.setattr(bot_media, "get_settings", lambda: SimpleNamespace(public_base_url="https://anisignal.test"))
    images = cdn_images()
    raw = await dynamic(session, [images[0] + "@200w.webp", *images[1:], "javascript:alert(1)"])
    now = utcnow()
    items = await notifications.load_candidates(session, None, now - timedelta(hours=1), now)
    assert len(items) == 1 and items[0]["media"] == images
    # 邮件继续使用原封面；机器人一张图片的额度容纳完整九宫格。
    assert items[0]["image"] == images[0]
    brief = bot_briefs.single_item_brief(items[0])
    source = brief["items"][0]["sources"][0]
    expected = f"https://anisignal.test/api/contents/{raw.id}/notification-image.jpg?v="
    assert source["image"].startswith(expected)
    assert source["media"] == images
    assert bot.collect_images(brief["items"], 1) == [source["image"]]


@pytest.mark.parametrize("overrides", [{"media": cdn_images(1)}, {"media": cdn_images(2)}, {"content_type": "video"},
    {"platform": "youtube"}, {"id": "custom:1:123"}])
def test_single_cover_and_other_content_keep_original_image(monkeypatch, overrides):
    monkeypatch.setattr(bot_media, "get_settings", lambda: SimpleNamespace(public_base_url="https://anisignal.test"))
    item = {"id": 123, "platform": "bilibili", "content_type": "dynamic", "image": cdn_images()[0], "media": cdn_images()}
    assert bot_media.notification_image({**item, **overrides}) == item["image"]


async def test_grid_endpoint_preserves_order_and_reuses_cache(client, session, monkeypatch):
    raw = await dynamic(session, cdn_images())
    original, tiles = grid_tiles()
    requested = []

    def handler(request):
        requested.append(str(request.url))
        index = int(request.url.path.rsplit("/", 1)[-1].split(".")[0])
        output = BytesIO()
        tiles[index].save(output, "PNG")
        return httpx.Response(200, content=output.getvalue())

    mock_cdn(monkeypatch, handler)
    path = f"/api/contents/{raw.id}/notification-image.jpg"
    response = await client.get(path)
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (2400, 2400)
        for index in range(9):
            point = (index % 3 * 800 + 400, index // 3 * 800 + 400)
            assert max(abs(a - b) for a, b in zip(image.getpixel(point), original.getpixel(point))) <= 3
    assert len(requested) == 9 and all(url.endswith("@1200w.jpg") for url in requested)
    assert (await client.get(path)).content == response.content
    assert len(requested) == 9
    raw.hidden = True
    await session.commit()
    assert (await client.get(path)).status_code == 404


def test_portrait_and_landscape_keep_all_edges():
    portrait = Image.new("RGBA", (100, 300), "red")
    portrait.putpixel((0, 0), (0, 0, 255, 255))
    content = BytesIO()
    portrait.save(content, "PNG")
    thumbnail = bot_media._thumbnail(content.getvalue())
    assert thumbnail.size == (100, 300)
    assert thumbnail.getpixel((0, 0))[2] > 200
    landscape = thumbnail.transpose(Image.Transpose.ROTATE_90)
    assert bot_media._compose([thumbnail, landscape]) is None


def test_rectangular_tiles_and_repeated_tiles_keep_their_places():
    images = cdn_images()
    images[4] = images[0]
    assert bot_media.bilibili_images(images) == images
    _, tiles = grid_tiles(width=600, height=300)
    with Image.open(BytesIO(bot_media._compose(tiles))) as image:
        assert image.size == (1800, 900)
        assert image.getpixel((599, 299)) != (255, 255, 255)
        assert image.getpixel((600, 300)) != (255, 255, 255)


def test_banner_tiles_do_not_pull_detail_posters_into_the_main_image():
    original, tiles = grid_tiles(rows=2)
    details = [Image.new("RGB", (100, 1000), "white") for _ in range(3)]
    assert bot_media._grid_count([*tiles, *details]) == 6
    with Image.open(BytesIO(bot_media._compose([*tiles, *details]))) as image:
        assert image.size == original.size == (2400, 1600)


@pytest.mark.parametrize("color", ["white", "black", "blue"])
def test_matching_plain_borders_do_not_make_an_album_a_grid(color):
    assert bot_media._compose([Image.new("RGB", (100, 100), color) for _ in range(9)]) is None


async def test_independent_album_redirects_to_original_main_image(client, session, monkeypatch):
    raw = await dynamic(session, cdn_images())
    calls = []
    def handler(request):
        calls.append(request.url)
        index = int(request.url.path.rsplit("/", 1)[-1].split(".")[0])
        output = BytesIO()
        Image.new("RGB", (100, 100), (index * 25, 255 - index * 20, index * 15)).save(output, "PNG")
        return httpx.Response(200, content=output.getvalue())
    mock_cdn(monkeypatch, handler)
    path = f"/api/contents/{raw.id}/notification-image.jpg"
    for _ in range(2):
        response = await client.get(path, follow_redirects=False)
        assert response.status_code == 307 and response.headers["location"] == cdn_images()[0]
    assert len(calls) == 9


async def test_failed_detail_image_does_not_break_the_main_banner(client, session, monkeypatch):
    raw = await dynamic(session, cdn_images())
    _, tiles = grid_tiles(rows=2)
    def handler(request):
        index = int(request.url.path.rsplit("/", 1)[-1].split(".")[0])
        if index >= 6:
            return httpx.Response(503)
        output = BytesIO()
        tiles[index].save(output, "PNG")
        return httpx.Response(200, content=output.getvalue())
    mock_cdn(monkeypatch, handler)
    response = await client.get(f"/api/contents/{raw.id}/notification-image.jpg")
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (2400, 1600)


async def test_failed_download_falls_back_without_caching_partial_grid(client, session, monkeypatch):
    raw = await dynamic(session, cdn_images(3))
    mock_cdn(monkeypatch, lambda request: httpx.Response(503))
    response = await client.get(f"/api/contents/{raw.id}/notification-image.jpg", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == cdn_images()[0]
    assert not bot_media._CACHE


async def test_grid_endpoint_rejects_non_cdn_images(client, session, monkeypatch):
    raw = await dynamic(session, ["http://127.0.0.1/secret", "https://i0.hdslb.com.evil.test/bfs/a.jpg"])
    def reject_download(request):
        raise AssertionError("不允许请求 B 站图片 CDN 以外的地址")
    mock_cdn(monkeypatch, reject_download)
    assert (await client.get(f"/api/contents/{raw.id}/notification-image.jpg")).status_code == 404
