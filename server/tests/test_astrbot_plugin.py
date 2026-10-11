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
        def __init__(self):
            self.images = []

        def message(self, text):
            self.text = text
            self.image_url = None
            return self

        def url_image(self, url):
            self.image_url = url
            self.images.append(url)
            return self

        def file_image(self, path):
            self.image_url = path
            self.images.append(path)
            return self

    module('aiohttp', ClientError=ConnectionError)
    module('astrbot')
    module('astrbot.api', llm_tool=decorator,
        logger=SimpleNamespace(warning=lambda *a: None, info=lambda *a: None))
    module('astrbot.api.event', AstrMessageEvent=object,
        filter=SimpleNamespace(command=decorator, regex=decorator, on_llm_request=decorator, on_llm_response=decorator, event_message_type=decorator,
            on_decorating_result=decorator, after_message_sent=decorator,
            EventMessageType=SimpleNamespace(ALL='all')))
    module('astrbot.api.star', Context=object, Star=object, register=decorator)
    module('astrbot.api.message_components', Face=type('Face', (), {}),
        At=type('At', (), {'__init__': lambda self, qq=None: setattr(self, 'qq', qq)}),
        Reply=type('Reply', (), {'__init__': lambda self, sender_id=None: setattr(self, 'sender_id', sender_id)}),
        Plain=type('Plain', (), {'__init__': lambda self, text=None: setattr(self, 'text', text or '')}))
    module('astrbot.core')
    module('astrbot.core.provider')
    module('astrbot.core.provider.entities', ProviderRequest=object)
    module('astrbot.core.message')
    module('astrbot.core.message.message_event_result', MessageChain=Chain)
    path = Path(__file__).parents[2] / 'integrations/astrbot_plugin_anisignal/main.py'
    spec = importlib.util.spec_from_file_location('plugin_under_test', path,
        submodule_search_locations=[str(path.parent)])
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, mod)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.random, 'random', lambda: 0.0)
    obj = mod.AniSignalPlugin.__new__(mod.AniSignalPlugin)
    obj.api = AsyncMock(return_value={'ok':True})
    obj.render_delivery = AsyncMock(return_value='人格播报\n原始资料')
    obj.pending_ack = None
    obj.config = {}
    obj.targets = {}
    obj.last_activity = {}
    obj.last_briefs = {}
    obj.last_cat_reply = {}
    obj.last_cat_topic_trigger = {}
    obj.cat_interjection_pending = set()
    obj.cat_followups = {}
    obj.cat_vision_cache = {}
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
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id='cat'), system_prompt='猫语',
        func_tool=object())
    await plugin.add_instructions(event('qq:GroupMessage:g'), req)
    assert req.system_prompt == '猫语'
    assert req.func_tool is None
    plugin.api.assert_not_called()


async def test_catgirl_keeps_tools_news_context_and_reply_cleanup(plugin):
    class Tools(list):
        remove_tool = list.remove

    umo = 'qq:GroupMessage:g'
    plugin.targets[umo] = {'enabled': True, 'tools': ['news']}
    plugin.api.return_value = {'recent_notifications': [{'text': '昨天已发的公告'}]}
    original_tools = Tools(['web_search', 'anisignal_query', 'anisignal_expand',
        'anisignal_subscription', 'anisignal_reminder'])
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id='catgirl'),
        system_prompt='软糯猫娘', func_tool=original_tools)
    ev = event(umo)
    await plugin.add_instructions(ev, req)
    assert req.func_tool == original_tools[:-1]  # 无提醒权限，其余工具仍可用。
    assert 'anisignal_reminder' in original_tools
    assert 'anisignal_query' in req.system_prompt and '昨天已发的公告' in req.system_prompt
    assert ev.get_extra('anisignal_cat_chat') and ev.get_extra('anisignal_model_started')
    plugin.api.assert_awaited_once_with('/context', {'umo': umo, 'actor_id': 'user'})

    response = SimpleNamespace(completion_text='还要摸摸头 &happy&')
    await plugin.suppress_cat_interjection(ev, response)
    assert response.completion_text == '还要摸摸头 &&happy&&'
    Plain = sys.modules['astrbot.api.message_components'].Plain
    result = SimpleNamespace(is_llm_result=lambda: True, chain=[Plain(response.completion_text)])
    ev.get_result = lambda: result
    await plugin.strip_residual_emotion_markup(ev)
    assert result.chain[0].text == '还要摸摸头'
    assert umo not in plugin.last_cat_reply  # 整理回复还不能当成已发送。
    ev._has_send_oper = True
    await plugin.log_cat_reply_time(ev)
    assert umo in plugin.last_cat_reply and ev.get_extra('anisignal_model_finished')


