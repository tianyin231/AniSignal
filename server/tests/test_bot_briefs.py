import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import AiUsage
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


def test_relative_dates_preserve_game_names_and_use_beijing_day():
    assert briefs.dated_text('《明日方舟》今日更新，明日晚播出。', datetime(2026, 10, 8, 18)) == '《明日方舟》10月9日更新，10月10日晚播出。'
    assert briefs.dated_text('明日方舟新EP', datetime(2026, 10, 9)) == '明日方舟新EP'


def test_publication_labels_use_source_time_and_preserve_multiple_publishers():
    assert briefs.publication_label({'published_at': datetime(2026, 10, 9, 6),
                                     'collected_at': datetime(2026, 10, 9, 7, 9)}) == '发布：10/09 14:00'
    assert briefs.publication_label({'published_at': None, 'collected_at': datetime(2026, 10, 9, 7, 9)}) == ''
    assert briefs.publication_label({'sources': [
        {'published_at': '2026-10-09T14:00:00+08:00'},
        {'published_at': datetime(2026, 10, 9, 4, tzinfo=timezone.utc)},
        {'published_at': '2026-10-09T06:00:00'},
        {'published_at': None},
    ]}) == '发布：10/09 12:00 / 10/09 14:00'


async def test_brief_dates_do_not_present_generation_time_as_publication(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={}))
    sources = [content(1, published_at=datetime(2026, 10, 9, 6)),
               content(2, published_at=None, collected_at=datetime(2026, 10, 9, 7, 9))]
    for mode in ('daily', 'updates'):
        brief = await briefs.build_brief(session, sources, datetime(2026, 10, 9, 7, 40), mode=mode)
        text = briefs.format_brief(brief)
        assert '发布：10/09 14:00' in text
        assert '15:40' not in text and '15:09' not in text
        assert brief['items'][0]['published_label'] == '发布：10/09 14:00'
        assert brief['items'][1]['published_label'] == ''
        assert '发布：10/09 14:00' in briefs.format_detail(brief['items'][0])
        assert '发布：' not in briefs.format_detail(brief['items'][1])


def test_fallback_uses_activity_fact_instead_of_cutting_character_dialogue():
    quote = '“欢迎回来，我的主人。如果您还没从繁忙的工作中回过神来，我不介意用这根皮鞭帮您清醒一下哦……咦？您当真了？”'
    item = content(1, community='blhx', title=quote,
                   text='#碧蓝航线#\n' + quote + '\n限时活动【严厉女仆不严厉】即将开启！\n活动期间完成七日任务，可获得限定换装。')
    result = briefs.fallback_items([item])[0]
    assert '严厉女仆不严厉' in result['title']
    assert '欢迎回来' not in result['title']


async def test_editor_groups_only_verified_sources_and_preserves_expansion(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(json.dumps({'items': [
        {'indices': [1, 2], 'title': '4.6版本上线时间', 'summary': '9月28日上线，专题页已发布。'},
        {'indices': [99], 'title': '假的', 'summary': '送10000钻石'},
    ]}), {})))
    items = await briefs.select_brief_items(session, [content(1, event_id=11, dedupe='first'),
                                                    content(2, event_id=22, dedupe='second')], datetime(2026, 9, 20))
    assert len(items) == 1 and [s['id'] for s in items[0]['sources']] == [1, 2]
    assert [(s['event_id'], s['dedupe']) for s in items[0]['sources']] == [(11, 'first'), (22, 'second')]
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


async def test_shared_copy_survives_restart_and_tracks_source_facts(session, session_factory, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={
        'api_key': 'fake', 'base_url': 'http://model', 'model': 'test',
    }))
    chat = AsyncMock(return_value=(json.dumps({'items': [{
        'indices': [1], 'title': '4.6版本上线时间', 'summary': '星穹铁道4.6版本将在9月28日上线。',
    }]}, ensure_ascii=False), {}))
    monkeypatch.setattr(briefs, 'chat', chat)
    source = content(1, dedupe='first')
    first = await briefs.build_shared_brief(session, [source], datetime(2026, 9, 20))
    async with session_factory() as restarted:
        second = await briefs.build_shared_brief(restarted, [{**source, 'id': 2, 'dedupe': 'second'}],
                                                 datetime(2026, 9, 20), mode='daily')
        assert first['items'][0]['title'] == second['items'][0]['title']
        assert first['items'][0]['summary'] == second['items'][0]['summary']
        assert second['items'][0]['sources'][0]['id'] == 2
        assert second['items'][0]['sources'][0]['dedupe'] == 'second'
        assert chat.await_count == 1
        changed = {**source, 'text': '4.6版本调整至9月29日上线，专题页已发布。'}
        chat.return_value = (json.dumps({'items': [{
            'indices': [1], 'title': '4.6版本时间调整', 'summary': '星穹铁道4.6版本调整至9月29日上线。',
        }]}, ensure_ascii=False), {})
        third = await briefs.build_shared_brief(restarted, [changed], datetime(2026, 9, 20))
        assert chat.await_count == 2 and '9月29日' in third['items'][0]['summary']


