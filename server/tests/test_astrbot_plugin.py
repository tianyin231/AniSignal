"""验证发送结果不明时不重发，以及工具操作者始终取自真实事件。"""
import importlib.util
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

    module('aiohttp', ClientError=ConnectionError)
    module('astrbot')
    module('astrbot.api', llm_tool=decorator, logger=SimpleNamespace(warning=lambda *a: None))
    module('astrbot.api.event', AstrMessageEvent=object,
        filter=SimpleNamespace(command=decorator, on_llm_request=decorator, event_message_type=decorator,
            EventMessageType=SimpleNamespace(ALL='all')))
    module('astrbot.api.star', Context=object, Star=object, register=decorator)
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
    obj.context = SimpleNamespace(get_platform_inst=lambda p: None, send_message=AsyncMock(return_value=True),
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value='cid-1'),
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
    return SimpleNamespace(unified_msg_origin=umo, get_sender_id=lambda: 'user',
        plain_result=lambda text: text, set_extra=lambda key, value: None)


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
