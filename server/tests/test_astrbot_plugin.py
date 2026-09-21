"""验证发送结果不明时不重发，以及工具操作者始终取自真实事件。"""
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def plugin(monkeypatch):
    def module(name, **attrs):
        obj = ModuleType(name)
        obj.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, obj)
        return obj

    def decorator(*args, **kwargs):
        return lambda fn: fn

    class Chain:
        def message(self, text):
            self.text = text
            self.image_url = None
            return self

        def url_image(self, url):
            self.image_url = url
            return self

        def file_image(self, path):
            self.image_url = path
            return self

    module('aiohttp', ClientError=ConnectionError)
    module('astrbot')
    module('astrbot.api', llm_tool=decorator, logger=SimpleNamespace(warning=lambda *a: None))
    module('astrbot.api.event', AstrMessageEvent=object,
        filter=SimpleNamespace(command=decorator, regex=decorator, on_llm_request=decorator, on_llm_response=decorator, event_message_type=decorator,
            on_decorating_result=decorator,
            EventMessageType=SimpleNamespace(ALL='all')))
    module('astrbot.api.star', Context=object, Star=object, register=decorator)
    module('astrbot.api.message_components', Face=type('Face', (), {}),
        Plain=type('Plain', (), {'__init__': lambda self, text=None: setattr(self, 'text', text or '')}))
    module('astrbot.core')
    module('astrbot.core.provider')
    module('astrbot.core.provider.entities', ProviderRequest=object)
    module('astrbot.core.message')
    module('astrbot.core.message.message_event_result', MessageChain=Chain)
    path = Path(__file__).parents[2] / 'integrations/astrbot_plugin_anisignal/main.py'
    spec = importlib.util.spec_from_file_location('plugin_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    obj = mod.AniSignalPlugin.__new__(mod.AniSignalPlugin)
    obj.api = AsyncMock(return_value={'ok':True})
    obj.render_delivery = AsyncMock(return_value='人格播报\n原始资料')
    obj.pending_ack = None
    obj.config = {}
    obj.targets = {}
    obj.last_activity = {}
    obj.last_briefs = {}
    obj.last_cat_reply = {}
    obj.cat_interjection_pending = set()
    obj.context = SimpleNamespace(get_platform_inst=lambda p: None, send_message=AsyncMock(return_value=True),
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value='cid-1'),
            get_conversation=AsyncMock(return_value=SimpleNamespace(persona_id=None)),
            add_message_pair=AsyncMock()))
    return obj


def delivery():
    return {'id':1,'umo':'qq:FriendMessage:self','receipt':'lease','body':'原始资料'}


async def test_successful_send_with_lost_ack_is_only_acknowledged_again(plugin):
    plugin.api.side_effect = ConnectionError('offline')
    with pytest.raises(ConnectionError):
        await plugin.send_delivery(delivery())
    assert plugin.context.send_message.await_count == 1
    assert plugin.pending_ack[1]['status'] == 'accepted'
    plugin.api.side_effect = None
    await plugin.api(*plugin.pending_ack)
    assert plugin.context.send_message.await_count == 1


async def test_model_failure_falls_back_to_original_and_timeout_is_unknown(plugin):
    plugin.render_delivery.side_effect = RuntimeError('model offline')
    plugin.context.send_message.side_effect = TimeoutError()
    await plugin.send_delivery(delivery())
    receipt = plugin.api.call_args.args[1]
    assert receipt['status'] == 'unknown' and receipt['rendered_body'] == '原始资料'
    assert plugin.context.send_message.await_count == 1


async def test_tool_identity_comes_from_message_not_arguments(plugin):
    event = SimpleNamespace(unified_msg_origin='qq:GroupMessage:real', get_sender_id=lambda:'real-user',
        message_obj=SimpleNamespace(message_id='real-message'))
    await plugin.reminder_tool(event, action='create', text='提醒', due_at='2026-10-01T08:00:00+08:00')
    payload = plugin.api.call_args.args[1]
    assert payload['umo'] == 'qq:GroupMessage:real' and payload['actor_id'] == 'real-user'
    assert payload['request_id'] == 'real-message'


async def test_failed_send_does_not_invent_success(plugin):
    plugin.context.send_message.return_value = False
    await plugin.send_delivery(delivery())
    assert plugin.api.call_args.args[1]['status'] == 'failed'


