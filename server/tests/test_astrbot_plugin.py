"""验证发送结果不明时不重发，以及工具操作者始终取自真实事件。"""
import importlib.util
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml


@pytest.fixture(params=['integrations/astrbot_plugin_anisignal/main.py', 'main.py'],
                ids=['standalone', 'repository'])
def plugin(monkeypatch, request):
    class SavedConfig(dict):
        def save_config(self):
            self.saved = copy.deepcopy(dict(self))

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
            self.parts = []
            self.image_url = None

        def message(self, text):
            self.text = text
            self.parts.append(('text', text))
            return self

        def url_image(self, url):
            self.image_url = url
            self.images.append(url)
            self.parts.append(('image', url))
            return self

        def file_image(self, path):
            self.image_url = path
            self.images.append(path)
            self.parts.append(('image', path))
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
    path = Path(__file__).parents[2] / request.param
    spec = importlib.util.spec_from_file_location('plugin_under_test', path,
        submodule_search_locations=[str(path.parent)])
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, mod)
    spec.loader.exec_module(mod)
    obj = mod.AniSignalPlugin.__new__(mod.AniSignalPlugin)
    obj.api = AsyncMock(return_value={'ok':True})
    obj.render_delivery = AsyncMock(side_effect=lambda row: mod.render_notification(row, {}, '人格播报'))
    obj.pending_ack = None
    obj.config = SavedConfig()
    obj.configuration_lock = asyncio.Lock()
    obj.configuration_ready = True
    obj.configuration_dirty = False
    obj.targets = {}
    obj.last_activity = {}
    obj.last_briefs = {}
    obj.context = SimpleNamespace(get_platform_inst=lambda p: None, send_message=AsyncMock(return_value=True),
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value='cid-1'),
            new_conversation=AsyncMock(return_value='new-cid'),
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


async def test_existing_sessions_are_imported_once_and_saved_before_sync(plugin):
    row = {'name': '已有会话', 'umo': 'qq:GroupMessage:existing', 'enabled': True,
           'community': 'wuwa', 'communities': None, 'daily_time': '09:15', 'keywords': ['角色']}
    plugin.configuration_ready = False
    plugin.api.side_effect = [{'targets': [row]}, {'targets': [row]}, {'targets': [row]}]
    await plugin.synchronize_configuration()
    assert plugin.config.saved['configuration_imported'] is True
    imported = plugin.config.saved['targets'][0]
    assert imported['subscription'] == {'communities': ['wuwa'], 'keywords': ['角色'], 'channels': []}
    assert imported['notifications']['daily_time'] == '09:15'
    assert plugin.configuration_ready and plugin.targets[row['umo']]['enabled']
    await plugin.synchronize_configuration()
    assert [call.args[0] for call in plugin.api.await_args_list] == ['/configuration'] * 3
    assert len(plugin.api.await_args_list[0].args) == 1
    assert all(len(call.args) == 2 for call in plugin.api.await_args_list[1:])
    plugin.context.send_message.assert_not_awaited()


async def test_clearing_plugin_targets_does_not_import_old_sessions_again(plugin):
    plugin.config.update(configuration_imported=True, targets=[])
    plugin.api.return_value = {'targets': []}
    await plugin.synchronize_configuration()
    assert plugin.api.await_count == 1 and plugin.api.call_args.args[1]['targets'] == []
    assert plugin.targets == {}


async def test_chat_subscription_changes_are_persisted_back_to_plugin_settings(plugin):
    mod = sys.modules[plugin.__class__.__module__]
    umo = 'telegram:GroupMessage:real'
    plugin.config.update(configuration_imported=True, targets=[mod.to_plugin_target({'name': '群聊', 'umo': umo})])
    changed = {'changed': True, 'communities': ['hsr'], 'keywords': ['角色'],
               'daily_enabled': True, 'daily_time': '09:30', 'updates_enabled': False}
    plugin.api.return_value = changed
    assert await plugin.actor_api(event(umo), '/subscription', action='community', value='hsr') == changed
    saved = plugin.config.saved['targets'][0]
    assert saved['subscription']['communities'] == ['hsr']
    assert saved['notifications']['daily_time'] == '09:30'
    plugin.context.send_message.assert_not_awaited()