async def test_shared_copy_does_not_repeat_old_facts_in_a_group_followup(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={
        'api_key': 'fake', 'base_url': 'http://model', 'model': 'test',
    }))
    chat = AsyncMock(return_value=(json.dumps({'items': [{
        'indices': [1], 'title': '活动开放', 'summary': '星穹铁道活动开放，完成七日任务可获得换装。',
    }]}, ensure_ascii=False), {}))
    monkeypatch.setattr(briefs, 'chat', chat)
    source = content(1, title='活动开放', text='活动开放，完成七日任务可获得换装。\n活动追加10月10日上线时间。')
    first = await briefs.build_shared_brief(session, [source], datetime(2026, 9, 20))
    followup = {**source, 'is_followup': True, 'new_points': ['活动追加10月10日上线时间。'],
                'previous_points': ['活动开放，完成七日任务可获得换装。']}
    updated = await briefs.build_shared_brief(session, [followup], datetime(2026, 9, 20))
    fresh_group = await briefs.build_shared_brief(session, [source], datetime(2026, 9, 20))
    assert chat.await_count == 1
    assert '补充' in briefs.format_brief(updated) and '10月10日' in briefs.format_brief(updated)
    assert '七日任务' not in briefs.format_brief(updated)
    assert first['items'][0]['summary'] == fresh_group['items'][0]['summary']
    assert not fresh_group['items'][0]['is_followup']


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


def test_fallback_removes_boilerplate_and_keeps_whole_chinese_sentences():
    item = content(1, title='标题：#鸣潮# 云游中国合作影像',
                   text='正文：#鸣潮# 云游中国合作影像。多个地方政府部门提供鸣谢支持。'
                        + '这段很长的说明' * 30 + '。\n官方网站：https://example.com/download')
    result = briefs.fallback_items([item])[0]
    assert result['title'] == '云游中国合作影像'
    assert result['summary'] == '多个地方政府部门提供鸣谢支持。'
    assert '#' not in result['summary'] and '…' not in result['summary']
    assert '官方网站' in result['sources'][0]['text']


def test_generic_event_summary_falls_back_to_specific_source_fact():
    item = content(1, title='云游中国合作影像', summary='官方版本/活动信息。',
                   text='#鸣潮# 多个地方政府部门提供鸣谢支持。')
    result = briefs.fallback_items([item])[0]
    assert result['summary'] == '多个地方政府部门提供鸣谢支持。'
    repeated = briefs.fallback_items([content(2, title='新区域预告其二', text='新区域预告其二已发布。')])[0]
    assert repeated['summary'] == ''


async def test_explicit_empty_editor_selection_is_not_filled(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(' {"items": []} ', {})))
    assert await briefs.select_brief_items(session, [content(1)], datetime(2026, 9, 20)) == []


async def test_updates_keep_two_topics_daily_three_and_daily_expansion_number_is_valid(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={}))
    items = [content(n, title=f'官方合作影像{n}', text=f'合作影像{n}已上线。') for n in range(4)]
    updates = await briefs.build_brief(session, items)
    daily = await briefs.build_brief(session, items, mode='daily')
    assert len(updates['items']) == 2
    assert len(daily['items']) == 3
    single = await briefs.build_brief(session, items[:1], mode='daily')
    assert '/展开 1' in briefs.format_brief(single)
    assert '/展开 2' not in briefs.format_brief(single)
    assert '/展开 2' in briefs.format_brief(daily)
    assert '/展开' not in briefs.format_brief(updates)