async def test_quota_429_is_distinguished_from_rate_limit_without_leaking_details(plugin):
    error = RuntimeError('account-private')
    error.status_code = 429
    error.body = {'error': {'message': 'account-private: insufficient balance'}}
    plugin.render_delivery.side_effect = error
    await plugin.send_delivery(delivery())
    receipt = plugin.api.call_args.args[1]
    assert receipt['status'] == 'accepted'
    assert receipt['error'] == '模型余额或额度不足，已使用原始资料'
    assert 'account-private' not in str(receipt)


def event(umo, targets=None):
    extras = {}
    return SimpleNamespace(unified_msg_origin=umo, get_sender_id=lambda: 'user',
        plain_result=lambda text: text, set_extra=extras.__setitem__, get_extra=lambda key: extras.get(key),
        is_at_or_wake_command=True, stop_event=lambda: extras.__setitem__('stopped', True))


async def test_session_info_reports_registration_state(plugin):
    plugin.targets = {'qq:GroupMessage:real': {'name': '鸣潮群', 'enabled': True}}
    text = [r async for r in plugin.session_info(event('qq:GroupMessage:real'))][0]
    assert '注册为「鸣潮群」' in text and '已启用' in text

    plugin.targets['qq:GroupMessage:real']['enabled'] = False
    text = [r async for r in plugin.session_info(event('qq:GroupMessage:real'))][0]
    assert '已暂停' in text

    plugin.targets = {}
    text = [r async for r in plugin.session_info(event('qq:GroupMessage:other'))][0]
    assert '尚未注册' in text and 'qq:GroupMessage:other' in text


async def test_paused_session_does_not_activate_tools_or_provider(plugin):
    plugin.targets = {'qq:GroupMessage:paused': {'name': 'x', 'enabled': False, 'tools': ['digest'], 'provider_id': 'p'}}
    plugin.last_activity = {}
    paused = event('qq:GroupMessage:paused')
    assert plugin.session_target(paused) is None
    await plugin.select_conversation_provider(paused)
    plugin.targets['qq:GroupMessage:paused']['enabled'] = True
    assert plugin.session_target(paused) is not None


async def test_message_events_track_last_activity_per_session(plugin):
    plugin.targets = {}
    plugin.last_activity = {}
    await plugin.select_conversation_provider(event('qq:GroupMessage:g1'))
    await plugin.select_conversation_provider(event('qq:GroupMessage:g2'))
    assert set(plugin.last_activity) == {'qq:GroupMessage:g1', 'qq:GroupMessage:g2'}


async def test_delivery_cover_image_is_attached_to_chain(plugin):
    row = delivery()
    row['images'] = ['https://i0.hdslb.com/cover.jpg']
    await plugin.send_delivery(row)
    chain = plugin.context.send_message.call_args.args[1]
    assert chain.image_url == 'https://i0.hdslb.com/cover.jpg'

    row['images'] = []
    await plugin.send_delivery(row)
    chain = plugin.context.send_message.call_args.args[1]
    assert chain.image_url is None


async def test_accepted_broadcast_is_recorded_into_conversation_history(plugin):
    await plugin.send_delivery(delivery())
    manager = plugin.context.conversation_manager
    manager.get_curr_conversation_id.assert_awaited_once_with('qq:FriendMessage:self')
    cid, user_msg, assistant_msg = manager.add_message_pair.await_args.args
    assert cid == 'cid-1'
    assert '每日早报' in user_msg['content'] or '资讯' in user_msg['content']
    assert assistant_msg['content'] == '人格播报\n原始资料'


async def test_history_recording_failure_does_not_break_receipt(plugin):
    plugin.context.conversation_manager.add_message_pair.side_effect = RuntimeError('db down')
    await plugin.send_delivery(delivery())
    receipt = plugin.api.call_args.args[1]
    assert receipt['status'] == 'accepted' and receipt['receipt'] == 'lease'


