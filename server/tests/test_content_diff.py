"""官方公告与内容版本 Diff（抓暗改）单测与 API 集成测试。"""

from datetime import datetime, timedelta

import pytest

from app.clock import utcnow
from app.models import ContentVersion, Event, Evidence, RawContent, Source
from app.services.content_diff import (
    build_version_history,
    collapse_substantive_versions,
    compute_version_diff,
    inline_diff_segments,
    readable_text,
)


def _ver(
    version: int,
    *,
    title: str = "版本更新维护公告",
    text: str = "",
    media: list[str] | None = None,
    parser_version: str = "2",
    extra: dict | None = None,
    minutes_offset: int = 0,
) -> ContentVersion:
    return ContentVersion(
        raw_content_id=1,
        version=version,
        parsed_at=datetime(2026, 11, 20, 8, 0) + timedelta(minutes=minutes_offset),
        title=title,
        text=text,
        normalized_json={"media": media or [], "extra": extra or {}},
        parser_version=parser_version,
    )


def test_readable_text_strips_html_noise_and_preserves_paragraphs():
    html_v1 = '<p style="color:red">维护时间：06:00-11:00</p><p>补偿：星声&times;300<br/>敬请期待</p>'
    plain_v2 = "维护时间：06:00-11:00\n补偿：星声×300\n敬请期待"
    assert readable_text(html_v1) == readable_text(plain_v2)


def test_collapse_skips_reparse_and_cdn_shard_noise():
    v1 = _ver(
        1,
        text="<p>维护时间：06:00-11:00</p>",
        media=["http://i0.hdslb.com/bfs/new_dyn/abc.jpg"],
        parser_version="1",
    )
    # 解析器升级 + B 站图片 CDN 分片轮换：无实质变化
    v2 = _ver(
        2,
        text="<div>维护时间：06:00-11:00</div>",
        media=["https://i2.hdslb.com/bfs/new_dyn/abc.jpg"],
        parser_version="2",
        minutes_offset=10,
    )
    kept = collapse_substantive_versions([v1, v2])
    assert len(kept) == 1
    assert kept[0].version == 1

    # YouTube 封面分片、分辨率、直播态与 ?v= 缓存参数轮换不记为修订
    yt_v1 = _ver(1, title="PV", text="desc", media=["https://i1.ytimg.com/vi/abc123/hqdefault.jpg"])
    yt_v2 = _ver(2, title="PV", text="desc", media=["https://i.ytimg.com/vi/abc123/maxresdefault_live.jpg?v=6ae2ae7e"], minutes_offset=10)
    yt_v3 = _ver(3, title="PV", text="desc", media=["https://i.ytimg.com/vi/abc123/maxresdefault.jpg"], minutes_offset=20)
    assert len(collapse_substantive_versions([yt_v1, yt_v2, yt_v3], platform="youtube")) == 1

    # X 图片格式参数变化与视频预览图 amplify_video_thumb <-> media 切换不记为修订
    x_fmt_v1 = _ver(1, title="Post", text="Patch notes live.", media=["https://pbs.twimg.com/media/AbCdEf123.jpg"])
    x_fmt_v2 = _ver(2, title="Post", text="Patch notes live.", media=["https://pbs.twimg.com/media/AbCdEf123?format=webp&name=medium"], minutes_offset=5)
    assert len(collapse_substantive_versions([x_fmt_v1, x_fmt_v2], platform="x")) == 1

    x_vid_v1 = _ver(1, title="PV", text="Watch the trailer", media=["https://pbs.twimg.com/amplify_video_thumb/999/img/aaa.jpg"])
    x_vid_v2 = _ver(2, title="PV", text="Watch the trailer", media=["https://pbs.twimg.com/media/BBB123?format=jpg&name=large"], minutes_offset=5)
    assert len(collapse_substantive_versions([x_vid_v1, x_vid_v2], platform="x")) == 1

    # RSS/CDN 纯缓存时间戳参数（?t=）变化不记为修订
    cdn_v1 = _ver(1, title="News", text="Body", media=["https://cdn.example.com/asset/hero.png?t=1793422837"])
    cdn_v2 = _ver(2, title="News", text="Body", media=["https://cdn.example.com/asset/hero.png?t=1793423031"], minutes_offset=5)
    assert len(collapse_substantive_versions([cdn_v1, cdn_v2], platform="rss")) == 1