async def test_failed_local_save_is_retried_before_claiming_configuration_ready(plugin):
    row = {'name': '已有会话', 'umo': 'qq:GroupMessage:existing', 'enabled': True}
    plugin.configuration_ready = False
    plugin.api.side_effect = [{'targets': [row]}, {'targets': [row]}]
    original_save = plugin.config.save_config
    def fail():
        raise PermissionError('readonly')
    plugin.config.save_config = fail
    with pytest.raises(PermissionError):
        await plugin.synchronize_configuration()
    assert not plugin.configuration_ready and plugin.configuration_dirty
    assert plugin.api.await_count == 1
    plugin.config.save_config = original_save
    await plugin.synchronize_configuration()
    assert plugin.configuration_ready and not plugin.configuration_dirty


def test_new_install_defaults_are_portable_and_mask_the_access_key():
    path = Path(__file__).parents[2] / 'integrations/astrbot_plugin_anisignal/_conf_schema.json'
    schema = json.loads(path.read_text(encoding='utf-8'))
    assert schema['tools_key']['secret'] is True
    assert schema['base_url']['default'] == 'http://anisignal.xyz'
    assert not any(key.startswith('cat_') or key == 'legacy_cat_behavior' for key in schema)
    assert 'card_font_path' in schema
    session=schema['targets']['templates']['session']['items']
    assert not {'ai_prompt','ai_provider_id'}.intersection(session['interaction']['items'])
    assert session['presentation']['items']['show_details_hint']['default'] is False


def test_repository_install_manifests_match_standalone_plugin(plugin):
    root = Path(__file__).parents[2]
    standalone = root / 'integrations/astrbot_plugin_anisignal'
    for filename in ['metadata.yaml', '_conf_schema.json']:
        assert (root / filename).read_bytes() == (standalone / filename).read_bytes(), \
            f'修改插件设置或元数据后，需要同步根目录的 {filename}'
    metadata = yaml.safe_load((root / 'metadata.yaml').read_text(encoding='utf-8'))
    assert metadata['version'] == 'v' + sys.modules[plugin.__class__.__module__].VERSION
    assert metadata['repo'] == 'https://github.com/tianyin231/AniSignal'


def test_legacy_cleanup_keeps_connection_and_session_configuration(plugin):
    config = {'base_url':'http://example.test', 'tools_key':'test-key', 'targets':[{'umo':'qq:GroupMessage:g',
              'interaction':{'ai_prompt':'旧人格覆盖','ai_provider_id':'old-model','tools':['news']},
              'presentation':{'notification_template':'{content}'}}],
              'card_font_path':'/fonts/custom.ttf', 'legacy_cat_behavior':False, 'cat_reply_keywords':['猫']}
    clean = sys.modules['plugin_under_test.configuration'].cleanup_legacy_configuration
    assert clean(config) is True
    assert config == {'base_url':'http://example.test', 'tools_key':'test-key',
                      'targets':[{'umo':'qq:GroupMessage:g','interaction':{'tools':['news']},
                                  'presentation':{'notification_template':'{content}'}}], 'card_font_path':'/fonts/custom.ttf'}
    assert clean(config) is False


@pytest.mark.parametrize('persona_id',['cat','catgirl','akashi','custom'])
async def test_unregistered_persona_history_and_media_are_untouched(plugin,persona_id):
    history=[{'role':'user','content':'原有聊天与图片'}]
    images=['file:///original.gif']
    req=SimpleNamespace(conversation=SimpleNamespace(persona_id=persona_id),system_prompt='原始人格',
                        contexts=history,image_urls=images,func_tool=None)
    await plugin.add_instructions(event('unregistered:GroupMessage:g'),req)
    assert req.system_prompt=='原始人格' and req.contexts is history and req.image_urls is images
    plugin.api.assert_not_awaited()