async def test_catgirl_chat_filters_material_pairs_without_losing_conversation_or_tools(plugin):
    history = [
        {'role': 'user', 'content': '我喜欢巧克力'},
        {'role': 'assistant', 'content': '记着呢。'},
        {'role': 'user', 'content': '（AniSignal 向本会话推送了一条每日早报）'},
        {'role': 'assistant', 'content': '长篇资讯正文'},
        {'role': 'user', 'content': '（处理了情报查询）'},
        {'role': 'assistant', 'content': '查询资料清单'},
        {'role': 'user', 'content': '刚才第二条什么时候结束？'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'lookup'}]},
        {'role': 'tool', 'tool_call_id': 'lookup', 'content': '10月10日截止'},
        {'role': 'assistant', 'content': '10月10日截止。'},
    ]
    original = json.loads(json.dumps(history))
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id='catgirl'),
        system_prompt='猫娘', contexts=history, func_tool=None)
    await plugin.add_instructions(event('qq:FriendMessage:self'), req)
    assert req.contexts == history[:2] + history[4:]
    assert history == original  # 不原地改写调用方传入的上下文。


async def test_catgirl_product_query_keeps_facts_for_followup(plugin):
    manager = plugin.context.conversation_manager
    manager.get_conversation.return_value = SimpleNamespace(persona_id='catgirl')
    ev = event('qq:FriendMessage:self')
    body = '键盘：已出现3位独立用户到货证据。'
    replies = [reply async for reply in plugin.present_with_persona(ev, body, history_label='商品查询')]
    assert len(replies) == 1
    _, user_msg, assistant_msg = manager.add_message_pair.await_args.args
    assert user_msg['content'] == '（处理了商品查询）' and assistant_msg['content'] == body


async def test_catgirl_notification_index_keeps_detail_reference_without_broadcast_body(plugin):
    umo = 'qq:FriendMessage:self'
    plugin.targets[umo] = {'enabled': True, 'tools': ['news']}
    plugin.api.return_value = {'recent_notifications': [{
        'id': 73, 'kind': 'updates', 'sent_at': '2026-10-09T15:00:00+08:00',
        'text': '完整播报正文不应反复注入' * 200,
        'brief': {'items': [{'title': '活动截止提醒', 'summary': '10月10日截止', 'community': 'wuwa'}]},
    }]}
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id='catgirl'),
        system_prompt='猫娘', func_tool=None)
    await plugin.add_instructions(event(umo), req)
    assert '完整播报正文' not in req.system_prompt
    assert '"id": 73' in req.system_prompt and '10月10日截止' in req.system_prompt
    assert 'anisignal_expand' in req.system_prompt
    assert 'sent_at只是推送时间' in req.system_prompt


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
        assert not ev.get_extra('stopped')  # 空文本不能打断同轮的工具调用。


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
    assert not result.chain and not ev.get_extra('stopped')
    assert not ev.get_extra('anisignal_cat_result_ready')


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
    assert not result.chain and not ev.get_extra('stopped')
    assert not ev.get_extra('anisignal_cat_result_ready')


@pytest.mark.parametrize('placeholder', ['', '||meme_start||，||meme_end||', '😺'])
async def test_empty_llm_stage_resumes_tool_execution_and_sends_only_final_answer(plugin, placeholder):
    """框架先 yield LLM 结果，再执行工具；装饰阶段 stop 会让外层提前关闭生成器。"""
    components = sys.modules['astrbot.api.message_components']
    ev = event('napcat_nNjs:FriendMessage:test')
    ev._has_send_oper = False
    ev.set_extra('anisignal_cat_chat', True)
    # 确认空阶段会撤销旧 ready 标记，不能误记发送成功或开启续聊。
    ev.set_extra('anisignal_cat_result_ready', True)
    search = AsyncMock(return_value='已核对的查询结果')
    send = AsyncMock()

    async def agent_turn():
        response = SimpleNamespace(completion_text=placeholder)
        await plugin.suppress_cat_interjection(ev, response)
        chain = [components.Plain(response.completion_text)] if response.completion_text else []
        yield SimpleNamespace(is_llm_result=lambda: True, chain=chain)
        answer = await search()
        yield SimpleNamespace(is_llm_result=lambda: True, chain=[components.Plain(answer)])

    async for result in agent_turn():
        ev.get_result = lambda: result
        await plugin.strip_residual_emotion_markup(ev)
        if ev.get_extra('stopped'):
            break  # 与 AstrBot Scheduler 对 stop_event 的行为一致。
        if not result.chain:
            assert not ev.get_extra('anisignal_cat_result_ready')
            assert not plugin.last_cat_reply
            continue  # RespondStage 原生跳过空结果，不发送、不触发 after_message_sent。
        await send(result.chain[0].text)
        ev._has_send_oper = True
        await plugin.log_cat_reply_time(ev)

    search.assert_awaited_once()
    send.assert_awaited_once_with('已核对的查询结果')
    assert not ev.get_extra('stopped')
    assert ev.unified_msg_origin in plugin.last_cat_reply