def test_collapse_filters_risk_control_image_drops_and_degraded_snapshots():
    imgs = [
        "https://i0.hdslb.com/bfs/new_dyn/aaa.jpg",
        "https://i0.hdslb.com/bfs/new_dyn/bbb.jpg",
        "https://i0.hdslb.com/bfs/new_dyn/ccc.jpg",
    ]
    # 1. 源站风控丢图（全丢 3->0 或部分丢 3->1，正文未变）：不误报为“删除图片”
    drop_v1 = _ver(1, title="活动公告", text="活动现已开启，欢迎参与。", media=imgs)
    drop_v2 = _ver(2, title="活动公告", text="活动现已开启，欢迎参与。", media=[], minutes_offset=10)
    drop_v3 = _ver(3, title="活动公告", text="活动现已开启，欢迎参与。", media=[imgs[0]], minutes_offset=20)
    kept_drop = collapse_substantive_versions([drop_v1, drop_v2, drop_v3], platform="bilibili")
    assert [v.version for v in kept_drop] == [1]

    # 2. 首抓风控 0 图，后续补回图片（0->3，正文未变）：视为补全而非修订
    backfill_v1 = _ver(1, title="活动公告", text="活动现已开启，欢迎参与。", media=[])
    backfill_v2 = _ver(2, title="活动公告", text="活动现已开启，欢迎参与。", media=imgs, minutes_offset=10)
    kept_bf = collapse_substantive_versions([backfill_v1, backfill_v2], platform="rss")
    assert [v.version for v in kept_bf] == [2]

    # 3. 真实正文编辑时伴随风控丢图（v2 为 0 图），后续 v3 补回图片：合并为单一文本修订且不出现空 Diff/假删图
    edit_v1 = _ver(1, title="公告", text="维护结束：11:00", media=imgs[:2])
    edit_v2 = _ver(2, title="公告", text="维护结束：13:00", media=[], minutes_offset=10)
    edit_v3 = _ver(3, title="公告", text="维护结束：13:00", media=imgs[:2], minutes_offset=20)
    kept_edit = collapse_substantive_versions([edit_v1, edit_v2, edit_v3], platform="rss")
    assert [v.version for v in kept_edit] == [1, 3]
    diff_edit = compute_version_diff(kept_edit[0], kept_edit[1])
    assert diff_edit["stats"]["added_lines"] == 1
    assert diff_edit["media_added"] == []
    assert diff_edit["media_removed"] == []

    # 即使尚未抓回 v3，v1(有图) -> v2(0图+改文) 也只展示文本差异，不误报“移除全部图片”
    diff_mid = compute_version_diff(collapse_substantive_versions([edit_v1, edit_v2], platform="rss")[0],
                                    collapse_substantive_versions([edit_v1, edit_v2], platform="rss")[1])
    assert diff_mid["stats"]["added_lines"] == 1
    assert diff_mid["media_removed"] == []
    assert diff_mid["media_added"] == []

    # 4. B 站空动态多步占位回填后再次风控退回空正文（无后续恢复）：折叠为唯一完整版本
    b_v1 = _ver(1, title="", text="", media=[])
    b_v3 = _ver(3, title="图文动态", text="", media=[imgs[0]], minutes_offset=5)
    b_v7 = _ver(7, title="图文动态", text="", media=[], minutes_offset=10)
    b_v8 = _ver(8, title="OST上架公告", text="《鸣潮》OST现已正式上架各大音乐平台。", media=[imgs[0]], minutes_offset=15)
    b_v9 = _ver(9, title="图文动态", text="", media=[], minutes_offset=20)
    kept_b = collapse_substantive_versions([b_v1, b_v3, b_v7, b_v8, b_v9], platform="bilibili")
    assert [v.version for v in kept_b] == [8]

    # 5. B 站转发动态补全后再退回初始残缺态（v1短+0图 -> v2全+1图 -> v3短+0图）：忽略退化 v3
    short_repost = "鸣潮丨「莫宁」1/6手办 预售即将开启，更多详细情报可关注官方店铺。"
    full_repost = short_repost + "\n2026年9月25日18:00正式开启预售，全款 799 元。"
    r_v1 = _ver(1, title=short_repost, text=short_repost, media=[])
    r_v2 = _ver(2, title=short_repost, text=full_repost, media=[imgs[0]], minutes_offset=10)
    r_v3 = _ver(3, title=short_repost, text=short_repost, media=[], minutes_offset=20)
    kept_r = collapse_substantive_versions([r_v1, r_v2, r_v3], platform="bilibili")
    assert [v.version for v in kept_r] == [2]

    # 6. B 站转发动态原本完整带图，风控后退回首行转发语且丢图（且无后续恢复）：忽略退化 v2
    rep_full = "亲爱的旅行者，特别联动关卡现已正式上线！\n\n互动抽奖 #原神#\n详细活动规则与奖励说明。"
    rep_drop_v1 = _ver(1, title="亲爱的旅行者，特别联动关卡现已正式上线！", text=rep_full, media=imgs[:2])
    rep_drop_v2 = _ver(2, title="亲爱的旅行者，特别联动关卡现已正式上线！", text="亲爱的旅行者，特别联动关卡现已正式上线！", media=[], minutes_offset=60)
    assert [v.version for v in collapse_substantive_versions([rep_drop_v1, rep_drop_v2], platform="bilibili")] == [1]

    # 7. B 站动态图床 bfs/new_dyn 纯换链（正文未变，旧链在 B 站即刻 404 不可比对）：原地升级到最新有效图链
    dyn_v1 = _ver(1, title="巡游巴士", text="广州站活动详情", media=[imgs[0], imgs[1]])
    dyn_v2 = _ver(
        2,
        title="巡游巴士",
        text="广州站活动详情",
        media=[imgs[0], "https://i0.hdslb.com/bfs/new_dyn/zzz999.png"],
        minutes_offset=60,
    )
    kept_dyn = collapse_substantive_versions([dyn_v1, dyn_v2], platform="bilibili")
    assert [v.version for v in kept_dyn] == [2]

    # B 站视频封面（bfs/archive）与官网公告换图（fastcdn.mihoyo.com）旧图均 200 可比对，仍完整保留
    vid_v1 = _ver(1, title="活动PV", text="简介", media=["https://i1.hdslb.com/bfs/archive/old_cover.jpg"])
    vid_v2 = _ver(2, title="活动PV", text="简介", media=["https://i2.hdslb.com/bfs/archive/new_cover.jpg"], minutes_offset=30)
    assert [v.version for v in collapse_substantive_versions([vid_v1, vid_v2], platform="bilibili")] == [1, 2]

    # 8. X Cloudflare 邮箱反爬脱敏（真实邮箱 <-> [email protected]）不算官方修订，优先保留真实邮箱版
    cf_v1 = _ver(1, title="Maintenance", text="Contact support: al.cs@yo-star.com", media=["https://pbs.twimg.com/media/X1.jpg"])
    cf_v2 = _ver(2, title="Maintenance", text="Contact support: [email\xa0protected]", media=["https://pbs.twimg.com/media/X1.jpg"], minutes_offset=30)
    assert [v.version for v in collapse_substantive_versions([cf_v1, cf_v2], platform="x")] == [1]
    assert [v.version for v in collapse_substantive_versions([
        _ver(1, title="Maintenance", text="Contact support: [email\xa0protected]"),
        _ver(2, title="Maintenance", text="Contact support: al.cs@yo-star.com", minutes_offset=30),
    ], platform="x")] == [2]

    # 9. B 站解析器升级：占位标题（空/转发动态）升级、跨版本改摘正文片段为标题、pv4->pv5 剥离转发首行均不算作者改稿
    p_v1 = _ver(1, title="", text="#鸣潮# 活动开启", parser_version="1")
    p_v2 = _ver(2, title="转发动态", text="#鸣潮# 活动开启", parser_version="3", minutes_offset=10)
    p_v3 = _ver(3, title="活动开启", text="#鸣潮# 活动开启", parser_version="4", minutes_offset=20)
    assert [v.version for v in collapse_substantive_versions([p_v1, p_v2, p_v3], platform="bilibili")] == [3]

    syn_text = "#鸣潮#互动抽奖\n「剑仙之道」网页活动开启！"
    syn_v1 = _ver(1, title="「剑仙之道」网页活动开启！", text=syn_text, parser_version="3")
    syn_v2 = _ver(2, title="互动抽奖", text=syn_text, parser_version="4", minutes_offset=10)
    assert [v.version for v in collapse_substantive_versions([syn_v1, syn_v2], platform="bilibili")] == [2]

    rp_v1 = _ver(1, title="转发语第一行", text="转发语第一行\n原动态第一行\n原动态第二行", media=[imgs[0]], parser_version="4")
    rp_v2 = _ver(2, title="原动态第一行", text="原动态第一行\n原动态第二行", media=[imgs[0]], parser_version="5", minutes_offset=20)
    assert [v.version for v in collapse_substantive_versions([rp_v1, rp_v2], platform="bilibili")] == [2]

    # 10. 管理员手工删图（origin=admin）仍必须完整保留，不被风控过滤误吞
    adm_v1 = _ver(1, title="公告", text="正文一致", media=[imgs[0]], parser_version="2")
    adm_v2 = _ver(2, title="公告", text="正文一致", media=[], parser_version="2+admin", minutes_offset=10)
    kept_adm = collapse_substantive_versions([adm_v1, adm_v2], platform="bilibili")
    assert [v.version for v in kept_adm] == [1, 2]
    diff_adm = compute_version_diff(kept_adm[0], kept_adm[1])
    assert diff_adm["media_removed"] == [imgs[0]]