async def test_updates_are_complete_shares_and_arknights_is_not_rejected_as_a_date(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    title = '明日方舟新EP《大地之盐》'
    share = title + '上线啦，10月9日起各音乐平台都能听，伴奏版也一起上架。'
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(json.dumps({'items': [
        {'indices': [1], 'title': title, 'summary': share}]}), {})))
    source = content(1, community='arknights', title='《明日方舟》EP - 大地之盐',
                     text='10月9日大地之盐正式上架各音乐平台，包含伴奏版。', published_at=datetime(2026, 10, 9, 3))
    brief = await briefs.build_brief(session, [source], datetime(2026, 10, 9, 4))
    assert briefs.format_brief(brief) == share + '\n发布：10/09 11:00'
    assert brief['items'][0]['share_text'] == share
    assert brief['items'][0]['sources'][0]['title'] == source['title']
    assert source['url'] in briefs.format_detail(brief['items'][0])
    brief['items'].append({**brief['items'][0], 'share_text': '鸣潮先约电台深夜频道更新啦。', 'community': 'wuwa'})
    assert briefs.format_brief(brief) == f'1. {share}\n发布：10/09 11:00\n\n2. 鸣潮先约电台深夜频道更新啦。\n发布：10/09 11:00'


async def test_share_text_cannot_bypass_date_or_fact_checks(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    mocked = AsyncMock()
    monkeypatch.setattr(briefs, 'chat', mocked)
    source = content(1)
    for summary in ('明天上线啦。', '昨日上线啦。', '免费送99999星琼啦。'):
        mocked.return_value = (json.dumps({'items': [{'indices': [1], 'title': '版本上线', 'summary': summary}]}), {})
        result = await briefs.build_brief(session, [source])
        assert summary not in briefs.format_brief(result)
        assert 'share_text' not in result['items'][0]


async def test_editor_keeps_complete_summary_over_old_42_character_limit(session, monkeypatch):
    title = '新区域预告其二上线'
    summary = '这次分享的是新区域预告的第二部分，视频已经上线，可以接着前一支预告看。共鸣者心PV也在同一批资料中。'
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(json.dumps({'items': [
        {'indices': [1], 'title': title, 'summary': summary}]}), {})))
    result = await briefs.select_brief_items(session, [content(1, title=title, text=summary)], datetime(2026, 9, 20))
    assert len(summary) > 42 and result[0]['summary'] == summary
    assert '…' not in briefs.format_brief({'title': '刚刚的重点', 'date': '09/20', 'items': result})


def test_same_version_english_chinese_sources_do_not_mix_independent_topics():
    chinese = briefs._source(content(1, title='3.7版本预告', text='3.7版本预告上线。'))
    english = briefs._source(content(2, title='Version 3.7 Trailer', text='The Version 3.7 trailer is out.'))
    merchandise = briefs._source(content(3, title='3.7版本纪念盲盒预售', text='3.7版本主题商品开放预售。'))
    assert briefs._compatible_sources([chinese, english])
    assert not briefs._compatible_sources([chinese, merchandise])


async def test_foreign_source_is_translated_by_editor_and_not_cut_in_fallback(session, monkeypatch):
    item = content(1, title='[PRE-LIVE] Countdown to the finale', text='The countdown begins for the finale.')
    assert briefs.fallback_items([item]) == []
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(json.dumps({'items': [
        {'indices': [1], 'title': '终场直播倒计时', 'summary': '终场直播进入倒计时。'}]}), {})))
    result = await briefs.select_brief_items(session, [item], datetime(2026, 9, 20))
    assert result[0]['title'] == '终场直播倒计时'
    assert '[PRE-LIVE]' in result[0]['sources'][0]['title']


async def test_english_month_translation_keeps_supported_chinese_date(session, monkeypatch):
    item = content(1, title='Version 3.7 arrives on September 30th', text='Version 3.7 arrives on September 30th (UTC+8).')
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    response = {'items': [{'indices': [1], 'title': '3.7版本上线时间', 'summary': '9月30日上线。'}]}
    mocked = AsyncMock(return_value=(json.dumps(response), {}))
    monkeypatch.setattr(briefs, 'chat', mocked)
    result = await briefs.select_brief_items(session, [item], datetime(2026, 9, 29))
    assert result[0]['summary'] == '9月30日上线。'
    response['items'][0]['summary'] = '9月29日上线。'
    mocked.return_value = (json.dumps(response), {})
    assert await briefs.select_brief_items(session, [item], datetime(2026, 9, 29)) == []