@pytest.mark.parametrize('persona_id,recorded', [('cat', False), ('catgirl', False), ('akashi', True)])
async def test_cat_broadcast_history_follows_persona(plugin, persona_id, recorded):
    manager = plugin.context.conversation_manager
    manager.get_conversation.return_value = SimpleNamespace(persona_id=persona_id)
    await plugin.send_delivery(delivery())
    assert manager.add_message_pair.await_count == int(recorded)


@pytest.mark.parametrize('persona_id', ['cat', 'catgirl'])
async def test_cat_interjection_quiet_hours_cooldown_and_unanswered(plugin, monkeypatch, persona_id):
    from datetime import datetime, timezone, timedelta
    umo = 'qq:GroupMessage:g'
    clock = SimpleNamespace(now=lambda tz: datetime(2026, 9, 21, 15, tzinfo=tz))
    monkeypatch.setitem(plugin.add_instructions.__func__.__globals__, 'datetime', clock)
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id=persona_id),
        system_prompt='猫系人格', func_tool=None)
    ev = event(umo)
    ev.is_at_or_wake_command = False
    await plugin.add_instructions(ev, req)
    assert ev.get_extra('anisignal_cat_interject') and not ev.get_extra('stopped')
    assert '[NO_REPLY]' in req.system_prompt
    if persona_id == 'catgirl':
        assert '简短中文' in req.system_prompt and '猫叫' not in req.system_prompt

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


@pytest.mark.parametrize('persona_id', ['cat', 'catgirl'])
async def test_cat_vision_previews_animation_and_preserves_speakers(plugin, tmp_path, persona_id):
    import base64
    import io
    from PIL import Image

    frames = [Image.new('RGB', (80, 60), color) for color in ('red', 'green', 'blue')]
    path = tmp_path / 'cat.gif'
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    original = path.read_bytes()
    uri = 'data:image/gif;base64,' + base64.b64encode(original).decode()
    history = [{'role': 'user', 'content': [
        {'type': 'text', 'text': '[user_id=123] 这是甲发的动图'},
        {'type': 'image_url', 'image_url': {'url': uri, 'detail': 'low'}},
    ]}]
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id=persona_id),
        system_prompt='猫系人格', contexts=history, image_urls=[str(path)], func_tool=None)
    ev = event('qq:FriendMessage:self')
    await plugin.add_instructions(ev, req)
    preview = req.contexts[0]['content'][1]['image_url']
    assert preview['url'].startswith('data:image/jpeg;base64,')
    assert req.image_urls == [preview['url']]  # 当前附件与历史附件使用同一预览。
    assert preview['detail'] == 'low'
    assert req.contexts[0]['content'][0] == history[0]['content'][0]
    assert history[0]['content'][1]['image_url']['url'] == uri
    assert path.read_bytes() == original
    with Image.open(io.BytesIO(base64.b64decode(preview['url'].split(',', 1)[1]))) as image:
        assert image.getpixel((192, 192))[0] > 200
        assert image.getpixel((576, 192))[1] > 100
        assert image.getpixel((960, 192))[2] > 200


async def test_cat_bounds_history_and_keeps_static_images(plugin, tmp_path):
    from PIL import Image

    path = tmp_path / 'static.png'
    Image.new('RGB', (8, 8), 'white').save(path)
    history = [{'role': 'user', 'content': f'[user_id={i}] 喵'} for i in range(30)]
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id='cat'),
        system_prompt='猫语', contexts=history, image_urls=[str(path)])
    await plugin.add_instructions(event('qq:FriendMessage:self'), req)
    assert req.contexts == history[-24:]
    assert len(history) == 30
    assert req.image_urls == [str(path)]


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


