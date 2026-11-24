"""AniSignal 内置只读 Streamable HTTP MCP Server 协议、鉴权与工具集成测试。"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api import mcp as mcp_api
from app.clock import utcnow
from app.models import (
    CollectTarget,
    ContentSchedule,
    ContentVersion,
    CustomFeed,
    CustomFeedItem,
    EmailCode,
    Event,
    Evidence,
    Product,
    RawContent,
    RedeemCode,
    Source,
    SteamAppSnapshot,
    User,
)
from app.services import bot_clients


@pytest.fixture(autouse=True)
def _clear_mcp_rate_limits():
    mcp_api._mem_rate_buckets.clear()
    yield
    mcp_api._mem_rate_buckets.clear()


async def _register_and_login(client, session, email="mcp@t.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "password123"})
    code_row = await session.scalar(select(EmailCode).order_by(EmailCode.id.desc()).limit(1))
    resp = await client.post("/api/auth/verify", json={"email": email, "code": code_row.code_plain})
    assert resp.status_code == 200
    return await session.scalar(select(User).where(User.email == email))


async def _rpc(client, method: str, params: dict | None = None, *, req_id=1, headers=None, query=""):
    payload = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        payload["id"] = req_id
    if params is not None:
        payload["params"] = params
    return await client.post(f"/api/mcp{query}", json=payload, headers=headers or {})


async def _seed_rich_dataset(session):
    now = utcnow()
    src_wuwa = Source(platform="kuro_cn", name="鸣潮官方", identity="official", external_id="mcp-wuwa-off")
    src_hsr = Source(platform="miyoushe", name="崩铁官方", identity="official", external_id="mcp-hsr-off")
    session.add_all([src_wuwa, src_hsr])
    await session.flush()

    raw_wuwa = RawContent(
        source_id=src_wuwa.id,
        platform="kuro_cn",
        content_type="notice",
        external_id="mcp_wuwa_1",
        url="https://mc.kurogames.com/notice/100",
        community="wuwa",
        published_at=now - timedelta(hours=3),
        content_hash="h1",
        raw_json={},
    )
    raw_hsr = RawContent(
        source_id=src_hsr.id,
        platform="miyoushe",
        content_type="notice",
        external_id="mcp_hsr_1",
        url="https://www.miyoushe.com/sr/article/200",
        community="hsr",
        published_at=now - timedelta(hours=2),
        content_hash="h2",
        raw_json={},
    )
    raw_hidden = RawContent(
        source_id=src_wuwa.id,
        platform="kuro_cn",
        content_type="notice",
        external_id="mcp_hidden_1",
        url="https://mc.kurogames.com/notice/hidden",
        community="wuwa",
        hidden=True,
        published_at=now - timedelta(hours=1),
        content_hash="h3",
        raw_json={},
    )
    session.add_all([raw_wuwa, raw_hsr, raw_hidden])
    await session.flush()

    v1 = ContentVersion(
        raw_content_id=raw_wuwa.id,
        version=1,
        parsed_at=now - timedelta(hours=3),
        title="2.8 版本维护更新公告",
        text="维护时间：06:00 - 11:00\n补偿：星声×300",
        normalized_json={"media": [], "extra": {}},
        parser_version="2",
    )
    v2 = ContentVersion(
        raw_content_id=raw_wuwa.id,
        version=2,
        parsed_at=now - timedelta(hours=1),
        title="2.8 版本维护更新公告",
        text="维护时间：06:00 - 13:00\n补偿：星声×600",
        normalized_json={"media": [], "extra": {}},
        parser_version="2",
    )
    v_hsr = ContentVersion(
        raw_content_id=raw_hsr.id,
        version=1,
        parsed_at=now - timedelta(hours=2),
        title="星穹铁道前瞻直播预告",
        text="本周五 19:30 开启前瞻直播。",
        normalized_json={"media": [], "extra": {}},
        parser_version="2",
    )
    v_hidden = ContentVersion(
        raw_content_id=raw_hidden.id,
        version=1,
        parsed_at=now - timedelta(hours=1),
        title="机密测试隐藏公告",
        text="不应被 MCP 查到",
        normalized_json={"media": [], "extra": {}},
        parser_version="2",
    )
    session.add_all([v1, v2, v_hsr, v_hidden])
    await session.flush()
    raw_wuwa.latest_version_id = v2.id
    raw_hsr.latest_version_id = v_hsr.id
    raw_hidden.latest_version_id = v_hidden.id

    ev = Event(
        title="鸣潮 2.8 版本维护与补偿上调",
        summary="维护时间延至 13:00，补偿提升至星声×600。",
        category="version_update",
        community="wuwa",
        identity="official",
        importance=True,
        is_active=True,
    )
    session.add(ev)
    await session.flush()
    session.add(Evidence(event_id=ev.id, raw_content_id=raw_wuwa.id))

    session.add_all([
        ContentSchedule(
            raw_content_id=raw_wuwa.id,
            community="wuwa",
            title="2.8 限时挑战活动",
            kind="activity",
            start_at=now - timedelta(hours=10),
            end_at=now + timedelta(hours=36),
            all_day=False,
        ),
        ContentSchedule(
            raw_content_id=raw_hsr.id,
            community="hsr",
            title="星铁版本前瞻直播",
            kind="broadcast",
            start_at=now + timedelta(hours=24),
            end_at=now + timedelta(hours=25),
            all_day=False,
        ),
        RedeemCode(
            raw_content_id=raw_wuwa.id,
            community="wuwa",
            code="WUWA28BONUS",
            status="active",
            expires_at=now + timedelta(days=3),
        ),
        RedeemCode(
            raw_content_id=raw_wuwa.id,
            community="wuwa",
            code="EXPIRED999",
            status="active",
            expires_at=now - timedelta(hours=1),
        ),
        Product(
            slug="wuwa-keyboard",
            name="爱弥斯联名客制化键盘",
            community="wuwa",
            watch_terms=["爱弥斯", "键盘"],
            monitor_status="watching",
        ),
        CollectTarget(
            community="wuwa",
            platform="steam",
            target_type="steam_app",
            target_value="3513350",
            display_name="Wuthering Waves",
            enabled=True,
        ),
        SteamAppSnapshot(
            community="wuwa",
            app_id=3513350,
            name="Wuthering Waves",
            player_count=48210,
            global_positive=18000,
            global_negative=2000,
            global_score_desc="Very Positive",
            cn_positive=9000,
            cn_negative=1000,
            price_formatted="免费开玩",
            discount_percent=0,
            captured_at=now - timedelta(minutes=15),
        ),
    ])
    await session.commit()
    return {"raw_wuwa": raw_wuwa, "raw_hsr": raw_hsr, "raw_hidden": raw_hidden, "event": ev}


@pytest.mark.asyncio
async def test_mcp_protocol_handshake_discovery_and_errors(client):
    # GET 浏览器/JSON 发现端点
    info = await client.get("/api/mcp", headers={"Accept": "application/json"})
    assert info.status_code == 200
    body = info.json()
    assert body["name"] == "anisignal"
    assert body["transport"] == "streamable-http"
    assert "list_official_revisions" in body["tools"]

    # GET 纯 SSE 请求按 Streamable HTTP 规范返回 405
    sse = await client.get("/api/mcp", headers={"Accept": "text/event-stream"})
    assert sse.status_code == 405
    assert "POST" in sse.headers["allow"]

    # DELETE 无状态关闭返回 204
    assert (await client.delete("/api/mcp")).status_code == 204

    # initialize 协议协商
    init = await _rpc(client, "initialize", {"protocolVersion": "2025-03-26", "clientInfo": {"name": "pytest"}})
    assert init.status_code == 200
    assert init.headers["mcp-protocol-version"] == "2025-03-26"
    init_res = init.json()["result"]
    assert init_res["protocolVersion"] == "2025-03-26"
    assert init_res["serverInfo"]["name"] == "anisignal"

    # notifications/initialized（无 id）返回 202
    notif = await _rpc(client, "notifications/initialized", req_id=None)
    assert notif.status_code == 202

    # Batch 批处理请求
    batch = await client.post(
        "/api/mcp",
        json=[
            {"jsonrpc": "2.0", "id": 10, "method": "ping"},
            {"jsonrpc": "2.0", "id": 11, "method": "prompts/list"},
        ],
    )
    assert batch.status_code == 200
    arr = batch.json()
    assert len(arr) == 2
    assert arr[0]["result"] == {}
    assert any(p["name"] == "daily_briefing" for p in arr[1]["result"]["prompts"])

    # 非法 JSON 与未知方法
    bad_json = await client.post("/api/mcp", content=b"{not-json", headers={"Content-Type": "application/json"})
    assert bad_json.status_code == 400
    assert bad_json.json()["error"]["code"] == -32700

    unknown = await _rpc(client, "does/not_exist")
    assert unknown.status_code == 200
    assert unknown.json()["error"]["code"] == -32601

    # 无效 Token 必须返回 401，绝不静默降级为匿名
    unauth = await _rpc(client, "ping", headers={"Authorization": "Bearer invalid-token-xyz"})
    assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_mcp_public_tools_resources_and_prompts(client, session):
    seeded = await _seed_rich_dataset(session)

    # 匿名 tools/list 不含 get_my_watch_feed，且全部标注只读
    tl = (await _rpc(client, "tools/list")).json()["result"]["tools"]
    names = [t["name"] for t in tl]
    assert "search_signals" in names
    assert "list_official_revisions" in names
    assert "get_my_watch_feed" not in names
    assert all(t["annotations"]["readOnlyHint"] is True for t in tl)

    # search_signals（排除隐藏内容）
    s_res = (await _rpc(client, "tools/call", {"name": "search_signals", "arguments": {"q": "公告"}})).json()["result"]
    assert s_res["isError"] is False
    text = s_res["content"][0]["text"]
    assert "2.8 版本维护更新公告" in text
    assert "机密测试隐藏公告" not in text

    # get_content_detail 带版本 Diff；隐藏内容返回 isError=True
    d_res = (
        await _rpc(client, "tools/call", {"name": "get_content_detail", "arguments": {"content_id": seeded["raw_wuwa"].id}})
    ).json()["result"]
    assert d_res["isError"] is False
    assert "星声×600" in d_res["content"][0]["text"]
    assert "v1→v2" in d_res["content"][0]["text"]

    h_res = (
        await _rpc(client, "tools/call", {"name": "get_content_detail", "arguments": {"content_id": seeded["raw_hidden"].id}})
    ).json()["result"]
    assert h_res["isError"] is True

    # list_events
    ev_res = (await _rpc(client, "tools/call", {"name": "list_events", "arguments": {"community": "wuwa"}})).json()["result"]
    assert "鸣潮 2.8 版本维护与补偿上调" in ev_res["content"][0]["text"]

    # get_calendar (ending_soon)
    cal_res = (await _rpc(client, "tools/call", {"name": "get_calendar", "arguments": {"status": "ending_soon"}})).json()["result"]
    assert "2.8 限时挑战活动" in cal_res["content"][0]["text"]

    # list_redeem_codes（自动排除已过期码）
    code_res = (await _rpc(client, "tools/call", {"name": "list_redeem_codes", "arguments": {}})).json()["result"]
    code_txt = code_res["content"][0]["text"]
    assert "WUWA28BONUS" in code_txt
    assert "EXPIRED999" not in code_txt

    # list_official_revisions
    rev_res = (await _rpc(client, "tools/call", {"name": "list_official_revisions", "arguments": {"days": 14}})).json()["result"]
    assert "2.8 版本维护更新公告" in rev_res["content"][0]["text"]
    assert "v1→v2" in rev_res["content"][0]["text"]

    # get_product_fulfillment（列表 + slug 详情）
    prod_res = (await _rpc(client, "tools/call", {"name": "get_product_fulfillment", "arguments": {"q": "键盘"}})).json()["result"]
    assert "爱弥斯联名客制化键盘" in prod_res["content"][0]["text"]
    slug_res = (await _rpc(client, "tools/call", {"name": "get_product_fulfillment", "arguments": {"slug": "wuwa-keyboard"}})).json()["result"]
    assert slug_res["isError"] is False
    assert "wuwa-keyboard" in slug_res["content"][0]["text"]

    # get_steam_pulse
    steam_res = (await _rpc(client, "tools/call", {"name": "get_steam_pulse", "arguments": {"community": "wuwa"}})).json()["result"]
    assert "Wuthering Waves" in steam_res["content"][0]["text"]
    assert "48,210" in steam_res["content"][0]["text"]

    # resources/list & resources/read
    res_list = (await _rpc(client, "resources/list")).json()["result"]["resources"]
    uris = [r["uri"] for r in res_list]
    assert "anisignal://codes" in uris
    assert "anisignal://account/preferences" not in uris

    read_codes = (await _rpc(client, "resources/read", {"uri": "anisignal://codes"})).json()["result"]["contents"]
    parsed_codes = json.loads(read_codes[0]["text"])
    assert [c["code"] for c in parsed_codes] == ["WUWA28BONUS"]

    # prompts/get
    pr = (await _rpc(client, "prompts/get", {"name": "audit_official_revisions", "arguments": {"community": "wuwa"}})).json()["result"]
    assert "wuwa" in pr["messages"][0]["content"]["text"]


@pytest.mark.asyncio
async def test_mcp_authenticated_personalization_watch_feed_and_token_rotation(client, session):
    await _seed_rich_dataset(session)
    user = await _register_and_login(client, session, email="personal_mcp@t.com")

    # 配置账号偏好仅看 wuwa，添加关键词 "维护" 与自建定制监控
    user.feed_preferences = {"communities": ["wuwa"], "exclude_keywords": []}
    await session.commit()
    await client.post("/api/account/keywords", json={"keyword": "维护"})

    own_feed = CustomFeed(user_id=user.id, name="我的 UP 监控", kind="uid", platform="bilibili", target_value="1001")
    other_user = User(email="other@t.com", password_hash="x", is_verified=True, status="active")
    session.add_all([own_feed, other_user])
    await session.flush()
    other_feed = CustomFeed(user_id=other_user.id, name="别人的私有监控", kind="uid", platform="bilibili", target_value="2002")
    session.add(other_feed)
    await session.flush()
    session.add_all([
        CustomFeedItem(feed_id=own_feed.id, platform="bilibili", external_id="bv1", title="我的定制视频", url="https://b23.tv/1"),
        CustomFeedItem(feed_id=other_feed.id, platform="bilibili", external_id="bv2", title="别人的私密视频", url="https://b23.tv/2"),
    ])
    await session.commit()

    # 获取 feed-token（含 mcp_path）
    tok_resp = await client.get("/api/account/feed-token")
    assert tok_resp.status_code == 200
    token = tok_resp.json()["token"]
    assert tok_resp.json()["mcp_path"] == f"/mcp?token={token}"
    headers = {"Authorization": f"Bearer {token}"}

    # 已认证 tools/list 展示 get_my_watch_feed
    tl = (await _rpc(client, "tools/list", headers=headers)).json()["result"]["tools"]
    assert any(t["name"] == "get_my_watch_feed" for t in tl)

    # 省略 community 时自动按账号偏好（wuwa）过滤，不混入 hsr；显式传 all 时可跨区查看
    pref_search = (await _rpc(client, "tools/call", {"name": "search_signals", "arguments": {}}, headers=headers)).json()["result"]
    assert "2.8 版本维护更新公告" in pref_search["content"][0]["text"]
    assert "星穹铁道前瞻直播预告" not in pref_search["content"][0]["text"]

    all_search = (
        await _rpc(client, "tools/call", {"name": "search_signals", "arguments": {"community": "all"}}, headers=headers)
    ).json()["result"]
    assert "星穹铁道前瞻直播预告" in all_search["content"][0]["text"]

    # get_my_watch_feed 只返回本人关注流与本人定制监控（不泄露他人私有监控）
    wf = (await _rpc(client, "tools/call", {"name": "get_my_watch_feed", "arguments": {}}, headers=headers)).json()["result"]
    wf_text = wf["content"][0]["text"]
    assert "2.8 版本维护更新公告" in wf_text
    assert "我的定制视频" in wf_text
    assert "别人的私密视频" not in wf_text

    # 读取账号偏好资源（不含邮箱与密码）
    pref_res = (await _rpc(client, "resources/read", {"uri": "anisignal://account/preferences"}, headers=headers)).json()["result"]
    pref_json = json.loads(pref_res["contents"][0]["text"])
    assert pref_json["preferred_communities"] == ["wuwa"]
    assert pref_json["keywords"] == ["维护"]
    assert "email" not in pref_json

    # 也支持 Bot Token 鉴权
    _, bot_token = await bot_clients.create_user_client(session, user, "MCP Test Bot")
    bot_ping = await _rpc(client, "ping", headers={"Authorization": f"Bearer {bot_token}"})
    assert bot_ping.status_code == 200

    # 重置 feed-token 后旧 Token 立刻失效（401）
    rot = await client.post("/api/account/feed-token/rotate")
    assert rot.status_code == 200
    assert (await _rpc(client, "ping", headers=headers)).status_code == 401
    assert (await _rpc(client, "ping", query=f"?token={rot.json()['token']}")).status_code == 200