async def test_residual_emotion_markup_is_stripped_from_replies(plugin):
    Plain = sys.modules['astrbot.api.message_components'].Plain
    chain = [Plain('你好 &&reply&& 不错，&&happy&&')]
    ev = event('qq:GroupMessage:g')
    ev.get_result = lambda: SimpleNamespace(is_llm_result=lambda: True,
        get_plain_text=lambda: chain[0].text, chain=chain)
    await plugin.strip_residual_emotion_markup(ev)
    assert chain[0].text == '你好  不错，'

    chain = [Plain('正常回复没有标记')]
    ev.get_result = lambda: SimpleNamespace(is_llm_result=lambda: True,
        get_plain_text=lambda: chain[0].text, chain=chain)
    await plugin.strip_residual_emotion_markup(ev)
    assert chain[0].text == '正常回复没有标记'

    chain = [Plain('咪…  喵？\n呜—— &&confused&&')]
    ev.get_result = lambda: SimpleNamespace(is_llm_result=lambda: True,
        get_plain_text=lambda: chain[0].text, chain=chain)
    await plugin.strip_residual_emotion_markup(ev)
    assert chain[0].text == '咪…  喵？\n呜——'


async def test_cat_chat_does_not_inject_news_instructions(plugin):
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id='cat'), system_prompt='猫语')
    await plugin.add_instructions(event('qq:GroupMessage:g'), req)
    assert req.system_prompt == '猫语'
    plugin.api.assert_not_called()


@pytest.mark.parametrize('text,expected', [
    ('哈。嘶？||meme_start||，||meme_end||', '哈。嘶？'),
    (r'咪～\|\|meme\_start\|\|angry\|\|meme\_end\|\|  喵？', '咪～  喵？'),
    ('咪……\n||MEME_START||\nangry\n||MEME_END||\n喵！', '咪……\n\n喵！'),
    ('咪？||meme_start||unfinished', '咪？'),
    ('||meme_end||咪？', '咪？'),
    ('||meme_start||，||meme_end||', ''),
    ('咪～ (=^･ω･^=) &&happy&&', '咪～ (=^･ω･^=) &&happy&&'),
    ('喵……，哈？&annoyed&', '喵……，哈？&&annoyed&&'),
    ('咪？&& ANNOYED &', '咪？&&annoyed&&'),
    (r'咪？\&annoyed\&', '咪？&&annoyed&&'),
    ('咪？&amp;annoyed&amp;', '咪？&&annoyed&&'),
    ('咪？＆annoyed＆', '咪？&&annoyed&&'),
    ('咪？&annoyed', '咪？&&annoyed&&'),
    ('咪？&annoyed\n喵！', '咪？&&annoyed&&\n喵！'),
    ('咪？&&annoyed&&&&happy&&', '咪？&&annoyed&&&&happy&&'),
    ('咪？&annoyed&&sad&', '咪？&&annoyed&&&&sad&&'),
    ('咪～ (&&) (&ω&) (=^･ω･^=) &lt;3', '咪～ (&&) (&ω&) (=^･ω･^=) &lt;3'),
])
async def test_meme_markup_is_cleaned_or_normalized_before_selection(plugin, text, expected):
    ev = event('qq:GroupMessage:g')
    ev.set_extra('anisignal_cat_chat', True)
    response = SimpleNamespace(completion_text=text)
    await plugin.suppress_cat_interjection(ev, response)
    assert response.completion_text == expected
    if not expected:
        assert ev.get_extra('stopped')


@pytest.mark.parametrize('marker', [
    '&annoyed&', '&&annoyed&', '&annoyed&&', '&& annoyed &&',
    r'\&annoyed\&', '&amp;annoyed&amp;', '＆annoyed＆', '&annoyed', '&unknown_tag&',
    '&&annoyed&&&&happy&&', '&annoyed&&sad&', '&&&annoyed&&&',
])
async def test_final_cleanup_removes_malformed_emotion_markers_without_losing_cat_rhythm(plugin, marker):
    components = sys.modules['astrbot.api.message_components']
    image = SimpleNamespace(type='Image', file='cat.gif')
    text = '喵……，哈？  (=^･ω･^=)\n咪～'
    result = SimpleNamespace(is_llm_result=lambda: True, chain=[components.Plain(text + marker), image])
    ev = event('qq:GroupMessage:g')
    ev.set_extra('anisignal_cat_chat', True)
    ev.get_result = lambda: result
    await plugin.strip_residual_emotion_markup(ev)
    assert result.chain[0].text == text and result.chain[1] is image