@pytest.mark.parametrize('draw,expected', [(0.29, True), (0.30, False), (0.99, False)])
async def test_cat_topic_probability_and_no_random_fallback(plugin, monkeypatch, draw, expected):
    plugin.config.update(cat_reply_keywords=['猫'], cat_topic_reply_probability=0.3)
    plugin.context.get_config = lambda **kwargs: {
        'provider_ltm_settings': {'active_reply': {'enable': True, 'whitelist': []}}}
    monkeypatch.setattr(plugin.select_conversation_provider.__func__.__globals__['random'], 'random', lambda: draw)
    ev = event('qq:GroupMessage:g')
    ev.is_at_or_wake_command = False
    ev.get_message_str = lambda: '这只猫好可爱'
    ev.get_group_id = lambda: 'g'
    await plugin.select_conversation_provider(ev)
    assert ev.is_at_or_wake_command is expected
    if not expected:
        req = SimpleNamespace(conversation=SimpleNamespace(persona_id='cat'), system_prompt='猫语')
        await plugin.add_instructions(ev, req)
        assert ev.get_extra('stopped')  # 不能被另一条随机回复链路重新触发。


async def test_cat_topic_cooldown_reserves_slot_per_group_and_keeps_direct_calls(plugin, monkeypatch):
    plugin.config.update(cat_reply_keywords=['猫'], cat_topic_reply_probability=1.0,
                         cat_topic_cooldown_seconds=90)
    plugin.context.get_config = lambda **kwargs: {
        'provider_ltm_settings': {'active_reply': {'enable': True, 'whitelist': []}}}
    clock = [10000.0]
    monkeypatch.setattr(plugin.select_conversation_provider.__func__.__globals__['time'], 'time', lambda: clock[0])

    async def trigger(group, direct=False):
        ev = event(f'qq:GroupMessage:{group}')
        ev.is_at_or_wake_command = direct
        ev.get_message_str = lambda: '这只猫'
        ev.get_group_id = lambda: group
        await plugin.select_conversation_provider(ev)
        return ev

    assert (await trigger('one')).is_at_or_wake_command
    assert not (await trigger('one')).is_at_or_wake_command  # 第一条还在生成也不能连着触发。
    assert (await trigger('two')).is_at_or_wake_command
    direct = await trigger('one', direct=True)
    assert direct.is_at_or_wake_command and not direct.get_extra('anisignal_cat_topic_skipped')
    clock[0] += 89
    assert not (await trigger('one')).is_at_or_wake_command
    clock[0] += 1
    assert (await trigger('one')).is_at_or_wake_command
    clock[0] += 100
    plugin.last_cat_reply['qq:GroupMessage:one'] = clock[0] - 20
    assert not (await trigger('one')).is_at_or_wake_command  # 点名回复之后也留出安静间隔。


async def test_persona_intro_rejects_sales_and_long_monologues(plugin):
    plugin._resolve_persona = AsyncMock(return_value=('model', '明石', 'akashi'))
    plugin.context.llm_generate = AsyncMock()
    row = delivery()
    for bad in ['现在下单打九折喵。', '（摇了摇尾巴）喵。', '这是一大段没有必要的开场白' * 4, '下个月9月28日上线喵']:
        plugin.context.llm_generate.return_value = SimpleNamespace(completion_text=bad)
        assert await type(plugin).render_delivery(plugin, row) == row['body']
    plugin.context.llm_generate.return_value = SimpleNamespace(completion_text='钱包先装睡吧喵。')
    assert await type(plugin).render_delivery(plugin, row) == '钱包先装睡吧喵。\n\n原始资料'


@pytest.mark.parametrize('kind', ['updates', 'critical', 'calendar', 'reminder'])
async def test_scheduled_notification_has_no_persona_opening_or_model_call(plugin, kind):
    plugin._resolve_persona = AsyncMock()
    plugin.context.llm_generate = AsyncMock()
    row = {**delivery(), 'kind': kind, 'body': '09/30 10:00 维护开始\n原公告链接'}
    assert await type(plugin).render_delivery(plugin, row) == row['body']
    plugin._resolve_persona.assert_not_awaited()
    plugin.context.llm_generate.assert_not_awaited()