async def test_ordinary_chat_output_is_not_processed_by_anisignal(plugin):
    Plain = sys.modules['astrbot.api.message_components'].Plain
    Face = sys.modules['astrbot.api.message_components'].Face
    chain=[Plain('正常表情 😺 &&custom&&'),Face()]
    ev=event('qq:GroupMessage:g')
    ev.get_result=lambda:SimpleNamespace(is_llm_result=lambda:True,chain=chain)
    await plugin.strip_residual_emotion_markup(ev)
    assert chain[0].text=='正常表情 😺 &&custom&&' and len(chain)==2


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
        request_llm=lambda **kwargs: SimpleNamespace(**kwargs),
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
    await plugin.track_activity(paused)
    plugin.targets['qq:GroupMessage:paused']['enabled'] = True
    assert plugin.session_target(paused) is not None


async def test_message_events_track_last_activity_per_session(plugin):
    plugin.targets = {}
    plugin.last_activity = {}
    await plugin.track_activity(event('qq:GroupMessage:g1'))
    await plugin.track_activity(event('qq:GroupMessage:g2'))
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
    assert assistant_msg['content'] == '人格播报\n\n原始资料'


async def test_history_recording_failure_does_not_break_receipt(plugin):
    plugin.context.conversation_manager.add_message_pair.side_effect = RuntimeError('db down')
    await plugin.send_delivery(delivery())
    receipt = plugin.api.call_args.args[1]
    assert receipt['status'] == 'accepted' and receipt['receipt'] == 'lease'


async def test_residual_emotion_markup_is_stripped_from_replies(plugin):
    Plain = sys.modules['astrbot.api.message_components'].Plain
    chain = [Plain('你好 &&reply&& 不错，&&happy&&')]
    ev = event('qq:GroupMessage:g')
    ev.set_extra('anisignal_reply', True)
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


async def test_raw_product_query_keeps_facts_for_followup(plugin):
    manager = plugin.context.conversation_manager
    manager.get_conversation.return_value = SimpleNamespace(persona_id='catgirl')
    ev = event('qq:FriendMessage:self')
    plugin.config['targets'] = [{'umo':ev.unified_msg_origin, 'presentation':{'reply_mode':'text'}}]
    body = '键盘：已出现3位独立用户到货证据。'
    replies = [reply async for reply in plugin.present_with_persona(ev, body, history_label='商品查询')]
    assert len(replies) == 1
    _, user_msg, assistant_msg = manager.add_message_pair.await_args.args
    assert user_msg['content'] == '（处理了商品查询）' and assistant_msg['content'] == body


async def test_notification_index_keeps_detail_reference_without_broadcast_body(plugin):
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


async def test_notification_keeps_original_persona_actions_emoji_and_length(plugin):
    plugin._resolve_persona = AsyncMock(return_value=('model', '自定义会话人格', 'any-persona'))
    intro = '（放下手里的书）这份消息里有你一直关注的进展，我也一起看看。🙂'
    plugin.context.llm_generate = AsyncMock(return_value=SimpleNamespace(completion_text=intro))
    row = delivery()
    assert (await type(plugin).render_delivery(plugin, row)).text == intro+'\n\n'+row['body']
    prompt = plugin.context.llm_generate.await_args.kwargs['system_prompt']
    assert prompt.startswith('自定义会话人格') and '最多24字' not in prompt


@pytest.mark.parametrize('conversation_persona',[None,'previous-persona'])
async def test_notifications_resolve_native_session_rules_on_every_call(plugin,conversation_persona):
    manager=plugin.context.conversation_manager
    manager.get_conversation.return_value=SimpleNamespace(persona_id=conversation_persona)
    resolver=AsyncMock(side_effect=[('session-rule',{'prompt':'当前原生人格'},None,False),
                                    ('new-selection',{'prompt':'切换后的原生人格'},None,False)])
    plugin.context.persona_manager=SimpleNamespace(resolve_selected_persona=resolver)
    plugin.context.get_config=lambda umo:{'provider_settings':{}}
    plugin.context.get_current_chat_provider_id=AsyncMock(return_value='native-model')
    assert await plugin._resolve_persona('qq:FriendMessage:self')==('native-model','当前原生人格','session-rule')
    assert await plugin._resolve_persona('qq:FriendMessage:self')==('native-model','切换后的原生人格','new-selection')
    assert resolver.await_args.kwargs['conversation_persona_id']==conversation_persona
    assert resolver.await_args.kwargs['umo']=='qq:FriendMessage:self'
    manager.new_conversation.assert_not_awaited()