def test_collapse_folds_transient_flap_but_keeps_genuine_revert():
    # 线上常见抖动 1：B 站转发动态丢失原动态正文后再恢复（A -> B子集 -> A）
    full = "恭喜@玩家A等10位同学中奖，已私信通知。\n互动抽奖 #鸣潮# 3.8版本现已开启！"
    b1 = _ver(1, title="恭喜@玩家A等10位同学中奖，已私信通知。", text=full)
    b2 = _ver(2, title="恭喜@玩家A等10位同学中奖，已私信通知。", text="恭喜@玩家A等10位同学中奖，已私信通知。", minutes_offset=10)
    b3 = _ver(3, title="恭喜@玩家A等10位同学中奖，已私信通知。", text=full, minutes_offset=20)
    assert len(collapse_substantive_versions([b1, b2, b3], platform="bilibili")) == 1

    # 线上常见抖动 2：X 瞬时丢正文成空帖后再恢复
    x1 = _ver(1, title="Update", text="Server maintenance starts soon.")
    x2 = _ver(2, title="X 原帖", text="", minutes_offset=5)
    x3 = _ver(3, title="Update", text="Server maintenance starts soon.", minutes_offset=10)
    assert len(collapse_substantive_versions([x1, x2, x3], platform="x")) == 1

    # 真正官方修改后再改回（引入了新词句 13:00）必须完整保留
    e1 = _ver(1, text="维护时间：06:00-11:00")
    e2 = _ver(2, text="维护时间：06:00-13:00", minutes_offset=10)
    e3 = _ver(3, text="维护时间：06:00-11:00", minutes_offset=20)
    kept_edit = collapse_substantive_versions([e1, e2, e3], platform="kuro_cn")
    assert [v.version for v in kept_edit] == [1, 2, 3]