@pytest.mark.parametrize('persona_id,prompt', [
    ('cat', '自定义纯猫语人格'), ('', '你扮演一只在群里生活的小猫。只通过猫叫回应。'),
])
async def test_pure_cat_broadcast_omits_empty_cat_sound_without_changing_chat(plugin, persona_id, prompt):
    plugin._resolve_persona = AsyncMock(return_value=('model', prompt, persona_id))
    plugin.context.llm_generate = AsyncMock()
    row = {**delivery(), 'kind': 'daily'}
    assert await type(plugin).render_delivery(plugin, row) == row['body']
    plugin.context.llm_generate.assert_not_awaited()


@pytest.mark.parametrize('reaction', [
    '', '喵喵！', '啊！', '哇！', '好期待喵！', '值得期待喵！', '敬请期待！', '猫猫整理好了喵！', '你们觉得怎么样呀？',
    '要不要猫猫帮你看看呀？', '有什么需要帮助的吗？', '喜欢就回复猫猫呀',
    '</p><p>', '&lt;/p&gt;&lt;p&gt;', '[NO_REPLY]', '||meme_start||happy||meme_end||',
])
async def test_notification_drops_empty_generic_and_reply_seeking_reactions(plugin, reaction):
    plugin._resolve_persona = AsyncMock(return_value=('model', '软糯小猫娘', 'catgirl'))
    plugin.context.llm_generate = AsyncMock(return_value=SimpleNamespace(completion_text=reaction))
    row = delivery()
    assert await type(plugin).render_delivery(plugin, row) == row['body']


async def test_notification_reaction_uses_snapshot_and_keeps_verified_body_unchanged(plugin):
    plugin._resolve_persona = AsyncMock(return_value=('model', '软糯小猫娘', 'catgirl'))
    plugin.context.llm_generate = AsyncMock(return_value=SimpleNamespace(
        completion_text='</p><p>尾巴上的灯还会跟着节奏亮呀。&happy&</p>'))
    body = '灯会新预告\n09/30 20:00 公布完整演出。\n想细看，发 /展开 1'
    row = {**delivery(), 'body': body, 'kind': 'daily', 'brief': {'items': [{
        'title': '灯会新预告', 'summary': '公开演出片段。', 'sources': [{
            'title': '灯会演出预告', 'source': '官方账号',
            'text': '<p>角色尾巴上的灯会随着音乐节奏点亮。</p>' + '背景资料' * 200,
        }],
    }]}}
    result = await type(plugin).render_delivery(plugin, row)
    assert result == '尾巴上的灯还会跟着节奏亮呀。\n\n' + body
    kwargs = plugin.context.llm_generate.await_args.kwargs
    assert kwargs['system_prompt'].startswith('软糯小猫娘')
    snapshot = json.loads(kwargs['prompt'].split('对应来源快照（仅供理解）：\n', 1)[1])
    excerpt = snapshot[0]['sources'][0]['excerpt']
    assert '尾巴上的灯会随着音乐节奏点亮' in excerpt and len(excerpt) == 350
    assert '<p>' not in excerpt
    assert row['body'] == body and row['brief']['items'][0]['sources'][0]['text'].startswith('<p>')


@pytest.mark.parametrize('count', [1, 3])
async def test_daily_card_does_not_repeat_expansion_hint_in_caption(plugin, monkeypatch, count):
    card = importlib.import_module('plugin_under_test.card')
    monkeypatch.setattr(card, 'render_brief_card', lambda brief, path, font: path)
    brief = {'mode': 'daily', 'items': [{'title': '动态', 'summary': '具体变化'}] * count}
    sent, text = await plugin.send_brief_message('qq:FriendMessage:self', '短反应\n\n正文', brief, body='正文')
    assert sent and text == '短反应'
    chain = plugin.context.send_message.await_args.args[1]
    assert chain.image_url.endswith('brief.png')
    plugin.context.send_message.assert_awaited_once()


async def test_daily_card_can_be_sent_without_a_forced_caption(plugin, monkeypatch):
    card = importlib.import_module('plugin_under_test.card')
    monkeypatch.setattr(card, 'render_brief_card', lambda brief, path, font: path)
    brief = {'mode': 'daily', 'items': [{'title': '动态', 'summary': '具体变化'}]}
    sent, text = await plugin.send_brief_message('qq:FriendMessage:self', '正文', brief, body='正文')
    assert sent and text == ''
    chain = plugin.context.send_message.await_args.args[1]
    assert chain.image_url.endswith('brief.png') and not getattr(chain, 'text', '')
    plugin.context.send_message.assert_awaited_once()