async def test_omitting_intro_skips_persona_generation_and_follows_template(plugin):
    plugin._resolve_persona=AsyncMock()
    plugin.context.llm_generate=AsyncMock()
    row=delivery()
    plugin.config['targets']=[{'umo':row['umo'],'presentation':{'notification_template':'{body}'}}]
    assert (await type(plugin).render_delivery(plugin,row)).text=='原始资料'
    plugin._resolve_persona.assert_not_awaited()
    plugin.context.llm_generate.assert_not_awaited()


async def test_daily_card_hint_is_controlled_by_the_same_plugin_option(plugin,monkeypatch):
    render=Mock(return_value='/tmp/brief.png')
    monkeypatch.setattr(importlib.import_module('plugin_under_test.card'),'render_brief_card',render)
    umo='qq:FriendMessage:self'
    options={}
    plugin.config['targets']=[{'umo':umo,'presentation':options}]
    brief={'mode':'daily','items':[{'title':'消息','summary':'摘要','sources':[{'text':'有来源正文的完整消息'}]}]}
    await plugin.send_brief_message(umo,'摘要',brief,body='摘要')
    assert render.call_args.args[3]==''
    options['show_details_hint']=True
    await plugin.send_brief_message(umo,'摘要',brief,body='摘要')
    assert render.call_args.args[3]=='查看详情：/展开 1'


@pytest.mark.parametrize('kind', ['updates', 'critical', 'calendar', 'reminder'])
async def test_all_notification_kinds_follow_the_current_persona(plugin, kind):
    plugin._resolve_persona = AsyncMock(return_value=('current-model', '原有人格', 'custom'))
    plugin.context.llm_generate = AsyncMock(return_value=SimpleNamespace(completion_text='你的提醒到啦。'))
    row = {**delivery(), 'kind': kind, 'body': '09/30 10:00 维护开始\n原公告链接'}
    assert (await type(plugin).render_delivery(plugin, row)).text == '你的提醒到啦。\n\n'+row['body']
    assert plugin.context.llm_generate.await_args.kwargs['chat_provider_id']=='current-model'


@pytest.mark.parametrize('persona_id,prompt', [
    ('cat', '自定义纯猫语人格'), ('', '你扮演一只在群里生活的小猫。只通过猫叫回应。'),
])
async def test_notification_does_not_special_case_persona_names(plugin, persona_id, prompt):
    plugin._resolve_persona = AsyncMock(return_value=('model', prompt, persona_id))
    plugin.context.llm_generate = AsyncMock(return_value=SimpleNamespace(completion_text='喵。'))
    row = {**delivery(), 'kind': 'daily'}
    assert (await type(plugin).render_delivery(plugin, row)).text == '喵。\n\n'+row['body']
    assert plugin.context.llm_generate.await_args.kwargs['system_prompt'].startswith(prompt)


@pytest.mark.parametrize('reaction', [
    '', '</p><p>', '&lt;/p&gt;&lt;p&gt;', '[NO_REPLY]', '||meme_start||happy||meme_end||',
])
async def test_notification_drops_only_empty_or_control_output(plugin, reaction):
    plugin._resolve_persona = AsyncMock(return_value=('model', '软糯小猫娘', 'catgirl'))
    plugin.context.llm_generate = AsyncMock(return_value=SimpleNamespace(completion_text=reaction))
    row = delivery()
    assert (await type(plugin).render_delivery(plugin, row)).text == row['body']


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
    result = (await type(plugin).render_delivery(plugin, row)).text
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
    monkeypatch.setattr(card, 'render_brief_card', lambda brief, path, font, hint: path)
    brief = {'mode': 'daily', 'items': [{'title': '动态', 'summary': '具体变化'}] * count}
    sent, text = await plugin.send_brief_message('qq:FriendMessage:self', '短反应\n\n正文', brief, body='正文')
    assert sent and text == '短反应'
    chain = plugin.context.send_message.await_args.args[1]
    assert chain.image_url.endswith('brief.png')
    plugin.context.send_message.assert_awaited_once()