def test_collapse_ignores_empty_draw_and_platform_truncation_backfill():
    # B 站空动态首次抓取无正文，后续自愈回填正文：不误报为官方修订
    draw_v1 = _ver(1, title="动态", text="", media=[])
    draw_v2 = _ver(2, title="动态", text="亲爱的漂泊者，活动已开启。", media=["https://i0.hdslb.com/a.jpg"], minutes_offset=5)
    assert len(collapse_substantive_versions([draw_v1, draw_v2], platform="bilibili")) == 1

    # B 站列表 ~250 字简介截断补全 & 转发动态缺失原动态卡片补全
    b_short = "鸣潮丨「莫宁」1/6手办 预售即将开启，更多详细情报，漂泊者可关注@库洛游戏周边 或前往店铺查看。"
    b_full = b_short + "\n2026年9月25日18:00，手办开启预售，全款 799 元，预计 2027 年 6 月出货。"
    assert len(collapse_substantive_versions([_ver(1, text=b_short), _ver(2, text=b_full, minutes_offset=10)], platform="bilibili")) == 1

    # X 摘要截断（含旧版无 truncated 标志）补全为全文：不误报
    x_v1 = _ver(1, title="Update", text="Patch 2.4 brings a new region", extra={"truncated": True})
    x_v2 = _ver(2, title="Update", text="Patch 2.4 brings a new region and rewards for all players.", extra={"truncated": False}, minutes_offset=3)
    kept_x = collapse_substantive_versions([x_v1, x_v2], platform="x")
    assert len(kept_x) == 1
    assert kept_x[0].version == 2

    x_legacy_v1 = _ver(1, title="Security", text="Commanders, we identified a launcher issue in the Windows client...", parser_version="1")
    x_legacy_v2 = _ver(2, title="Security", text="Commanders, we identified a launcher issue in the Windows client and have deployed a full security patch for all regions.", parser_version="2", minutes_offset=5)
    assert len(collapse_substantive_versions([x_legacy_v1, x_legacy_v2], platform="x")) == 1

    # RSS 短摘要补全文：不误报
    r_v1 = _ver(1, title="News", text="Monday morning the board voted to reinstate the CEO after an emergency meeting.")
    r_v2 = _ver(2, title="News", text="Monday morning the board voted to reinstate the CEO after an emergency meeting. " + "Additional detailed reporting on the governance changes. " * 6, minutes_offset=15)
    assert len(collapse_substantive_versions([r_v1, r_v2], platform="rss")) == 1

    # 官网滚动已知问题更新说明（带落款）绝不被误吞
    k1 = _ver(1, title="3.8版本已知问题及更新说明", text="亲爱的漂泊者：\n1. 修复了卡顿问题。\n《鸣潮》项目组")
    k2 = _ver(2, title="3.8版本已知问题及更新说明", text="亲爱的漂泊者：\n1. 修复了卡顿问题。\n2. 修复了联机掉线问题并补偿星声×300。\n《鸣潮》项目组", minutes_offset=60)
    assert [v.version for v in collapse_substantive_versions([k1, k2], platform="kuro_cn")] == [1, 2]