async def test_manual_preview_of_update_share_does_not_add_an_intro(plugin):
    plugin._resolve_persona = AsyncMock()
    row = {**delivery(), 'kind': 'manual', 'brief': {'mode': 'updates'}, 'body': '鸣潮先约电台深夜频道更新啦。'}
    assert await type(plugin).render_delivery(plugin, row) == row['body']
    plugin._resolve_persona.assert_not_awaited()


async def test_card_send_error_does_not_send_a_second_text_message(plugin, monkeypatch):
    card = importlib.import_module('plugin_under_test.card')
    monkeypatch.setattr(card, 'render_brief_card', lambda brief, path, font: path)
    plugin.context.send_message.side_effect = TimeoutError()
    row = {**delivery(), 'brief': {'mode': 'daily', 'items': [{'title': '动态'}]}}
    await plugin.send_delivery(row)
    plugin.context.send_message.assert_awaited_once()
    assert plugin.api.call_args.args[1]['status'] == 'unknown'


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


@pytest.mark.parametrize('published,expected', [
    ('2026-10-09T06:00:00', '发布：10/09 14:00'),
    ('2026-10-09T14:00:00+08:00', '发布：10/09 14:00'),
    (None, ''),
])
async def test_local_expansion_uses_publication_time_without_collection_fallback(plugin, published, expected):
    umo = 'qq:GroupMessage:real'
    plugin.targets = {umo: {'enabled': True, 'tools': ['news']}}
    plugin.last_briefs[umo] = {'brief': {'items': [{'title': '原始动态', 'sources': [
        {'title': '原始动态', 'source': '官方账号', 'published_at': published,
         'collected_at': '2026-10-09T07:40:00'},
    ]}]}}
    text = (await plugin.detail(event(umo)))['text']
    assert '15:40' not in text and '22:00' not in text
    if expected:
        assert expected in text
    else:
        assert '发布：' not in text
    plugin.api.assert_not_awaited()


async def test_delivery_uses_at_most_one_image(plugin):
    row = {**delivery(), 'images': ['first', 'second', 'third']}
    await plugin.send_delivery(row)
    assert plugin.context.send_message.call_args.args[1].image_url == 'first'
    assert plugin.context.send_message.call_args.args[1].images == ['first']


@pytest.mark.parametrize('max_images', [1, 3, 9])
async def test_delivery_honors_configured_image_limit(plugin, max_images):
    row = {**delivery(), 'images': [f'image-{i}' for i in range(10)], 'max_images': max_images}
    await plugin.send_delivery(row)
    assert plugin.context.send_message.call_args.args[1].images == row['images'][:max_images]
    plugin.context.send_message.assert_awaited_once()


async def test_daily_card_counts_toward_image_limit(plugin, monkeypatch):
    card = importlib.import_module('plugin_under_test.card')
    monkeypatch.setattr(card, 'render_brief_card', lambda brief, path, font: path)
    row = {**delivery(), 'brief': {'mode': 'daily', 'items': [{'title': '今日消息'}]},
           'images': ['first', 'second', 'third'], 'max_images': 3}
    await plugin.send_delivery(row)
    images = plugin.context.send_message.call_args.args[1].images
    assert len(images) == 3 and images[0].endswith('brief.png')
    assert images[1:] == ['first', 'second']
    plugin.context.send_message.assert_awaited_once()


@pytest.fixture
def followup_chat(plugin, monkeypatch):
    clock = [10000.0]
    monkeypatch.setattr(plugin.select_conversation_provider.__func__.__globals__['time'], 'time', lambda: clock[0])
    plugin.config.update(cat_followup_platforms=['napcat_nNjs'])
    active = {'enable': True, 'whitelist': ['napcat_nNjs:GroupMessage:g']}
    plugin.context.get_config = lambda **kwargs: {'provider_ltm_settings': {'active_reply': active}}
    plugin.context.conversation_manager.get_conversation.return_value = SimpleNamespace(persona_id='catgirl')
    components = sys.modules['astrbot.api.message_components']

    async def incoming(text='那你喜欢哪个呀', sender='user', group='g', platform='napcat_nNjs',
                       at=None, quote=None, direct=False, private=False):
        kind = 'FriendMessage' if private else 'GroupMessage'
        ev = event(f'{platform}:{kind}:{group}')
        chain = ([components.At(at)] if at is not None else [])
        chain += [components.Reply(quote)] if quote is not None else []
        chain.append(components.Plain(text))
        ev.get_sender_id = lambda: sender
        ev.get_self_id = lambda: 'bot'
        ev.get_group_id = lambda: '' if private else group
        ev.get_messages = lambda: chain
        ev.get_message_str = lambda: text
        ev.is_at_or_wake_command = direct or at == 'bot' or quote == 'bot' or private
        ev._has_send_oper = False
        await plugin.select_conversation_provider(ev)
        return ev

    async def reply(ev, success=True, prior_send=False, persona='catgirl', text='草莓的呀'):
        req = SimpleNamespace(conversation=SimpleNamespace(persona_id=persona), system_prompt='', func_tool=None)
        await plugin.add_instructions(ev, req)
        result = SimpleNamespace(is_llm_result=lambda: True, chain=[components.Plain(text)])
        ev.get_result = lambda: result
        ev._has_send_oper = prior_send
        await plugin.strip_residual_emotion_markup(ev)
        ev._has_send_oper = prior_send or success
        await plugin.log_cat_reply_time(ev)
        return req

    return SimpleNamespace(incoming=incoming, reply=reply, clock=clock, active=active)