async def test_final_cleanup_removes_invalid_meme_block_and_keeps_image(plugin):
    components = sys.modules['astrbot.api.message_components']
    image = SimpleNamespace(type='Image', file='cat.gif')
    result = SimpleNamespace(is_llm_result=lambda: True,
        chain=[components.Plain('哈。嘶？||meme_start||，||meme_end||'), image])
    ev = event('qq:GroupMessage:g')
    ev.set_extra('anisignal_cat_chat', True)
    ev.get_result = lambda: result
    await plugin.strip_residual_emotion_markup(ev)
    assert result.chain[0].text == '哈。嘶？' and result.chain[1] is image

    result.chain = [components.Plain('||meme_start||，||meme_end||')]
    await plugin.strip_residual_emotion_markup(ev)
    assert not result.chain and ev.get_extra('stopped')


async def test_cat_reply_removes_emoji_and_native_faces_but_keeps_cat_images(plugin):
    components = sys.modules['astrbot.api.message_components']
    image = SimpleNamespace(type='Image', file='cat.gif')
    chain = [components.Plain('咪😺～  喵？！\n(=^･ω･^=) ~ ……❤️🐈‍⬛👍🏽1️⃣🇨🇳 &&happy&&'),
        components.Face(), image]
    result = SimpleNamespace(is_llm_result=lambda: True, chain=chain)
    ev = event('qq:GroupMessage:g')
    ev.set_extra('anisignal_cat_chat', True)
    ev.get_result = lambda: result
    await plugin.strip_residual_emotion_markup(ev)
    assert len(result.chain) == 2 and result.chain[1] is image
    assert result.chain[0].text == '咪～  喵？！\n(=^･ω･^=) ~ ……'


async def test_emoji_only_reply_is_not_sent_as_an_empty_message(plugin):
    components = sys.modules['astrbot.api.message_components']
    result = SimpleNamespace(is_llm_result=lambda: True,
        chain=[components.Plain('😺'), components.Face()])
    ev = event('qq:GroupMessage:g')
    ev.set_extra('anisignal_cat_chat', True)
    ev.get_result = lambda: result
    await plugin.strip_residual_emotion_markup(ev)
    assert not result.chain and ev.get_extra('stopped')


async def test_cat_broadcast_does_not_teach_human_speech(plugin):
    manager = plugin.context.conversation_manager
    manager.get_conversation.return_value = SimpleNamespace(persona_id='cat')
    await plugin.send_delivery(delivery())
    manager.add_message_pair.assert_not_called()


async def test_cat_interjection_quiet_hours_cooldown_and_unanswered(plugin, monkeypatch):
    from datetime import datetime, timezone, timedelta
    umo = 'qq:GroupMessage:g'
    clock = SimpleNamespace(now=lambda tz: datetime(2026, 9, 21, 15, tzinfo=tz))
    monkeypatch.setitem(plugin.add_instructions.__func__.__globals__, 'datetime', clock)
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id='cat'), system_prompt='猫语')
    ev = event(umo)
    ev.is_at_or_wake_command = False
    await plugin.add_instructions(ev, req)
    assert ev.get_extra('anisignal_cat_interject') and not ev.get_extra('stopped')

    plugin.last_cat_reply[umo] = clock.now(timezone(timedelta(hours=8))).timestamp() - 60
    await plugin.add_instructions(ev, req)
    assert ev.get_extra('stopped')
    plugin.last_cat_reply.clear()
    plugin.cat_interjection_pending.add(umo)
    ev.set_extra('stopped', False)
    await plugin.add_instructions(ev, req)
    assert ev.get_extra('stopped')

    plugin.cat_interjection_pending.clear()
    clock.now = lambda tz: datetime(2026, 9, 21, 23, tzinfo=tz)
    ev.set_extra('stopped', False)
    await plugin.add_instructions(ev, req)
    assert ev.get_extra('stopped')
    ev.is_at_or_wake_command = True
    ev.set_extra('stopped', False)
    await plugin.add_instructions(ev, req)
    assert not ev.get_extra('stopped')  # 夜间主动冒泡关闭，点名仍回复。


async def test_cat_can_decline_to_interject(plugin):
    ev = event('qq:GroupMessage:g')
    ev.set_extra('anisignal_cat_interject', True)
    response = SimpleNamespace(completion_text='[NO_REPLY]')
    await plugin.suppress_cat_interjection(ev, response)
    assert ev.get_extra('stopped') and response.completion_text == ''


