"""默认发送原主图；连续切片才还原成高清主图。"""

import asyncio
import hashlib
import re
from collections import OrderedDict
from io import BytesIO
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageChops, ImageFile, ImageOps, ImageStat

from app.config import get_settings

CELL_SIZE = 1200
RENDER_VERSION = "main-v2"
_CACHE: OrderedDict[tuple[str, ...], bytes | None] = OrderedDict()
_RENDER_LOCK = asyncio.Lock()


def bilibili_images(values: list) -> list[str]:
    """只从 B 站图片 CDN 取图，不提供可指定任意网址的代理。"""
    images = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            parsed = urlparse(value)
        except ValueError:
            continue
        if (parsed.scheme not in {"http", "https"}
                or not re.fullmatch(r"i\d+\.hdslb\.com", parsed.hostname or "")
                or not parsed.path.startswith("/bfs/")):
            continue
        image = f"https://{parsed.hostname}{parsed.path.split('@', 1)[0]}"
        images.append(image)
    return images


def notification_image(item: dict) -> str | None:
    base = get_settings().public_base_url.rstrip("/")
    images = bilibili_images(item.get("media") or [])
    if (base and isinstance(item.get("id"), int) and item.get("platform") == "bilibili"
            and item.get("content_type") == "dynamic" and len(images) >= 3):
        version = hashlib.sha256((RENDER_VERSION + "\n" + "\n".join(images)).encode()).hexdigest()[:12]
        return f"{base}/api/contents/{item['id']}/notification-image.jpg?v={version}"
    return item.get("image")


def _thumbnail(content: bytes) -> Image.Image:
    with Image.open(BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image).convert("RGBA")
        # 保留原图像素，不把小图放大后伪装成高清图。
        image.thumbnail((CELL_SIZE, CELL_SIZE), Image.Resampling.LANCZOS)
        result = Image.new("RGB", image.size, "white")
        result.paste(image, (0, 0), image)
        return result


def _seam(left: Image.Image, right: Image.Image, vertical: bool) -> tuple[float, float]:
    width, height = left.size
    if vertical:
        edges = (left.crop((0, height - 1, width, height)), right.crop((0, 0, width, 1)))
        size = (64, 1)
    else:
        edges = (left.crop((width - 1, 0, width, height)), right.crop((0, 0, 1, height)))
        size = (1, 64)
    edges = [edge.convert("RGB").resize(size, Image.Resampling.BOX) for edge in edges]
    difference = sum(ImageStat.Stat(ImageChops.difference(*edges)).mean) / 3
    variation = max(ImageStat.Stat(edge).stddev[channel] for edge in edges for channel in range(3))
    return difference, variation


def _grid_count(images: list[Image.Image]) -> int:
    # 动态可能前六张是宣传画切片，后三张是详情长图，只还原连续的主画面。
    count = 0
    for image in images[:9]:
        if image.size != images[0].size:
            break
        count += 1
    for length in (9, 6, 3):
        if length > count or (length == 3 and count != 3):
            continue
        seams = [_seam(images[index], images[index + 1], False)
                 for index in range(length) if index % 3 != 2]
        seams += [_seam(images[index], images[index + 3], True) for index in range(length - 3)]
        # 尺寸相同还不够：接缝必须连续；纯色边框不能作为拼图的证据。
        if all(difference <= 18 for difference, _ in seams) and any(variation >= 12 for _, variation in seams):
            return length
    return 0


def _compose(images: list[Image.Image]) -> bytes | None:
    count = _grid_count(images)
    if not count:
        return None
    width, height = images[0].size
    canvas = Image.new("RGB", (3 * width, count // 3 * height), "white")
    for index, image in enumerate(images[:count]):
        canvas.paste(image, (index % 3 * width, index // 3 * height))
    output = BytesIO()
    canvas.save(output, "JPEG", quality=95, subsampling=0, optimize=True)
    return output.getvalue()


async def render_notification_image(images: list[str]) -> bytes | None:
    key = tuple(images)
    # 限制同时制图的数量；同一动态发往多个会话时复用成品。
    async with _RENDER_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
        slots = asyncio.Semaphore(3)
        async with asyncio.timeout(18), httpx.AsyncClient(timeout=8, trust_env=False,
                headers={"Referer": "https://t.bilibili.com/"}) as client:
            async def download(url, expected_ratio=None):
                async with slots:
                    async with client.stream("GET", f"{url}@{CELL_SIZE}w.jpg") as response:
                        response.raise_for_status()
                        content, parser = bytearray(), ImageFile.Parser()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if parser.image is None:
                                parser.feed(chunk)
                                if parser.image:
                                    ratio = parser.image.width / parser.image.height
                                    # 尺寸头一到就排除详情长图，不下载完整长图再缩成细条。
                                    if expected_ratio is None:
                                        if not 0.5 <= ratio <= 2:
                                            return None
                                    elif abs(ratio - expected_ratio) > expected_ratio * 0.01:
                                        return None
                        return await asyncio.to_thread(_thumbnail, bytes(content))

            first = await download(images[0])
            thumbnails = [first]
            if first is not None:
                thumbnails += await asyncio.gather(*(download(url, first.width / first.height)
                    for url in images[1:9]), return_exceptions=True)
            ready, failure = [], None
            for thumbnail in thumbnails:
                if thumbnail is None:
                    break
                if isinstance(thumbnail, Exception):
                    failure = thumbnail
                    break
                ready.append(thumbnail)
            result = await asyncio.to_thread(_compose, ready)
            if result is None and failure:
                raise failure
        _CACHE[key] = result
        if len(_CACHE) > 16:
            _CACHE.popitem(last=False)
        return result