@pytest.mark.parametrize('address', [{'at': 'bot'}, {'quote': 'bot'}])
async def test_cat_followup_opens_only_after_a_successful_direct_reply(plugin, followup_chat, address):
    chat = followup_chat
    direct = await chat.incoming(**address)
    assert not plugin.cat_followups
    await chat.reply(direct)
    umo = direct.unified_msg_origin
    assert plugin.cat_followups[umo] == {'sender': 'user', 'expires': 10090.0, 'remaining': 3}
    followup = await chat.incoming()
    assert followup.is_at_or_wake_command and followup.get_extra('anisignal_cat_followup')
    assert followup.get_message_str() == '那你喜欢哪个呀'
    req = await chat.reply(followup)
    assert '[NO_REPLY]' in req.system_prompt and '同一位群友' in req.system_prompt
    assert plugin.cat_followups[umo]['expires'] == 10090.0
    assert plugin.cat_followups[umo]['remaining'] == 2
    plugin.context.send_message.assert_not_called()  # 续聊仍交给原有聊天管线发送。


@pytest.mark.parametrize('success,prior_send', [(False, False), (False, True), (True, True)])
async def test_cat_followup_does_not_treat_failed_or_prior_sends_as_success(plugin, followup_chat, success, prior_send):
    direct = await followup_chat.incoming(at='bot')
    await followup_chat.reply(direct, success=success, prior_send=prior_send)
    assert not plugin.cat_followups and not plugin.last_cat_reply


async def test_cat_followup_never_opens_from_an_unaddressed_or_non_llm_reply(plugin, followup_chat):
    ev = await followup_chat.incoming(direct=True)  # 仅唤醒词不是 @/引用。
    await followup_chat.reply(ev)
    assert not plugin.cat_followups
    direct = await followup_chat.incoming(at='bot')
    direct.set_extra('anisignal_cat_chat', True)
    direct.set_extra('anisignal_catgirl_chat', True)
    direct.get_result = lambda: SimpleNamespace(is_llm_result=lambda: False, chain=['command'])
    await plugin.strip_residual_emotion_markup(direct)
    direct._has_send_oper = True
    await plugin.log_cat_reply_time(direct)
    assert not plugin.cat_followups


async def test_cat_followup_keeps_people_groups_and_platforms_separate(plugin, followup_chat):
    chat = followup_chat
    direct = await chat.incoming(at='bot')
    await chat.reply(direct)
    chat.active['whitelist'].append('napcat_nNjs:GroupMessage:other')
    for kwargs in [{'sender': 'other'}, {'group': 'other'}, {'platform': 'qq_official'}]:
        ev = await chat.incoming(**kwargs)
        assert not ev.is_at_or_wake_command and not ev.get_extra('anisignal_cat_followup')
    assert plugin.cat_followups[direct.unified_msg_origin]['remaining'] == 3
    private = await chat.incoming(private=True)
    assert private.is_at_or_wake_command and not private.get_extra('anisignal_cat_followup')


@pytest.mark.parametrize('active,platforms,persona', [
    ({'enable': False, 'whitelist': ['g']}, ['napcat_nNjs'], 'catgirl'),
    ({'enable': True, 'whitelist': []}, ['napcat_nNjs'], 'catgirl'),
    ({'enable': True, 'whitelist': ['other']}, ['napcat_nNjs'], 'catgirl'),
    ({'enable': True, 'whitelist': ['g']}, [], 'catgirl'),
    ({'enable': True, 'whitelist': ['g']}, ['napcat_nNjs'], 'cat'),
])
async def test_cat_followup_requires_enabled_platform_explicit_group_and_catgirl(plugin, followup_chat, active, platforms, persona):
    chat = followup_chat
    chat.active.clear()
    chat.active.update(active)
    plugin.config['cat_followup_platforms'] = platforms
    plugin.context.conversation_manager.get_conversation.return_value.persona_id = persona
    direct = await chat.incoming(at='bot')
    await chat.reply(direct, persona=persona)
    assert direct.is_at_or_wake_command and not plugin.cat_followups


