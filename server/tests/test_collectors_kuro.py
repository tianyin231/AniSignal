r"""库洛（鸣潮）公告采集器：notice_id 白名单（SEC-027）。

`notice_id` 来自库洛 CDN 的 notice.json，原样插进出站 URL 的 path 段。上游 JSON 一旦
被污染，`../`、`?`、`#`、`%2f` 就会把请求指到同一 host 的任意路径（httpx 只归一化
字面 `..`，不解码 `..%2f`，也不阻止 `?`/`#` 截断路径段）。这里钉住白名单边界。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.collectors import kuro as kuro_module
from app.collectors.kuro import KuroCollector
from app.models.enums import Platform


class _FakeClient:
    """桩客户端：`get_json` 已被 mock，这里只需要 `async with` 能成立。

    不能用真的 `httpx.AsyncClient()`——它会读环境里的 ALL_PROXY 并构造 mount，
    在带代理的 CI/沙箱上直接抛 InvalidURL，与用例想验的事无关。
    """

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def kuro(monkeypatch):
    monkeypatch.setattr(kuro_module, "httpx", SimpleNamespace(AsyncClient=_FakeClient))
    return KuroCollector(Platform.KURO_GLOBAL)


def _list_payload(notice_ids):
    return {"game": [{"noticeId": nid, "tittle": f"公告 {nid}"} for nid in notice_ids]}


async def test_notice_id_whitelist_accepts_normal_ids(kuro, monkeypatch):
    """正常形态的 ID 全部放行并照常抓详情（白名单不能把正常公告挡在门外）。"""
    ids = ["50553", "abc_12-x", "0"]
    detail_calls: list[str] = []

    async def _fake_get_json(client, url, **kwargs):
        if url.endswith("/notice.json"):
            return _list_payload(ids)
        detail_calls.append(url)
        return {"textContent": f"正文 {len(detail_calls)}"}

    monkeypatch.setattr(kuro_module, "get_json", _fake_get_json)
    items = await kuro.fetch_notices(10)
    assert [i["entry"]["noticeId"] for i in items] == ids
    # 三条都去抓了详情，且 URL 各自独立
    assert len(detail_calls) == 3
    assert items[0]["detail"] == {"textContent": "正文 1"}
    assert all("gamenotice/content" in u for u in detail_calls)


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../../../other/resource",
        "..%2f..%2fadmin",
        "x?foo=1",
        "x#frag",
        "x@evil.com",
        "x:8080",
        "a" * 65,
        "",
        " ",
    ],
)
async def test_notice_id_whitelist_rejects_url_shaping_ids(kuro, monkeypatch, bad_id):
    """SEC-027：能改变出站 URL 语义的 notice_id 一律不抓详情，也不进 detail_url。"""
    get_json = AsyncMock(side_effect=[_list_payload([bad_id]), None])
    monkeypatch.setattr(kuro_module, "get_json", get_json)
    items = await kuro.fetch_notices(10)
    assert items == [], f"{bad_id!r} 应被整条跳过"
    # 只有列表请求发出去了，没有构造出任何详情 URL
    assert get_json.await_count == 1


async def test_notice_id_whitelist_only_skips_the_bad_entry(kuro, monkeypatch):
    """坏条目被跳过，但同批的好条目照常处理——不能因为一条脏数据丢掉整轮采集。"""
    get_json = AsyncMock(
        side_effect=[
            _list_payload(["../../evil", "777", "x?y=1"]),
            {"textContent": "<p>ok</p>"},
        ]
    )
    monkeypatch.setattr(kuro_module, "get_json", get_json)
    items = await kuro.fetch_notices(10)
    assert [i["entry"]["noticeId"] for i in items] == ["777"]
    assert items[0]["detail"] == {"textContent": "<p>ok</p>"}


async def test_detail_url_is_built_under_the_fixed_cdn_path(kuro, monkeypatch):
    """白名单放行的 ID 也只能落在固定 CDN 路径下（回归：别再把 path 段拼散）。"""
    get_json = AsyncMock(side_effect=[_list_payload(["50553"]), None])
    monkeypatch.setattr(kuro_module, "get_json", get_json)
    items = await kuro.fetch_notices(1)
    url = items[0]["detail_url"]
    assert url.startswith("https://aki-gm-resources-back.aki-game.net/gamenotice/content/")
    assert url.endswith("/50553/en.json")
    assert url.count("/gamenotice/content/") == 1


async def test_fetch_notices_survives_unparseable_payload(kuro, monkeypatch):
    """上游形状异常时退回通用提取，白名单同样生效（不能在这里抛出去炸掉整轮采集）。"""
    get_json = AsyncMock(side_effect=[{"unexpected": [{"id": "../../evil"}]}, None])
    monkeypatch.setattr(kuro_module, "get_json", get_json)
    items = await kuro.fetch_notices(10)
    assert items == []


async def test_fetch_rejects_non_notice_target_type(kuro):
    """target_type 契约不变（与 SEC-032 的按类型校验是两道独立的闸）。"""
    from app.collectors.base import CollectorError

    with pytest.raises(CollectorError):
        await kuro.fetch("uid", "123", 5)