async def test_daily_card_can_be_sent_without_a_forced_caption(plugin, monkeypatch):
    card = importlib.import_module('plugin_under_test.card')
    monkeypatch.setattr(card, 'render_brief_card', lambda brief, path, font, hint: path)
    brief = {'mode': 'daily', 'items': [{'title': '动态', 'summary': '具体变化'}]}
    sent, text = await plugin.send_brief_message('qq:FriendMessage:self', '正文', brief, body='正文')
    assert sent and text == ''
    chain = plugin.context.send_message.await_args.args[1]
    assert chain.image_url.endswith('brief.png') and not getattr(chain, 'text', '')
    plugin.context.send_message.assert_awaited_once()


async def test_original_notification_mode_skips_the_model_and_is_per_session(plugin):
    plugin._resolve_persona = AsyncMock()
    row = {**delivery(), 'kind': 'manual', 'brief': {'mode': 'updates'}, 'body': '鸣潮先约电台深夜频道更新啦。'}
    plugin.config['targets'] = [{'umo':row['umo'], 'presentation':{'notification_style':'original'}}]
    assert (await type(plugin).render_delivery(plugin, row)).text == row['body']
    plugin._resolve_persona.assert_not_awaited()
    assert plugin.presentation('other:GroupMessage:g') == {}


async def test_default_query_uses_native_conversation_without_replacing_its_persona(plugin):
    ev=event('qq:GroupMessage:g')
    ev.get_message_str=lambda:'/资讯 请用两段介绍'
    plugin.targets[ev.unified_msg_origin]={'enabled':True,'tools':['news'], 'instructions':'分为两段'}
    plugin.config['targets']=[{'umo':ev.unified_msg_origin,'presentation':{'instructions':'分为两段'}}]
    replies=[r async for r in plugin.present_brief(ev,{'text':'已核验的资料与来源'},'news')]
    request=replies[0]
    conversation=await plugin.context.conversation_manager.get_conversation('qq:GroupMessage:g','cid-1')
    assert request.conversation is conversation
    assert '已核验的资料与来源' in request.prompt and '分为两段' in request.prompt
    assert not hasattr(request,'system_prompt')
    plugin.context.send_message.assert_not_awaited()
    plugin.context.conversation_manager.add_message_pair.assert_not_awaited()


async def test_default_plugin_does_not_override_normal_chat_or_model(plugin):
    class Tools(list):
        def remove_tool(self,name):
            if name in self:self.remove(name)
    umo='qq:GroupMessage:g'
    plugin.targets[umo]={'enabled':True,'tools':['news'],'provider_id':'old-override','instructions':'旧的固定角色'}
    plugin.context.get_config=lambda **kwargs: {'provider_ltm_settings':{'active_reply':{'enable':True}}}
    ev=event(umo)
    await plugin.track_activity(ev)
    assert ev.get_extra('selected_provider') is None
    history=[{'role':'user','content':'原有聊天'}]
    req=SimpleNamespace(conversation=SimpleNamespace(persona_id='catgirl'),system_prompt='原有自定义人格',
                        contexts=history,func_tool=Tools(['web_search','anisignal_query','anisignal_reminder']))
    await plugin.add_instructions(ev,req)
    assert req.system_prompt.startswith('原有自定义人格') and '旧的固定角色' not in req.system_prompt
    assert '不顺带建议' not in req.system_prompt and req.contexts is history
    assert '站点身份' not in req.system_prompt and '产品名 AniSignal' not in req.system_prompt
    assert 'web_search' in req.func_tool and ev.get_extra('anisignal_cat_chat') is None