async def test_cat_followup_expires_and_does_not_extend_itself(plugin, followup_chat):
    chat = followup_chat
    direct = await chat.incoming(at='bot')
    await chat.reply(direct)
    chat.clock[0] = 10089
    followup = await chat.incoming()
    await chat.reply(followup)
    assert plugin.cat_followups[direct.unified_msg_origin]['expires'] == 10090
    chat.clock[0] = 10090
    expired = await chat.incoming()
    assert not expired.is_at_or_wake_command and not plugin.cat_followups


async def test_cat_followup_reserves_at_most_three_turns_before_model_reply(plugin, followup_chat):
    chat = followup_chat
    await chat.reply(await chat.incoming(at='bot'))
    for _ in range(3):
        assert (await chat.incoming()).is_at_or_wake_command
    exhausted = await chat.incoming()
    assert not exhausted.is_at_or_wake_command and not plugin.cat_followups


@pytest.mark.parametrize('kwargs', [
    {'at': 'someone-else'}, {'quote': 'someone-else'}, {'quote': ''},
    {'text': '晚安啦！'}, {'text': '猫猫，先不聊了'}, {'text': '不用回了'},
])
async def test_cat_followup_closes_when_user_redirects_or_finishes(plugin, followup_chat, kwargs):
    chat = followup_chat
    await chat.reply(await chat.incoming(at='bot'))
    ev = await chat.incoming(**kwargs)
    assert not ev.is_at_or_wake_command and not plugin.cat_followups
    assert ev.get_extra('anisignal_cat_topic_skipped')
    assert not (await chat.incoming()).is_at_or_wake_command


async def test_cat_followup_does_not_suppress_normal_direct_goodbye(plugin, followup_chat):
    chat = followup_chat
    await chat.reply(await chat.incoming(at='bot'))
    goodbye = await chat.incoming(at='bot', text='晚安')
    assert goodbye.is_at_or_wake_command and not goodbye.get_extra('stopped')
    await chat.reply(goodbye, text='晚安呀')
    assert not plugin.cat_followups


@pytest.mark.parametrize('text', ['[NO_REPLY]', ' [NO_REPLY] &&neutral&& '])
async def test_cat_followup_model_can_close_window_without_leaking_control_text(plugin, followup_chat, text):
    chat = followup_chat
    await chat.reply(await chat.incoming(at='bot'))
    followup = await chat.incoming()
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id='catgirl'), system_prompt='', func_tool=None)
    await plugin.add_instructions(followup, req)
    response = SimpleNamespace(completion_text=text)
    await plugin.suppress_cat_interjection(followup, response)
    assert response.completion_text == '' and followup.get_extra('stopped')
    assert not plugin.cat_followups


async def test_cat_followup_stale_silence_cannot_close_a_new_direct_window(plugin, followup_chat):
    chat = followup_chat
    await chat.reply(await chat.incoming(at='bot'))
    old = await chat.incoming()
    await chat.reply(await chat.incoming(at='bot'))
    await plugin.suppress_cat_interjection(old, SimpleNamespace(completion_text='[NO_REPLY]'))
    assert plugin.cat_followups[old.unified_msg_origin]['remaining'] == 3


async def test_cat_followup_rechecks_changed_persona_before_trigger_and_request(plugin, followup_chat):
    chat = followup_chat
    await chat.reply(await chat.incoming(at='bot'))
    plugin.context.conversation_manager.get_conversation.return_value.persona_id = 'cat'
    changed = await chat.incoming()
    assert not changed.is_at_or_wake_command and not plugin.cat_followups
    plugin.context.conversation_manager.get_conversation.return_value.persona_id = 'catgirl'
    await chat.reply(await chat.incoming(at='bot'))
    changed = await chat.incoming()
    req = SimpleNamespace(conversation=SimpleNamespace(persona_id='cat'), system_prompt='', func_tool=None)
    await plugin.add_instructions(changed, req)
    assert changed.get_extra('stopped') and not plugin.cat_followups