def test_source_keeps_deduplication_metadata_and_calendar_expands_full_list():
    source = briefs._source(content(1, event_id=42, dedupe='event-42', content_ids=[1, 2], notification_keys=['event-42']))
    assert (source['event_id'], source['dedupe'], source['content_ids']) == (42, 'event-42', [1, 2])
    assert source['notification_keys'] == ['event-42']
    item = {'title': '明日前的收尾提醒', 'summary': '几个活动将截止。', 'calendar_entries': [
        {'title': f'活动{index}', 'kind': 'activity', 'stage_at': '2026-09-30T02:00:00',
         'url': f'https://example.com/{index}'} for index in range(12)]}
    detail = briefs.format_detail(item)
    assert '12. 活动11' in detail and '09/30 10:00 截止（北京时间）' in detail
    assert 'https://example.com/11' in detail


def test_summary_fits_card_without_truncating_the_last_sentence():
    first = '新区域预告的第二部分已经上线，可以与前一支预告接着看。'
    following = '另一支影像也已公布，更多的完整介绍会在后续视频中继续放出，请以原始资料中的说明为准。'
    item = content(1, title='新区域预告', text=first + following)
    summary = briefs.fallback_items([item])[0]['summary']
    assert summary == first and len(summary) <= 60
    assert not summary.endswith('…')
    long_intro = '包含英文副标题的很长开场介绍' * 8 + '。'
    assert briefs.fallback_items([content(2, title='版本预告', text=long_intro + first)])[0]['summary'] == first


async def test_pre_live_is_not_promoted_to_a_version_preview_program(session, monkeypatch):
    item = content(1, title='[PRE-LIVE] Countdown to the Finale', text='Version 3.7 arrives on September 30th.')
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(json.dumps({'items': [
        {'indices': [1], 'title': '3.7版本前瞻预告', 'summary': '前瞻节目已经发布，版本于9月30日上线。'}]}), {})))
    result = await briefs.select_brief_items(session, [item], datetime(2026, 9, 29))
    assert result[0]['title'] == '3.7版本直播预热'
    assert '前瞻' not in result[0]['summary']
    assert '9月30日' in result[0]['summary']


async def test_acknowledgements_cannot_become_collaboration_or_joint_production(session, monkeypatch):
    item = content(1, title='云游中国特别合作影像', text='特别鸣谢济南、扬州等地文旅部门。')
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    mocked = AsyncMock()
    monkeypatch.setattr(briefs, 'chat', mocked)
    for unsupported in ('联动济南、扬州文旅部门推出影像。', '与济南、扬州文旅部门联合制作影像。'):
        mocked.return_value = (json.dumps({'items': [{'indices': [1], 'title': '云游中国新影像', 'summary': unsupported}]}), {})
        result = await briefs.select_brief_items(session, [item], datetime(2026, 9, 29))
        assert result[0]['title'] == '云游中国特别合作影像'
        assert result[0]['summary'] == '特别鸣谢济南、扬州等地文旅部门。'


def test_acknowledgements_do_not_invent_video_placement_or_department_counts():
    evidence = '特别鸣谢济南市文化和旅游局、襄阳市人民政府。'
    assert briefs._grounded_copy('合作影像', '片尾特别鸣谢相关单位。', evidence) is None
    assert briefs._grounded_copy('合作影像', '特别鸣谢六地文旅部门。', evidence) is None
    assert briefs._grounded_copy('合作影像', '公告还列出了济南、襄阳相关单位的鸣谢。', evidence)


@pytest.mark.parametrize('summary', ['', '这是一段没有句号的冗长公告' * 12, '免费送99999星琼。'])
async def test_rejected_editor_copy_is_recorded_and_fallback_stays_short(session, monkeypatch, caplog, summary):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(json.dumps({'items': [
        {'indices': [1], 'title': '限时任务开启', 'summary': summary}]}), {})))
    item = content(1, title='限时任务开启', text='完成七日任务，可获得限定换装。' + '后续宣传说明' * 50,
                   summary='这个旧事件摘要声称免费送99999星琼。')
    brief = await briefs.build_brief(session, [item])
    body = briefs.format_brief(brief)
    assert body.splitlines()[0] == '崩坏星穹铁道｜限时任务开启，完成七日任务，可获得限定换装。'
    assert '99999' not in body and '…' not in body
    usage = await session.scalar(select(AiUsage).where(AiUsage.task == 'bot_brief'))
    assert usage.status == 'fallback' and 'status=fallback' in caplog.text
    reason = 'unsupported_fact' if '99999' in summary else 'summary_too_long' if len(summary) > 60 else 'empty_or_foreign_summary'
    assert reason in caplog.text


