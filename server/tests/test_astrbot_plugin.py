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
    obj.context = SimpleNamespace(get_platform_inst=lambda p: None, send_message=AsyncMock(return_value=True))
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