def test_inline_diff_highlights_exact_chinese_and_timestamp_changes():
    segs = inline_diff_segments(
        "维护时间：06:00-11:00，补偿星声×300",
        "维护时间：06:00-13:00，补偿星声×600",
    )
    deleted = [s["text"] for s in segs if s["op"] == "delete"]
    inserted = [s["text"] for s in segs if s["op"] == "insert"]
    assert deleted == ["06:00-11:00", "300"]
    assert inserted == ["06:00-13:00", "600"]


def test_compute_diff_tracks_title_body_inline_and_media_changes():
    v1 = _ver(
        1,
        title="2.4 版本更新维护公告",
        text="亲爱的漂泊者：\n维护时间：06:00-11:00\n维护补偿：星声×300\n感谢支持。",
        media=["https://cdn.example.com/old.jpg", "https://cdn.example.com/keep.jpg"],
    )
    v2 = _ver(
        2,
        title="2.4 版本更新维护公告（已延期）",
        text="亲爱的漂泊者：\n维护时间：06:00-13:00\n维护补偿：星声×600\n新增延迟补偿说明。\n感谢支持。",
        media=["https://cdn.example.com/keep.jpg", "https://cdn.example.com/new.jpg", "javascript:alert(1)"],
        minutes_offset=60,
    )
    kept = collapse_substantive_versions([v1, v2])
    assert len(kept) == 2

    diff = compute_version_diff(kept[0], kept[1])
    assert diff["title_changed"] is True
    assert any(s["op"] == "insert" and "已延期" in s["text"] for s in diff["title_segments"])
    assert diff["stats"]["added_lines"] == 3
    assert diff["stats"]["removed_lines"] == 2
    assert diff["media_added"] == ["https://cdn.example.com/new.jpg"]
    assert diff["media_removed"] == ["https://cdn.example.com/old.jpg"]

    # 改写行带行内精细片段，未改上下文为 equal
    all_lines = [line for hunk in diff["hunks"] for line in hunk["lines"]]
    del_time = next(line for line in all_lines if line["op"] == "delete" and "06:00-11:00" in line["text"])
    ins_time = next(line for line in all_lines if line["op"] == "insert" and "06:00-13:00" in line["text"])
    assert del_time["segments"] is not None
    assert any(s["op"] == "delete" and s["text"] == "06:00-11:00" for s in del_time["segments"])
    assert ins_time["segments"] is not None
    assert any(s["op"] == "insert" and s["text"] == "06:00-13:00" for s in ins_time["segments"])


