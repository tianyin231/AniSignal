"""库洛公告采集器：notice_id 只允许 ID 形态字符（防路径/参数注入）。"""

import pytest

from app.collectors import kuro as kuro_module
from app.models.enums import Platform


@pytest.fixture
def kuro_calls(monkeypatch):
    """把 get_json 换成记录 URL 的桩：列表返回给定条目，详情返回固定结构。"""
    calls: list[str] = []
    entries: list[dict] = []

    async def fake_get_json(client, url, **kwargs):
        calls.append(url)
        if url.endswith("notice.json"):
            return {"game": entries}
        return {"article": {"title": "t", "content": "<p>x</p>"}}

    monkeypatch.setattr(kuro_module, "get_json", fake_get_json)
    return calls, entries


async def test_dirty_notice_id_is_skipped_and_never_requested(kuro_calls):
    calls, entries = kuro_calls
    entries.extend(
        [
            {"noticeId": "12345", "title": "正常公告"},
            {"noticeId": "../../../../other/resource", "title": "路径穿越"},
            {"noticeId": "..%2f..%2fadmin", "title": "编码穿越"},
            {"noticeId": "x?foo=1", "title": "加查询串"},
            {"noticeId": "x#frag", "title": "加片段"},
            {"noticeId": "x@evil.com", "title": "改主机"},
            {"noticeId": "x:8080", "title": "改端口"},
            {"noticeId": "a" * 65, "title": "超长"},
            {"noticeId": "   ", "title": "纯空格"},
            {"noticeId": "", "title": "空"},
        ]
    )

    collector = kuro_module.KuroCollector(Platform.KURO_CN)
    await collector.fetch_notices(limit=20)

    detail_calls = [url for url in calls if not url.endswith("notice.json")]
    assert len(detail_calls) == 1
    assert detail_calls[0].endswith("/12345/zh-Hans.json")

    joined = "\n".join(calls)
    for bad in ("..", "%2f", "foo=1", "#frag", "evil.com", ":8080", "a" * 65):
        assert bad not in joined


async def test_valid_notice_id_formats_are_kept(kuro_calls):
    calls, entries = kuro_calls
    entries.extend(
        [
            {"noticeId": "abc-123_XY"},
            {"id": 987654},
            {"notice_id": "Z_9"},
        ]
    )

    collector = kuro_module.KuroCollector(Platform.KURO_GLOBAL)
    await collector.fetch_notices(limit=10)

    detail_calls = [url for url in calls if not url.endswith("notice.json")]
    assert len(detail_calls) == 3
    assert any(url.endswith("/abc-123_XY/en.json") for url in detail_calls)
    assert any(url.endswith("/987654/en.json") for url in detail_calls)
    assert any(url.endswith("/Z_9/en.json") for url in detail_calls)
