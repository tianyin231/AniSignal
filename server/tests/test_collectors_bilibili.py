"""B 站采集器：风控拒绝 fail-fast 与按命令熔断（进程级跨任务生效）。"""

from datetime import timedelta

import pytest
import httpx

from app.collectors import bilibili as bili_module
from app.collectors.base import CollectorDeferred, CollectorError
from app.collectors.bilibili import BiliRiskControlError, BilibiliCollector


@pytest.fixture(autouse=True)
def _clear_circuit_state():
    bili_module._CIRCUIT_STATE.clear()
    bili_module._DRAW_DETAIL_CACHE.clear()
    bili_module._DRAW_DETAIL_RETRY_AT.clear()
    yield
    bili_module._CIRCUIT_STATE.clear()
    bili_module._DRAW_DETAIL_CACHE.clear()
    bili_module._DRAW_DETAIL_RETRY_AT.clear()


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


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_reap_cli_process(monkeypatch, cancel):
    class FakeProc:
        returncode = None
        calls = 0
        killed = False

        async def communicate(self):
            self.calls += 1
            if self.calls == 1:
                raise bili_module.asyncio.CancelledError() if cancel else TimeoutError()
            return b"", b""

        def kill(self):
            self.killed = True
            self.returncode = -9

    proc = FakeProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(bili_module.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(bili_module.asyncio.CancelledError if cancel else CollectorError):
        await BilibiliCollector()._run_once("dynamics", "1")
    assert proc.killed and proc.calls == 2


@pytest.mark.parametrize("exit_code", [4, 6])
async def test_cli_refusal_exit_codes_do_not_retry(monkeypatch, exit_code):
    calls = []

    class FakeProc:
        returncode = exit_code

        async def communicate(self):
            return b"", b"Dynamics: refused: the first page of this feed carried nothing"

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr(bili_module.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(BiliRiskControlError):
        await BilibiliCollector()._run("dynamics", "123")
    assert len(calls) == 1


def _mock_detail_http(monkeypatch, handler):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        bili_module.httpx, "AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize("refusal", [-352, -412, -509, 412, 429])
async def test_detail_refusals_are_cached_and_open_circuit(monkeypatch, refusal):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            refusal if refusal > 0 else 200, json={"code": refusal}
        )

    _mock_detail_http(monkeypatch, handler)
    collector = BilibiliCollector()
    assert await collector._fetch_draw_detail("one") == {}
    assert await collector._fetch_draw_detail("one") == {}
    assert len(calls) == 1
    assert await collector._fetch_draw_detail("two") == {}
    assert await collector._fetch_draw_detail("three") == {}
    assert len(calls) == 2
    assert bili_module._circuit_open("dynamic-detail")
    assert not bili_module._circuit_open("dynamics")


async def test_invisible_dynamic_does_not_block_other_details(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.params["id"])
        if calls[-1] == "gone":
            return httpx.Response(200, json={"code": 4101152})
        return httpx.Response(200, json={
            "code": 0, "data": {"item": {"modules": {"module_dynamic": {
                "major": {"opus": {"summary": {"text": "完整正文"}, "pics": []}}
            }}}}
        })

    _mock_detail_http(monkeypatch, handler)
    collector = BilibiliCollector()
    assert await collector._fetch_draw_detail("gone") == {}
    assert await collector._fetch_draw_detail("gone") == {}
    assert not bili_module._circuit_open("dynamic-detail")
    assert (await collector._fetch_draw_detail("good"))["text"] == "完整正文"
    assert (await BilibiliCollector()._fetch_draw_detail("good"))["text"] == "完整正文"
    assert calls == ["gone", "good"]


async def test_enriches_empty_articles_but_skips_live_and_image_only(monkeypatch):
    collector = BilibiliCollector()
    calls = []

    async def detail(dynamic_id):
        calls.append(dynamic_id)
        return {"text": "专栏补全", "pics": ["https://example.com/a.jpg"]}

    monkeypatch.setattr(collector, "_fetch_draw_detail", detail)
    items = [
        {"id": "article", "type": "DYNAMIC_TYPE_ARTICLE", "text": "", "pics": []},
        {"id": "live", "type": "DYNAMIC_TYPE_LIVE_RCMD", "text": "", "pics": []},
        {"id": "image", "type": "DYNAMIC_TYPE_DRAW", "pics": ["https://example.com/b.jpg"]},
    ]
    result = await collector._enrich_draw_dynamics(items)
    assert calls == ["article"]
    assert result[0]["text"] == "专栏补全"


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
    with pytest.raises(CollectorDeferred) as deferred:
        await collector._run("video", "BV2")
    assert deferred.value.retry_at == bili_module._CIRCUIT_STATE["video"]["until"]
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


# ---------- 参数注入：外部值不许长得像 CLI 选项 ----------


@pytest.fixture
def no_subprocess(monkeypatch):
    """任何 attempt 去 spawn bili 都直接判死：证明是「入口拦住了」，不是命令自己失败。"""
    calls: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        raise AssertionError(f"不应执行 bili：{args}")

    monkeypatch.setattr(bili_module.asyncio, "create_subprocess_exec", fake_exec)
    return calls


@pytest.mark.parametrize("bad", ["-o", "--type", "-n", "--videos", "--no-cache", "-", "--out"])
async def test_search_rejects_option_like_keyword(bad, no_subprocess):
    """外部值恰好等于白名单里的合法 flag 时，也必须被拒（按位置判，不按值判）。"""
    with pytest.raises(CollectorError):
        await BilibiliCollector().search_videos(bad, 5)
    assert no_subprocess == []


async def test_account_rejects_option_like_mid(no_subprocess):
    with pytest.raises(CollectorError):
        await BilibiliCollector().user_videos("--videos", 5)
    with pytest.raises(CollectorError):
        await BilibiliCollector().user_dynamics("-n", 5)
    assert no_subprocess == []


async def test_keyword_with_control_chars_rejected(no_subprocess):
    with pytest.raises(CollectorError):
        await BilibiliCollector().search_videos("鸣潮\x00", 5)
    assert no_subprocess == []


async def test_guard_argv_blocks_unknown_flag(monkeypatch):
    called = False

    async def fake_exec(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("不应执行")

    monkeypatch.setattr(bili_module.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(CollectorError):
        await BilibiliCollector()._run("video", "BV1", "--out", "/tmp/x")
    assert called is False
