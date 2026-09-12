"""B 站采集器：风控拒绝 fail-fast 与按命令熔断（进程级跨任务生效）。"""

from datetime import timedelta

import pytest

from app.collectors import bilibili as bili_module
from app.collectors.base import CollectorError
from app.collectors.bilibili import BiliRiskControlError, BilibiliCollector


@pytest.fixture(autouse=True)
def _clear_circuit_state():
    bili_module._CIRCUIT_STATE.clear()
    yield
    bili_module._CIRCUIT_STATE.clear()


async def test_run_once_flags_risk_control_stderr(monkeypatch):
    class FakeProc:
        returncode = 2

        async def communicate(self):
            return b"", b"BV1: risk control: gated by bilibili's anti-bot for anonymous access"

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(bili_module.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(BiliRiskControlError):
        await BilibiliCollector()._run_once("video", "BV1")


async def test_risk_control_refusal_fails_fast_without_retries(monkeypatch):
    collector = BilibiliCollector()
    calls = []

    async def fake_run_once(*args):
        calls.append(args)
        raise BiliRiskControlError("bili exited 2: risk control: gated")

    monkeypatch.setattr(collector, "_run_once", fake_run_once)

    with pytest.raises(BiliRiskControlError):
        await collector._run("video", "BV1", "-o", "json")

    assert calls == [("video", "BV1", "-o", "json")]
    assert bili_module._circuit_open("video") is False


async def test_circuit_opens_after_consecutive_refusals(monkeypatch):
    collector = BilibiliCollector()
    calls = []

    async def fake_run_once(*args):
        calls.append(args)
        raise BiliRiskControlError("risk control")

    monkeypatch.setattr(collector, "_run_once", fake_run_once)

    for _ in range(2):
        with pytest.raises(BiliRiskControlError):
            await collector._run("video", "BV1")

    assert bili_module._circuit_open("video")
    # 熔断后直接短路，不再触碰端点
    with pytest.raises(BiliRiskControlError):
        await collector._run("video", "BV2")
    assert len(calls) == 2
    # 其他命令不受影响
    assert bili_module._circuit_open("search") is False


async def test_circuit_resets_after_success(monkeypatch):
    collector = BilibiliCollector()
    calls = []

    async def fail_once_then_ok(*args):
        calls.append(args)
        if len(calls) == 1:
            raise BiliRiskControlError("risk control")
        return {"ok": True}

    monkeypatch.setattr(collector, "_run_once", fail_once_then_ok)

    with pytest.raises(BiliRiskControlError):
        await collector._run("video", "BV1")
    assert await collector._run("video", "BV2") == {"ok": True}
    assert bili_module._CIRCUIT_STATE.get("video") is None

    async def fail_again(*args):
        calls.append(args)
        raise BiliRiskControlError("risk control")

    monkeypatch.setattr(collector, "_run_once", fail_again)
    with pytest.raises(BiliRiskControlError):
        await collector._run("video", "BV3")
    assert bili_module._CIRCUIT_STATE["video"]["consecutive"] == 1
    assert bili_module._circuit_open("video") is False


async def test_circuit_reopens_when_probe_fails_after_expired_cooldown(monkeypatch):
    state = bili_module._CIRCUIT_STATE.setdefault("video", {"consecutive": 2, "until": None})
    state["until"] = bili_module.utcnow() - timedelta(seconds=1)
    assert bili_module._circuit_open("video") is False

    collector = BilibiliCollector()
    calls = []

    async def fake_run_once(*args):
        calls.append(args)
        raise BiliRiskControlError("risk control")

    monkeypatch.setattr(collector, "_run_once", fake_run_once)

    with pytest.raises(BiliRiskControlError):
        await collector._run("video", "BV1")

    assert len(calls) == 1
    assert bili_module._circuit_open("video")


async def test_transient_errors_still_retry(monkeypatch):
    collector = BilibiliCollector()
    calls = []
    sleeps = []

    async def fake_run_once(*args):
        calls.append(args)
        if len(calls) < 3:
            raise CollectorError("bili exited 1: boom")
        return {"ok": True}

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(collector, "_run_once", fake_run_once)
    monkeypatch.setattr(bili_module.asyncio, "sleep", fake_sleep)

    assert await collector._run("video", "BV1") == {"ok": True}
    assert len(calls) == 3
    assert sleeps == [2, 4]


async def test_search_videos_opens_circuit_and_falls_back_mid_job(monkeypatch):
    collector = BilibiliCollector()
    search_items = [{"bvid": f"BV{i}", "title": f"t{i}"} for i in range(5)]
    video_calls = []

    async def fake_run_nonempty(*args):
        return search_items

    async def fake_run_once(*args):
        video_calls.append(args[1])
        raise BiliRiskControlError("risk control")

    monkeypatch.setattr(collector, "_run_nonempty", fake_run_nonempty)
    monkeypatch.setattr(collector, "_run_once", fake_run_once)

    details = await collector.search_videos("鸣潮", 5)

    # 两次尝试后熔断，剩余视频不再触碰详情端点；全部用搜索结果兜底
    assert video_calls == ["BV0", "BV1"]
    assert [d["bvid"] for d in details] == ["BV0", "BV1", "BV2", "BV3", "BV4"]


async def test_search_videos_keeps_search_items_while_circuit_open(monkeypatch):
    collector = BilibiliCollector()
    search_items = [
        {"bvid": "BV1", "title": "a", "view_count": 100},
        {"bvid": "BV2", "title": "b", "view_count": 200},
    ]
    bili_module._CIRCUIT_STATE["video"] = {
        "consecutive": 2,
        "until": bili_module.utcnow() + timedelta(minutes=5),
    }

    async def fake_run_nonempty(*args):
        return search_items

    async def fail_video(*args):
        raise AssertionError("video detail should not be called while circuit open")

    monkeypatch.setattr(collector, "_run_nonempty", fake_run_nonempty)
    monkeypatch.setattr(collector, "_run", fail_video)

    details = await collector.search_videos("鸣潮", 10)

    assert [d["bvid"] for d in details] == ["BV1", "BV2"]
    assert details[0]["view_count"] == 100