async def test_editor_partial_validation_preserves_valid_rows_and_records_status(session, monkeypatch):
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(json.dumps({'items': [
        None, {'indices': [1], 'title': '版本情报', 'summary': '4.6版本将在9月28日上线。'}]}), {})))
    items = await briefs.select_brief_items(session, [content(1)], datetime(2026, 9, 20), limit=1)
    assert items[0]['share_text'] == '4.6版本将在9月28日上线。'
    usage = await session.scalar(select(AiUsage).where(AiUsage.task == 'bot_brief'))
    assert usage.status == 'partial'


def test_fallback_share_does_not_repeat_title_or_cut_a_sentence():
    item = content(1, title='新EP上架', text='新EP上架啦，伴奏版也能听。')
    line = briefs.format_brief({'mode': 'updates', 'items': briefs.fallback_items([item])}).splitlines()[0]
    assert line.count('新EP上架') == 1
    item = content(2, title='作品标题' * 10, text='这是一条完整的事实说明。' + '细节描述' * 10 + '。')
    line = briefs.format_brief({'mode': 'updates', 'items': briefs.fallback_items([item])}).splitlines()[0]
    assert len(line) < 90 and line.endswith('。') and '…' not in line


def test_recency_uses_all_source_publication_times_and_normalizes_timezone():
    now = datetime(2026, 10, 9, 8)
    assert briefs.is_recent_item({'published_at': '2026-10-09T14:00:00+08:00'}, now)
    assert not briefs.is_recent_item({'published_at': None, 'collected_at': now}, now)
    assert not briefs.is_recent_item({'published_at': now + timedelta(minutes=1)}, now)
    assert not briefs.is_recent_item({'sources': [{'published_at': now}, {'published_at': now - timedelta(days=2)}]}, now)


def test_real_announcement_fallback_prefers_time_over_character_greeting_and_album_lore():
    activity = content(1, community='wuwa', title='[天工寻物]限时战斗活动',
        text='天工匠见习生离纱，向你报到！ ✦活动时间✦ 2026年10月8日04:00 ~ 2026年10月26日03:59（服务器时间） '
             '✦奖励内容✦ 星声*500，以及其他养成材料等。')
    album = content(2, community='arknights', title='《明日方舟》EP - 大地之盐',
        text='10 月 9 日 大地之盐 正式上架各音乐平台 【专辑介绍】 褒奖、诋毁、信任、怀疑、怜悯、迁怒，一滴远离大海的水。')
    body = briefs.format_brief({'mode': 'updates', 'items': briefs.fallback_items([activity, album])})
    assert '向你报到' not in body and '褒奖' not in body and '…' not in body
    assert '2026年10月26日03:59（服务器时间）' in body
    assert '正式上架各音乐平台' in body


async def test_editor_sees_tail_facts_before_prompt_budget_and_keeps_reward_condition(session, monkeypatch):
    intro = '这段是没有具体事实的宣传说明。' * 100
    reward = '完成七日任务，可获得阿尔及利亚限定换装。'
    timing = '活动时间：10月15日维护后至11月5日维护。'
    item = content(1, community='blhx', title='限时活动【严厉女仆不严厉】', text=intro + reward + timing)
    share = '碧蓝航线女仆换装做七日任务就能拿，活动10月15日维护后开。'
    async def edit(config, system, prompt, **kwargs):
        payload = json.loads(prompt.split('资料：', 1)[1])[0]
        assert payload['key_points'][:2] == [reward, timing]
        assert intro not in prompt
        return json.dumps({'items': [{'indices': [1], 'title': '女仆换装活动', 'summary': share}]}), {}
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', edit)
    brief = await briefs.build_brief(session, [item])
    assert brief['items'][0]['share_text'] == share
    assert reward in brief['items'][0]['sources'][0]['text']
    assert '完成七日任务' in briefs.fallback_items([item])[0]['summary']