@pytest.mark.parametrize('text,expected', [
    ('今天看到一只可爱的猫娘', True),
    ('给你看看柴郡的新表情', True),
    ('这只哈基米在做什么', True),
    ('来给你挠下巴', True),
    ('I like NEKO stickers', True),
    ('路过放一只🐱', True),
    ('今天更新了游戏', False),
])
async def test_cat_topic_wakes_once_without_losing_message(plugin, text, expected):
    umo = 'qq:GroupMessage:g'
    schema = Path(__file__).parents[2] / 'integrations/astrbot_plugin_anisignal/_conf_schema.json'
    plugin.config['cat_reply_keywords'] = json.loads(schema.read_text(encoding='utf-8'))['cat_reply_keywords']['default']
    plugin.context.get_config = lambda **kwargs: {
        'provider_ltm_settings': {'active_reply': {'enable': True, 'whitelist': [umo]}}}
    ev = event(umo)
    ev.is_at_or_wake_command = False
    ev.get_message_str = lambda: text
    ev.get_group_id = lambda: 'g'
    plugin.cat_interjection_pending.add(umo)
    await plugin.select_conversation_provider(ev)
    assert ev.is_at_or_wake_command is expected
    assert ev.get_message_str() == text
    plugin.context.send_message.assert_not_called()  # 只唤醒，由原有管线发送一次。
    if expected:
        req = SimpleNamespace(conversation=SimpleNamespace(persona_id='cat'), system_prompt='猫语')
        await plugin.add_instructions(ev, req)
        assert not ev.get_extra('stopped') and umo not in plugin.cat_interjection_pending
        assert not ev.get_extra('anisignal_cat_interject')


@pytest.mark.parametrize('enabled,whitelist,keywords,expected', [
    (True, ['other'], ['猫'], False),
    (False, [], ['猫'], False),
    (True, [], [], False),
    (True, [], [' '], False),
    (True, ['g'], ['猫'], True),
])
async def test_cat_topic_respects_group_settings(plugin, enabled, whitelist, keywords, expected):
    plugin.config['cat_reply_keywords'] = keywords
    plugin.context.get_config = lambda **kwargs: {
        'provider_ltm_settings': {'active_reply': {'enable': enabled, 'whitelist': whitelist}}}
    ev = event('qq:GroupMessage:g')
    ev.is_at_or_wake_command = False
    ev.get_message_str = lambda: '好可爱的猫'
    ev.get_group_id = lambda: 'g'
    await plugin.select_conversation_provider(ev)
    assert ev.is_at_or_wake_command is expected


async def test_persona_intro_rejects_sales_and_long_monologues(plugin):
    plugin._resolve_persona = AsyncMock(return_value=('model', '明石'))
    plugin.context.llm_generate = AsyncMock()
    row = delivery()
    for bad in ['现在下单打九折喵。', '（摇了摇尾巴）喵。', '这是一大段没有必要的开场白' * 4, '下个月9月28日上线喵']:
        plugin.context.llm_generate.return_value = SimpleNamespace(completion_text=bad)
        assert await type(plugin).render_delivery(plugin, row) == row['body']
    plugin.context.llm_generate.return_value = SimpleNamespace(completion_text='钱包先装睡吧喵。')
    assert await type(plugin).render_delivery(plugin, row) == '钱包先装睡吧喵。\n\n原始资料'


async def test_expand_uses_sent_delivery_and_real_session_identity(plugin):
    umo = 'qq:GroupMessage:real'
    plugin.targets = {umo: {'enabled': True, 'tools': ['news']}}
    plugin.last_briefs[umo] = {'delivery_id': 12, 'brief': {'items': []}}
    plugin.api.return_value = {'text': '原来的第二条'}
    result = await plugin.detail(event(umo), 2)
    assert result['text'] == '原来的第二条'
    assert plugin.api.call_args.args == ('/detail', {'umo': umo, 'actor_id': 'user', 'number': 2, 'delivery_id': 12})
    plugin.targets[umo]['enabled'] = False
    assert 'error' in await plugin.detail(event(umo), 2)


async def test_delivery_uses_at_most_one_image(plugin):
    row = {**delivery(), 'images': ['first', 'second', 'third']}
    await plugin.send_delivery(row)
    assert plugin.context.send_message.call_args.args[1].image_url == 'first'