def test_origin_distinguishes_admin_and_source_revisions():
    v1 = _ver(1, text="原始内容 A", parser_version="2")
    v2 = _ver(2, text="管理端修正错别字 A'", parser_version="2+admin", minutes_offset=10)
    history = build_version_history(1, [v1, v2])
    assert history["revision_count"] == 1
    assert history["has_source_revision"] is False
    assert history["versions"][1]["origin"] == "admin"
    assert history["diff"]["to_origin"] == "admin"


def test_build_version_history_supports_custom_pair_and_validation():
    v1 = _ver(1, text="第一版内容", parser_version="2")
    v2 = _ver(2, text="第二版内容", parser_version="2", minutes_offset=30)
    v3 = _ver(3, text="第三版内容", parser_version="2+admin", minutes_offset=60)

    default_hist = build_version_history(1, [v1, v2, v3])
    assert default_hist["revision_count"] == 2
    assert default_hist["has_source_revision"] is True
    assert default_hist["diff"]["from_version"] == 2
    assert default_hist["diff"]["to_version"] == 3

    vs_first = build_version_history(1, [v1, v2, v3], from_version=1, to_version=3)
    assert vs_first["diff"]["from_version"] == 1
    assert vs_first["diff"]["to_version"] == 3

    # 倒序自动归一化
    swapped = build_version_history(1, [v1, v2, v3], from_version=3, to_version=1)
    assert swapped["diff"]["from_version"] == 1
    assert swapped["diff"]["to_version"] == 3

    with pytest.raises(ValueError):
        build_version_history(1, [v1, v2, v3], from_version=2, to_version=2)
    with pytest.raises(ValueError):
        build_version_history(1, [v1, v2, v3], from_version=99, to_version=3)