async def test_notification_template_does_not_silently_restore_omitted_parts(plugin):
    row=delivery()
    plugin._resolve_persona=AsyncMock(return_value=('model','原人格','custom'))
    plugin.context.llm_generate=AsyncMock(return_value=SimpleNamespace(completion_text='自然引导语'))
    options={'notification_template':'【订阅消息】\n{body}\n---\n{intro}'}
    plugin.config['targets']=[{'umo':row['umo'],'presentation':options}]
    assert (await type(plugin).render_delivery(plugin,row)).text=='【订阅消息】\n原始资料\n---\n自然引导语'
    options['notification_template']='{intro}'
    assert (await type(plugin).render_delivery(plugin,row)).text=='自然引导语'
    options.update(notification_style='original', notification_template='【原文】\n{body}')
    plugin.context.llm_generate.reset_mock()
    assert (await type(plugin).render_delivery(plugin,row)).text=='【原文】\n原始资料'
    plugin.context.llm_generate.assert_not_awaited()


async def test_text_notification_does_not_render_daily_card(plugin,monkeypatch):
    card=importlib.import_module('plugin_under_test.card')
    render=Mock()
    monkeypatch.setattr(card,'render_brief_card',render)
    plugin.config['targets']=[{'umo':delivery()['umo'],'presentation':{'notification_format':'text'}}]
    await plugin.send_brief_message(delivery()['umo'],'完整文字',{'mode':'daily','items':[{'title':'消息'}]})
    render.assert_not_called()
    chain=plugin.context.send_message.await_args.args[1]
    assert chain.text=='完整文字' and chain.image_url is None


async def test_structured_notification_sends_real_image_in_template_order_without_card(plugin,monkeypatch):
    card=importlib.import_module('plugin_under_test.card')
    render=Mock()
    monkeypatch.setattr(card,'render_brief_card',render)
    row={**delivery(),'images':['https://example.test/cover.jpg'],'brief':{'mode':'daily','items':[
        {'title':'真实标题','sources':[{'text':'来源原文','published_at':'2026-10-25T04:00:00Z'}]}]}}
    plugin.config['targets']=[{'umo':row['umo'],'presentation':{'notification_style':'original',
        'notification_template':'{title}\n{content}\n{images}\n发布：{time}'}}]
    plugin.render_delivery=type(plugin).render_delivery.__get__(plugin)
    await plugin.send_delivery(row)
    chain=plugin.context.send_message.await_args.args[1]
    assert chain.parts==[('text','真实标题\n来源原文\n'),('image','https://example.test/cover.jpg'),
                         ('text','\n发布：2026-10-25 12:00')]
    render.assert_not_called()
    plugin.context.send_message.assert_awaited_once()
    assert plugin.api.await_args.args[1]['status']=='accepted'


async def test_structured_notification_without_images_placeholder_does_not_attach_covers(plugin):
    row={**delivery(),'images':['https://example.test/cover.jpg']}
    plugin.config['targets']=[{'umo':row['umo'],'presentation':{'notification_style':'original','notification_template':'{content}'}}]
    plugin.render_delivery=type(plugin).render_delivery.__get__(plugin)
    await plugin.send_delivery(row)
    assert plugin.context.send_message.await_args.args[1].images==[]


async def test_structured_send_error_never_sends_a_second_fallback(plugin):
    row={**delivery(),'images':['https://example.test/cover.jpg']}
    plugin.config['targets']=[{'umo':row['umo'],'presentation':{'notification_style':'original','notification_template':'{content}\n{images}'}}]
    plugin.render_delivery=type(plugin).render_delivery.__get__(plugin)
    plugin.context.send_message.side_effect=TimeoutError()
    await plugin.send_delivery(row)
    plugin.context.send_message.assert_awaited_once()
    assert plugin.api.await_args.args[1]['status']=='unknown'


async def test_card_send_error_does_not_send_a_second_text_message(plugin, monkeypatch):
    card = importlib.import_module('plugin_under_test.card')
    monkeypatch.setattr(card, 'render_brief_card', lambda brief, path, font, hint: path)
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
    monkeypatch.setattr(card, 'render_brief_card', lambda brief, path, font, hint: path)
    row = {**delivery(), 'brief': {'mode': 'daily', 'items': [{'title': '今日消息'}]},
           'images': ['first', 'second', 'third'], 'max_images': 3}
    await plugin.send_delivery(row)
    images = plugin.context.send_message.call_args.args[1].images
    assert len(images) == 3 and images[0].endswith('brief.png')
    assert images[1:] == ['first', 'second']
    plugin.context.send_message.assert_awaited_once()
