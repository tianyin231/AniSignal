import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from app.services import bot_briefs as briefs


def content(n, **extra):
    return {'id': n, 'title': f'版本情报{n}', 'text': '4.6版本将在9月28日上线，专题页已发布。',
            'community': 'hsr', 'identity': 'official', 'source': '官方', 'url': f'https://example.com/{n}',
            'published_at': datetime(2026, 9, 19, 10), **extra}


def test_relative_dates_are_anchored_to_source_and_brief_is_bounded():
    assert briefs.dated_text('今晚19点播出，明天回顾', datetime(2026, 9, 19, 10)) == '9月19日晚19点播出，9月20日回顾'
    items = briefs.fallback_items([content(n, title='长标题' * 40, text='很长的原始资料' * 100) for n in range(9)])
    brief = {'title': '今日速报', 'date': '09/20', 'items': items}
    text = briefs.format_brief(brief)
    assert len(items) == 3 and len(text) < 300
    assert 'http' not in text and '/展开' in text
    assert 'https://example.com/0' in briefs.format_detail(items[0])


async def test_editor_groups_only_verified_sources_and_preserves_expansion(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(json.dumps({'items': [
        {'indices': [1, 2], 'title': '4.6版本上线时间', 'summary': '9月28日上线，专题页已发布。'},
        {'indices': [99], 'title': '假的', 'summary': '送10000钻石'},
    ]}), {})))
    items = await briefs.select_brief_items(session, [content(1), content(2)], datetime(2026, 9, 20))
    assert len(items) == 1 and [s['id'] for s in items[0]['sources']] == [1, 2]
    assert 'https://example.com/2' in briefs.format_detail(items[0])


async def test_editor_failure_and_invented_facts_keep_compact_fallback(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    mocked = AsyncMock(return_value=(json.dumps({'items': [
        {'indices': [1], 'title': '免费送10000钻石', 'summary': '今晚送完'}]}), {}))
    monkeypatch.setattr(briefs, 'chat', mocked)
    result = await briefs.build_brief(session, [content(1)], mode='daily')
    assert result['items'][0]['title'] == '版本情报1'
    mocked.side_effect = TimeoutError()
    result = await briefs.build_brief(session, [content(n) for n in range(9)], mode='daily')
    assert len(result['items']) == 3 and len(briefs.format_brief(result)) < 300


def test_merging_keeps_official_and_community_claims_separate():
    official = briefs._source(content(1))
    community = briefs._source(content(2, identity='unknown'))
    assert not briefs._compatible_sources([official, community])
    assert briefs._compatible_sources([official, briefs._source(content(3))])


def test_merging_requires_a_shared_version_and_bounded_sources():
    version = briefs._source(content(1))
    bus = briefs._source(content(2, title='巡游巴士武汉站', text='9月22日线下活动开启，预约开放。'))
    other_version = briefs._source(content(3, text='4.7版本专题页已发布。'))
    assert not briefs._compatible_sources([version, bus])
    assert not briefs._compatible_sources([version, other_version])
    assert not briefs._compatible_sources([version] * 4)


async def test_empty_dynamic_in_old_buffer_cannot_use_event_summary(session):
    empty = content(1, platform='bilibili', content_type='dynamic', text='',
                    title='活动公告', summary='旧事件摘要不能替代抓取正文', url='https://t.bilibili.com/1')
    image_only = content(2, platform='bilibili', content_type='dynamic', text='',
                         title='动态', image='https://img.example/2.jpg', url='https://t.bilibili.com/2')
    video = content(3, platform='bilibili', content_type='video', text='', title='版本预告视频')
    result = await briefs.select_brief_items(session, [empty, image_only, video], datetime(2026, 9, 22))
    assert [item['id'] for item in result] == [2, 3]
    snapshot = briefs.single_item_brief(empty)
    source = snapshot['items'][0]['sources'][0]
    assert source['text'] == '' and source['platform'] == 'bilibili' and source['content_type'] == 'dynamic'