async def test_followup_payload_and_fallback_only_include_new_facts(session, monkeypatch):
    old = content(1, title='天工寻物活动', text='活动10月12日18:00截止。奖励包含500星声。')
    new = content(2, title=old['title'], text='活动截止时间改为10月15日18:00。奖励包含500星声。')
    new = {**new, 'is_followup': True, 'previous_points': briefs.news_points(old),
           'new_points': briefs.changed_points(new, old)}
    async def edit(config, system, prompt, **kwargs):
        payload = json.loads(prompt.split('资料：', 1)[1])[0]
        assert payload['is_followup'] and '500' not in str(payload['key_points'])
        assert '500' in str(payload['previous_points'])
        return json.dumps({'items': [{'indices': [1], 'title': '天工寻物时间调整',
                                     'summary': '天工寻物改到10月15日18:00截止啦。'}]}), {}
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={'api_key': 'fake', 'base_url': 'http://model', 'model': 'test'}))
    monkeypatch.setattr(briefs, 'chat', edit)
    brief = await briefs.build_brief(session, [new])
    assert brief['items'][0]['share_text'] == '天工寻物改到10月15日18:00截止啦。'
    assert '补充' in briefs.format_brief(brief) and '500' not in briefs.format_brief(brief)
    assert '500' in brief['items'][0]['sources'][0]['text']
    # 若模型重播旧奖励，数字校验应回退为新截止时间，不能恢复整篇旧公告。
    monkeypatch.setattr(briefs, 'chat', AsyncMock(return_value=(json.dumps({'items': [
        {'indices': [1], 'title': '天工寻物时间调整', 'summary': '天工寻物仍送500星声。'}]}), {})))
    brief = await briefs.build_brief(session, [new])
    assert '500' not in briefs.format_brief(brief) and '10月15日18:00' in briefs.format_brief(brief)


async def test_followup_without_confirmed_changes_stays_quiet_when_editor_is_unavailable(session, monkeypatch):
    old = content(1, title='新区域预告', text='预告影像现已上线。')
    new = content(2, title='新区域预告', text='预告视频已经发布。')
    new = {**new, 'is_followup': True, 'previous_points': briefs.news_points(old), 'new_points': briefs.changed_points(new, old)}
    monkeypatch.setattr(briefs, 'get_ai_config', AsyncMock(return_value={}))
    assert (await briefs.build_brief(session, [new]))['items'] == []
    assert briefs.changed_points(old, {**old, 'text': ' 预告影像现已上线！'}) == []


def test_new_video_in_same_event_is_not_hidden_by_generic_release_copy():
    old = content(1, title='3.7版本新区域预告其一', text='预告影像已发布。')
    new = content(2, title='3.7版本新区域预告其二', text=old['text'])
    assert briefs.changed_points(new, old) == ['3.7版本新区域预告其二：预告影像已发布。']


def test_gameplay_score_is_not_ranked_as_an_activity_reward():
    item = content(1, title='天工寻物', text='每波次敌人清理完成后获得加时，倒计时结束后玩家以当前获得的款项结束挑战。'
                   '✦活动时间✦ 10月8日04:00至10月26日03:59。✦奖励内容✦ 星声*500、养成材料。')
    assert briefs.news_points(item)[:2] == ['活动时间：10月8日04:00至10月26日03:59。', '奖励内容：星声*500。']


def test_followup_compares_independent_clauses_without_losing_reward_conditions():
    old = content(1, title='测试活动', text='活动10月12日18:00截止，奖励包含500星声。')
    new = {**old, 'text': '活动10月15日18:00截止，奖励包含500星声。'}
    assert briefs.changed_points(new, old) == ['活动10月15日18:00截止。']
    conditional = content(2, title='任务奖励', text='完成七日任务，奖励包含500星声。')
    assert briefs.news_points(conditional) == ['完成七日任务，奖励包含500星声。']


def test_activity_eligibility_cannot_become_a_reward_claim():
    evidence = '奖励内容：星声*500。开放条件：联觉等级达到14级，并激活四鸣井。'
    assert briefs._grounded_copy('天工寻物活动', '做四鸣井任务就能拿500星声。', evidence) is None
    assert briefs._grounded_copy('天工寻物活动', '活动奖励含500星声，参与需14级并激活四鸣井。', evidence)
    assert briefs._grounded_copy('七日任务', '完成七日任务就能拿限定换装。', '完成七日任务，可获得限定换装。')