@pytest.mark.asyncio
async def test_public_content_versions_list_filter_and_event_sources(client, session):
    now = utcnow()
    src = Source(platform="kuro_cn", external_id="official-kuro_cn", name="鸣潮官网", identity="official")
    session.add(src)
    await session.flush()

    # 内容 1：官方公告暗改（v1 -> v2）
    revised = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="notice-revised",
        community="wuwa",
        source_id=src.id,
        url="https://mc.kurogames.com/main/news/1001",
        published_at=now - timedelta(hours=2),
        collected_at=now - timedelta(hours=2),
        raw_json={},
    )
    # 内容 2：仅 parser 重算，无实质正文变化（v1 -> v2）
    reparse_only = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="notice-reparse",
        community="wuwa",
        source_id=src.id,
        url="https://mc.kurogames.com/main/news/1002",
        published_at=now - timedelta(hours=1),
        collected_at=now - timedelta(hours=1),
        raw_json={},
    )
    # 内容 3：单版本普通公告
    single = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="notice-single",
        community="wuwa",
        source_id=src.id,
        url="https://mc.kurogames.com/main/news/1003",
        published_at=now - timedelta(minutes=30),
        collected_at=now - timedelta(minutes=30),
        raw_json={},
    )
    # 内容 4：正文未变但官号偷偷换图（应被 revised_only 召回）
    media_swapped = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="notice-media-swap",
        community="wuwa",
        source_id=src.id,
        url="https://mc.kurogames.com/main/news/1004",
        published_at=now - timedelta(minutes=20),
        collected_at=now - timedelta(minutes=20),
        raw_json={},
    )
    # 内容 5：已下架内容（必须 404）
    hidden = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="notice-hidden",
        community="wuwa",
        source_id=src.id,
        published_at=now - timedelta(minutes=10),
        collected_at=now - timedelta(minutes=10),
        hidden=True,
        raw_json={},
    )
    session.add_all([revised, reparse_only, single, media_swapped, hidden])
    await session.flush()

    session.add_all([
        ContentVersion(
            raw_content_id=revised.id, version=1, parsed_at=now - timedelta(hours=2),
            title="2.4 维护公告", text="<p>维护结束：11:00</p><p>补偿星声×300</p>",
            normalized_json={}, parser_version="2", category="version_update",
        ),
        ContentVersion(
            raw_content_id=revised.id, version=2, parsed_at=now - timedelta(hours=1),
            title="2.4 维护公告", text="<p>维护结束：13:00</p><p>补偿星声×600</p>",
            normalized_json={}, parser_version="2", category="version_update",
        ),
        ContentVersion(
            raw_content_id=reparse_only.id, version=1, parsed_at=now - timedelta(hours=1),
            title="活动预告", text="<p>本周五开启</p>",
            normalized_json={}, parser_version="1", category="official_announce",
        ),
        ContentVersion(
            raw_content_id=reparse_only.id, version=2, parsed_at=now - timedelta(minutes=50),
            title="活动预告", text="本周五开启",
            normalized_json={}, parser_version="2", category="version_update",
        ),
        ContentVersion(
            raw_content_id=single.id, version=1, parsed_at=now - timedelta(minutes=30),
            title="停服说明", text="例行停服。",
            normalized_json={}, parser_version="2", category="official_announce",
        ),
        ContentVersion(
            raw_content_id=media_swapped.id, version=1, parsed_at=now - timedelta(minutes=20),
            title="活动长图速递", text="详见下方长图",
            normalized_json={"media": ["https://cdn.example.com/poster_v1.jpg"]},
            parser_version="2", category="official_announce",
        ),
        ContentVersion(
            raw_content_id=media_swapped.id, version=2, parsed_at=now - timedelta(minutes=15),
            title="活动长图速递", text="详见下方长图",
            normalized_json={"media": ["https://cdn.example.com/poster_v2.jpg"]},
            parser_version="2", category="official_announce",
        ),
        ContentVersion(
            raw_content_id=hidden.id, version=1, parsed_at=now - timedelta(minutes=10),
            title="隐藏公告", text="隐藏文本",
            normalized_json={}, parser_version="2", category="official_announce",
        ),
    ])
    await session.flush()

    event = Event(
        title="2.4 版本维护",
        category="version_update",
        community="wuwa",
        identity="official",
        is_active=True,
        confidence="high",
    )
    session.add(event)
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=revised.id, role="primary"))
    await session.commit()

    # 1. 详情页带修订计数
    detail = (await client.get(f"/api/contents/{revised.id}")).json()
    assert detail["version"] == 2
    assert detail["revision_count"] == 1
    assert detail["has_source_revision"] is True

    # 2. 版本历史与 Diff 端点
    ver_resp = await client.get(f"/api/contents/{revised.id}/versions")
    assert ver_resp.status_code == 200
    body = ver_resp.json()
    assert body["revision_count"] == 1
    assert body["has_source_revision"] is True
    assert [v["version"] for v in body["versions"]] == [1, 2]
    assert body["diff"]["stats"]["added_lines"] == 2
    assert body["diff"]["stats"]["removed_lines"] == 2

    # 单版本 / 仅重算内容返回 diff=None，不报 404
    reparse_ver = (await client.get(f"/api/contents/{reparse_only.id}/versions")).json()
    assert reparse_ver["revision_count"] == 0
    assert reparse_ver["diff"] is None

    # 非法参数 400、隐藏内容 404
    assert (await client.get(f"/api/contents/{revised.id}/versions?from_version=1&to_version=1")).status_code == 400
    assert (await client.get(f"/api/contents/{revised.id}/versions?from_version=99")).status_code == 400
    assert (await client.get(f"/api/contents/{hidden.id}/versions")).status_code == 404

    # 3. 列表项附带修订标记，revised_only=true 精准排除重算噪声与单版本内容，同时保留纯换图修订
    all_list = (await client.get("/api/contents?community=wuwa&identity=official")).json()
    assert all_list["total"] == 4
    by_id = {item["id"]: item for item in all_list["items"]}
    assert by_id[revised.id]["revision_count"] == 1
    assert by_id[revised.id]["has_source_revision"] is True
    assert by_id[media_swapped.id]["revision_count"] == 1
    assert by_id[reparse_only.id]["revision_count"] == 0
    assert by_id[single.id]["revision_count"] == 0

    only_revised = (await client.get("/api/contents?community=wuwa&identity=official&revised_only=true")).json()
    assert only_revised["total"] == 2
    assert {item["id"] for item in only_revised["items"]} == {revised.id, media_swapped.id}

    # 4. 事件详情证据链同步暴露修订标记
    ev_detail = (await client.get(f"/api/events/{event.id}")).json()
    assert ev_detail["sources"][0]["content_id"] == revised.id
    assert ev_detail["sources"][0]["revision_count"] == 1
    assert ev_detail["sources"][0]["has_source_revision"] is True
