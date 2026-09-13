"""AI 账号池：密钥轮询分摊、限流冷却与全失败抛错。"""

import httpx
import pytest

from app.services import ai as ai_module
from app.services.ai import _pool_keys, chat

CONFIG = {"base_url": "http://x", "api_key": "keyA", "api_keys": ["keyA", "keyB"], "model": "m"}


def status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://x/chat/completions")
    return httpx.HTTPStatusError(f"{code} error", request=request, response=httpx.Response(code, request=request))


@pytest.fixture(autouse=True)
def _reset_pool(monkeypatch):
    ai_module._POOL_INDEX = 0
    ai_module._KEY_COOLDOWN.clear()
    yield
    ai_module._POOL_INDEX = 0
    ai_module._KEY_COOLDOWN.clear()


def test_pool_keys_dedup_and_primary_first():
    # 池中已含主密钥时保持管理员列出的顺序；缺失时主密钥兜底插入
    assert _pool_keys({"api_key": "A", "api_keys": ["B", "A", "C"]}) == ["B", "A", "C"]
    assert _pool_keys({"api_key": "A"}) == ["A"]
    assert _pool_keys({"api_key": "A", "api_keys": ["B", "C"]}) == ["A", "B", "C"]
    assert _pool_keys({}) == [""]


async def test_chat_rotates_to_next_key_on_429(monkeypatch):
    calls: list[str] = []

    async def once(config, key, system, user, json_mode=False):
        calls.append(key)
        if key == "keyA":
            raise status_error(429)
        return "ok", {"prompt_tokens": 1}

    monkeypatch.setattr(ai_module, "_chat_once", once)
    out, usage = await chat(CONFIG, "s", "u")
    assert out == "ok" and usage == {"prompt_tokens": 1}
    assert calls == ["keyA", "keyB"]
    # 密钥级失败进入冷却
    assert ai_module._KEY_COOLDOWN["keyA"] > 0


async def test_pool_index_advances_after_success(monkeypatch):
    order: list[str] = []

    async def once(config, key, system, user, json_mode=False):
        order.append(key)
        return "ok", None

    monkeypatch.setattr(ai_module, "_chat_once", once)
    await chat(CONFIG, "s", "u")
    await chat(CONFIG, "s", "u")
    # 成功后轮询起点推进，两次调用分摊到不同密钥
    assert order == ["keyA", "keyB"]


async def test_cooldown_skips_failed_key_on_next_call(monkeypatch):
    calls: list[str] = []

    async def once(config, key, system, user, json_mode=False):
        calls.append(key)
        if key == "keyA":
            raise status_error(429)
        return "ok", None

    monkeypatch.setattr(ai_module, "_chat_once", once)
    await chat(CONFIG, "s", "u")
    assert calls == ["keyA", "keyB"]
    # 冷却期内：直接从 keyB 开始，不再碰 keyA
    await chat(CONFIG, "s", "u")
    assert calls[2:] == ["keyB"]


async def test_all_keys_exhausted_raises_last_error(monkeypatch):
    async def once(config, key, system, user, json_mode=False):
        raise status_error(429)

    monkeypatch.setattr(ai_module, "_chat_once", once)
    with pytest.raises(httpx.HTTPStatusError):
        await chat(CONFIG, "s", "u")


async def test_non_key_errors_rotate_without_cooldown(monkeypatch):
    calls: list[str] = []

    async def once(config, key, system, user, json_mode=False):
        calls.append(key)
        if key == "keyA":
            raise httpx.ConnectError("boom")
        return "ok", None

    monkeypatch.setattr(ai_module, "_chat_once", once)
    out, _ = await chat(CONFIG, "s", "u")
    assert out == "ok" and calls == ["keyA", "keyB"]
    assert ai_module._KEY_COOLDOWN.get("keyA") is None
