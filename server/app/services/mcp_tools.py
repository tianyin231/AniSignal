"""AniSignal 内置只读 MCP（Model Context Protocol）工具、资源与提示词实现。

严格只读，复用站内现有检索、日历、兑换码、官方修订 Diff、事件、周边履约、Steam 遥测与 AI 日报服务：
- 匿名连接：可查询全部公开只读数据；
- 个人 Token 连接：默认套用账号分区偏好与排除词，并解锁个人关键词关注流与定制监控。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.clock import utcnow
from app.communities import COMMUNITIES, ensure_communities_synced, get_community, is_all_community
from app.models import (
    ContentSchedule,
    ContentTranslation,
    ContentVersion,
    CustomFeed,
    CustomFeedItem,
    Event,
    Evidence,
    Product,
    RawContent,
    RedeemCode,
    Source,
    User,
    UserKeyword,
)
from app.services.hidden_communities import account_blocks
from app.services.notifications import preference_conditions
from app.services.urls import safe_link


READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

_SCHEDULE_KIND_LABELS = {
    "activity": "活动",
    "pool": "卡池",
    "maintenance": "维护",
    "broadcast": "前瞻直播",
    "delay": "变更/延期",
    "deadline": "截止",
}


class McpToolError(ValueError):
    """工具调用参数或权限错误（转换为 MCP isError=true 响应，不触发 JSON-RPC 500）。"""


@dataclass(frozen=True)
class McpContext:
    user: User | None
    base_url: str
    redis: Any | None = None

    @property
    def preferred_communities(self) -> list[str]:
        if self.user is None:
            return []
        prefs = self.user.feed_preferences or {}
        selected = prefs.get("communities") or []
        return [c for c in selected if isinstance(c, str) and c in COMMUNITIES]

    @property
    def excluded_keywords(self) -> list[str]:
        if self.user is None:
            return []
        prefs = self.user.feed_preferences or {}
        return [str(k).strip() for k in (prefs.get("exclude_keywords") or []) if str(k).strip()]

    @property
    def hidden_communities(self) -> tuple[str, ...]:
        if self.user is None:
            return ()
        return account_blocks(self.user).communities


def _fmt_bj(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if dt is None:
        return "时间未知"
    return (dt + timedelta(hours=8)).strftime(fmt) + " (UTC+8)"


def _clip(text: str | None, limit: int = 240) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip("，、：； ") + "…"


def _clamp_int(value: Any, default: int, low: int, high: int, name: str) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise McpToolError(f"参数 {name} 应为整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise McpToolError(f"参数 {name} 应为整数") from exc
    return max(low, min(high, parsed))


def _str_arg(arguments: dict[str, Any], key: str, *, max_len: int = 100) -> str:
    raw = arguments.get(key)
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise McpToolError(f"参数 {key} 应为字符串")
    value = raw.strip()
    if len(value) > max_len:
        raise McpToolError(f"参数 {key} 长度不能超过 {max_len} 字符")
    return value


def _bool_arg(arguments: dict[str, Any], key: str, default: bool = False) -> bool:
    raw = arguments.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    raise McpToolError(f"参数 {key} 应为布尔值")


def _resolve_community_scope(
    ctx: McpContext, raw_community: str
) -> tuple[str, list[str] | None, tuple[str, ...]]:
    """解析 community 参数：
    - 显式传入具体分区：校验有效并返回单分区；
    - 显式传入 "all"：跨全站（遵循用户隐藏分区）；
    - 未传：已认证且配置偏好分区时默认限定到偏好分区，否则跨全站。
    返回 (community_arg, community_list_or_none, exclude_communities)。
    """
    value = raw_community.strip().lower()
    if not value:
        preferred = ctx.preferred_communities
        if preferred:
            if len(preferred) == 1:
                return preferred[0], preferred, ()
            return "all", preferred, ()
        return "all", None, ctx.hidden_communities
    if is_all_community(value):
        return "all", None, ctx.hidden_communities
    if value not in COMMUNITIES:
        valid = ", ".join(k for k, m in COMMUNITIES.items() if m.active)
        raise McpToolError(f"未知分区 '{value}'。可用分区：{valid}（或传 'all' 查全站）。")
    return value, [value], ()


def _matches_exclude_keywords(text: str, excluded: list[str]) -> bool:
    if not excluded:
        return False
    lowered = text.casefold()
    return any(word.casefold() in lowered for word in excluded)


def _content_url(base_url: str, content_id: int, *, anchor: str = "") -> str:
    base = base_url.rstrip("/")
    return f"{base}/contents/{content_id}{anchor}"


def _event_url(base_url: str, event_id: int) -> str:
    return f"{base_url.rstrip('/')}/events/{event_id}"


def _product_url(base_url: str, slug: str, community: str) -> str:
    return f"{base_url.rstrip('/')}/products/{slug}?community={community}"


def list_tools(ctx: McpContext) -> list[dict[str, Any]]:
    """返回 MCP tools/list 工具清单；个人专属工具仅在携带有效用户 Token 时展示。"""
    tools: list[dict[str, Any]] = [
        {
            "name": "list_communities",
            "title": "列出可用分区",
            "description": "列出 AniSignal 当前启用的所有游戏分区与资讯频道（代码、中文名、类型），帮助确定 community 参数。",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "search_signals",
            "title": "全站信号检索",
            "description": "按关键词、分区、来源身份（官方/社区）或平台检索最新或热门内容，支持中文与外语译文跨语言检索。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "搜索关键词，支持空格分隔多词（如 '鸣潮 维护' 或 '卡提希娅'），留空则按最新/热门返回。"},
                    "community": {"type": "string", "description": "分区代码（如 wuwa/hsr/genshin/arknights/blhx/endfield/zzz/ai），或 'all'；留空时已登录用户默认按偏好分区。"},
                    "identity": {"type": "string", "enum": ["all", "official", "community"], "description": "来源身份：all（全部）、official（仅官方）、community（仅社区）。"},
                    "platform": {"type": "string", "description": "可选平台筛选（如 bilibili/miyoushe/kuro_cn/kuro_global/youtube/x/steam/rss/xiaoheihe）。"},
                    "hours": {"type": "integer", "minimum": 1, "maximum": 720, "description": "仅看最近若干小时内发布的内容（默认 168，即 7 天）。"},
                    "sort": {"type": "string", "enum": ["time", "hot"], "description": "排序方式：time（最新）或 hot（播放量热门）。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25, "description": "返回条数上限（默认 10，最大 25）。"},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "get_content_detail",
            "title": "查看内容详情与修订对比",
            "description": "读取指定 content_id 的完整正文、外文译文、关联事件及（如有修订）官方版本 Diff 详情。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content_id": {"type": "integer", "minimum": 1, "description": "内容 ID（可从 search_signals、list_official_revisions 等结果获取）。"},
                    "include_diff": {"type": "boolean", "description": "存在多版本时是否附带最新实质版本 Diff 摘录（默认 true）。"},
                },
                "required": ["content_id"],
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "list_events",
            "title": "查询聚合事件",
            "description": "查询经过多平台证据聚类与 AI 核验的事件流，包含事件摘要、关键事实、国服/国际服同步状态与证据链。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "community": {"type": "string", "description": "分区代码或 'all'；留空时已登录用户默认按偏好分区。"},
                    "q": {"type": "string", "description": "可选关键词筛选事件标题与摘要。"},
                    "importance_only": {"type": "boolean", "description": "是否仅返回高重要度事件（默认 false）。"},
                    "hours": {"type": "integer", "minimum": 1, "maximum": 720, "description": "仅看最近若干小时内有新证据的事件（默认不限）。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数上限（默认 10，最大 20）。"},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "get_calendar",
            "title": "查询活动与维护日历",
            "description": "查询进行中、即将开始或即将截止的游戏活动、卡池、版本维护、前瞻直播与报名截止日程（北京时间）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "community": {"type": "string", "description": "分区代码或 'all'；留空时已登录用户默认按偏好分区。"},
                    "status": {"type": "string", "enum": ["all", "ongoing", "upcoming", "ending_soon"], "description": "状态筛选：all（进行中+即将开始）、ongoing（进行中）、upcoming（未开始）、ending_soon（72 小时内截止或维护）。"},
                    "kind": {"type": "string", "enum": ["all", "activity", "pool", "maintenance", "broadcast", "delay", "deadline"], "description": "日程类型筛选，默认 all。"},
                    "days_ahead": {"type": "integer", "minimum": 1, "maximum": 90, "description": "向未来查看的天数窗口（默认 30 天）。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "description": "返回条数上限（默认 15，最大 30）。"},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "list_redeem_codes",
            "title": "查询可用兑换码",
            "description": "列出当前有效的官方兑换码（自动隐藏已过期码），附截止时间与官方来源链接。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "community": {"type": "string", "description": "分区代码或 'all'；留空时已登录用户默认按偏好分区。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "description": "返回条数上限（默认 20，最大 30）。"},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "list_official_revisions",
            "title": "查询官方公告暗改（实质修订）",
            "description": "查询近期官方公告与动态的实质修订记录（改字、改时间、改奖励或换图），自动过滤风控丢图与解析重算噪音。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "community": {"type": "string", "description": "分区代码或 'all'；留空时已登录用户默认按偏好分区。"},
                    "q": {"type": "string", "description": "可选关键词过滤修订标题或变更内容。"},
                    "days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "回看最近天数（默认 14 天，最大 90 天）。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数上限（默认 10，最大 20）。"},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "get_product_fulfillment",
            "title": "查询联名周边履约进展",
            "description": "查询联名周边/硬件商品的官方预售/发售/发货说明、玩家实拍到货进展与已核验履约证据（严分玩家自购与官方送测）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "community": {"type": "string", "description": "分区代码或 'all'；留空时已登录用户默认按偏好分区。"},
                    "q": {"type": "string", "description": "按商品名或追踪词筛选（如 '键盘'、'爱弥斯'）。"},
                    "slug": {"type": "string", "description": "精确商品 slug，传入时返回该商品的完整档案与最新已核验信号。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 15, "description": "列表模式返回上限（默认 8，最大 15）。"},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "get_steam_pulse",
            "title": "查询 Steam 在线与口碑遥测",
            "description": "查询已接入游戏的 Steam 实时在线人数（CCU）、24 小时/窗口峰值、全区与简中好评率及实时价格折扣。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "community": {"type": "string", "description": "分区代码或 'all'（默认 all）。"},
                    "app_id": {"type": "integer", "minimum": 1, "description": "可选精确 Steam AppID，传入时只查该款游戏。"},
                    "hours": {"type": "integer", "minimum": 24, "maximum": 720, "description": "统计窗口小时数（默认 168，即 7 天）。"},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "get_ai_news",
            "title": "查询 AI 资讯与每日成刊",
            "description": "读取 AI 频道的每日精选成刊日报，或按关键词、话题检索近期 AI 资讯事件（保留来源与待核验标注）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["events", "edition"], "description": "events（按条件查近期 AI 事件）或 edition（读今日/指定日 AI 精选日报）。"},
                    "q": {"type": "string", "description": "events 模式可选搜索词。"},
                    "topic": {"type": "string", "description": "events 模式可选话题标签。"},
                    "selected_only": {"type": "boolean", "description": "events 模式是否仅看精选条目（默认 false）。"},
                    "day": {"type": "string", "description": "edition 模式可选日期 YYYY-MM-DD（默认今日）。"},
                    "hours": {"type": "integer", "minimum": 1, "maximum": 720, "description": "events 模式时间窗口小时数（默认 72）。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数上限（默认 10，最大 20）。"},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "get_trending",
            "title": "查询热度飙升榜",
            "description": "查询最近窗口内播放量增量最快的热门内容（需至少 2 次指标快照）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "community": {"type": "string", "description": "分区代码或 'all'；留空时已登录用户默认按偏好分区。"},
                    "hours": {"type": "integer", "minimum": 6, "maximum": 168, "description": "增量统计窗口小时数（默认 24）。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数上限（默认 10，最大 20）。"},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
    ]

    if ctx.user is not None:
        tools.append(
            {
                "name": "get_my_watch_feed",
                "title": "查询我的关注流与定制监控",
                "description": "读取当前认证账号的个人关键词关注流以及自建定制监控最新抓取条目。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "可选：只看命中的某一个已启用关注关键词。"},
                        "include_custom": {"type": "boolean", "description": "是否同时返回个人自建定制监控最新条目（默认 true）。"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数上限（默认 10，最大 20）。"},
                    },
                    "additionalProperties": False,
                },
                "annotations": READ_ONLY_ANNOTATIONS,
            }
        )
    return tools


async def _tool_list_communities(session: AsyncSession, ctx: McpContext, _args: dict) -> str:
    await ensure_communities_synced(session)
    lines = ["### AniSignal 可用分区"]
    for key, meta in COMMUNITIES.items():
        if not meta.active:
            continue
        domain_label = {"game": "游戏", "news": "AI 资讯", "feed": "社区热帖"}.get(meta.domain, meta.domain)
        lines.append(f"- `{key}` — {meta.label}（{domain_label}）")
    if ctx.user is not None:
        prefs = ctx.preferred_communities
        lines.append("")
        lines.append(f"当前账号偏好分区：{', '.join(prefs) if prefs else '未限定（全站）'}")
        if ctx.excluded_keywords:
            lines.append(f"当前账号排除词：{', '.join(ctx.excluded_keywords)}")
    return "\n".join(lines)


async def _tool_search_signals(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services import settings as settings_service
    from app.services.classify import HIDDEN_CATEGORIES
    from app.services.content_diff import load_revision_summaries
    from app.services.search import content_search_condition, supports_mysql_fts

    await ensure_communities_synced(session)
    q = _str_arg(args, "q", max_len=100)
    raw_comm = _str_arg(args, "community", max_len=40)
    identity = _str_arg(args, "identity", max_len=20) or "all"
    if identity not in {"all", "official", "community"}:
        raise McpToolError("identity 仅支持 all / official / community")
    platform = _str_arg(args, "platform", max_len=32)
    hours = _clamp_int(args.get("hours"), 168, 1, 720, "hours")
    sort = _str_arg(args, "sort", max_len=16) or "time"
    if sort not in {"time", "hot"}:
        raise McpToolError("sort 仅支持 time / hot")
    limit = _clamp_int(args.get("limit"), 10, 1, 25, "limit")

    _comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, raw_comm)
    conditions: list[Any] = [
        RawContent.hidden.is_(False),
        func.coalesce(RawContent.published_at, RawContent.collected_at) >= utcnow() - timedelta(hours=hours),
        or_(ContentVersion.category.notin_(HIDDEN_CATEGORIES), ContentVersion.category.is_(None)),
    ]
    if comm_list is not None:
        conditions.append(RawContent.community.in_(comm_list))
    elif exclude_comms:
        conditions.append(RawContent.community.notin_(exclude_comms))

    hidden_platforms = settings_service.hidden_platforms(await settings_service.get_media_visibility(session))
    if hidden_platforms:
        conditions.append(RawContent.platform.notin_(hidden_platforms))
    if platform:
        conditions.append(
            RawContent.platform.in_(("kuro_cn", "kuro_global"))
            if platform == "kuro"
            else RawContent.platform == platform
        )
    if identity == "official":
        conditions.append(Source.identity == "official")
    elif identity == "community":
        conditions.append(or_(Source.identity != "official", Source.identity.is_(None)))
    if q:
        search_cond = content_search_condition(q, use_mysql_fts=await supports_mysql_fts(session))
        if search_cond is not None:
            conditions.append(search_cond)

    stmt = (
        select(RawContent, Source.name, Source.identity, ContentVersion)
        .options(defer(RawContent.raw_json))
        .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
        .outerjoin(Source, Source.id == RawContent.source_id)
        .where(*conditions)
    )
    if sort == "hot":
        from app.models import MetricSnapshot

        latest_view = (
            select(MetricSnapshot.view_count)
            .where(MetricSnapshot.raw_content_id == RawContent.id)
            .order_by(MetricSnapshot.id.desc())
            .limit(1)
            .correlate(RawContent)
            .scalar_subquery()
        )
        stmt = stmt.order_by(func.coalesce(latest_view, 0).desc(), RawContent.id.desc())
    else:
        stmt = stmt.order_by(func.coalesce(RawContent.published_at, RawContent.collected_at).desc(), RawContent.id.desc())

    rows = list((await session.execute(stmt.limit(limit * 2))).all())
    excluded_kw = ctx.excluded_keywords
    filtered = []
    for row in rows:
        raw, src_name, src_id, ver = row
        haystack = f"{ver.title or ''} {ver.text or ''} {src_name or ''}"
        if _matches_exclude_keywords(haystack, excluded_kw):
            continue
        filtered.append(row)
        if len(filtered) >= limit:
            break

    if not filtered:
        scope_desc = ", ".join(comm_list) if comm_list else "全站"
        return f"近 {hours} 小时在范围（{scope_desc}）内未检索到匹配内容" + (f"（关键词：{q}）" if q else "") + "。这只说明 AniSignal 未收录，不代表全网没有。"

    multi_platforms = {raw.id: raw.platform for raw, _, _, ver in filtered if ver.version > 1}
    rev_map = (
        await load_revision_summaries(session, list(multi_platforms), platforms=multi_platforms)
        if multi_platforms
        else {}
    )

    lines = [f"### 检索结果（共 {len(filtered)} 条，近 {hours} 小时" + (f"，关键词：{q}" if q else "") + "）"]
    for idx, (raw, src_name, src_id, ver) in enumerate(filtered, 1):
        meta = get_community(raw.community)
        title = ver.title or raw.external_id
        role_tag = "官方" if src_id == "official" else "社区"
        rev = rev_map.get(raw.id)
        rev_tag = f" · 已修订 {rev.revision_count} 次" if rev and rev.revision_count > 0 else ""
        excerpt = _clip(ver.text, 180)
        detail = _content_url(ctx.base_url, raw.id)
        origin = safe_link(raw.url)
        lines.append(
            f"\n{idx}. **[{meta.label}·{role_tag}] {title}** (`content_id={raw.id}`{rev_tag})"
        )
        lines.append(f"   来源：{src_name or raw.platform}（{raw.platform}） · 发布：{_fmt_bj(raw.published_at or raw.collected_at)}")
        if excerpt:
            lines.append(f"   摘录：{excerpt}")
        link_parts = [f"站内详情：{detail}"]
        if origin:
            link_parts.append(f"原文：{origin}")
        lines.append("   " + " | ".join(link_parts))
    return "\n".join(lines)


async def _tool_get_content_detail(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services.content_diff import build_version_history, load_version_inputs
    from app.services.revision_notifications import diff_change_summary

    await ensure_communities_synced(session)
    content_id = _clamp_int(args.get("content_id"), 0, 1, 2**31 - 1, "content_id")
    include_diff = _bool_arg(args, "include_diff", True)

    row = (
        await session.execute(
            select(RawContent, Source, ContentVersion)
            .options(defer(RawContent.raw_json))
            .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
            .outerjoin(Source, Source.id == RawContent.source_id)
            .where(RawContent.id == content_id, RawContent.hidden.is_(False))
        )
    ).first()
    if row is None:
        raise McpToolError(f"未找到内容 content_id={content_id}（不存在或已隐藏）。")

    raw, source, version = row
    meta = get_community(raw.community)
    title = version.title or raw.external_id
    role = "官方" if source and source.identity == "official" else "社区"
    lines = [
        f"### [{meta.label}·{role}] {title}",
        f"- `content_id`: {raw.id} · 版本 v{version.version}",
        f"- 来源：{source.name if source else raw.platform}（{raw.platform} / {raw.content_type}）",
        f"- 发布时间：{_fmt_bj(raw.published_at)} · 入库：{_fmt_bj(raw.collected_at)}",
        f"- 站内链接：{_content_url(ctx.base_url, raw.id)}",
    ]
    origin = safe_link(raw.url)
    if origin:
        lines.append(f"- 源站原文：{origin}")

    body = _clip(version.text, 2400)
    if body:
        lines.extend(["", "#### 正文", body])

    trans = await session.get(ContentTranslation, raw.id)
    if trans and trans.text:
        lines.extend(["", f"#### 译文（{trans.lang}）", _clip(trans.text, 1600)])

    ev_pair = (
        await session.execute(
            select(Event)
            .join(Evidence, Evidence.event_id == Event.id)
            .where(Evidence.raw_content_id == raw.id, Event.is_active.is_(True), Event.category != "ai_news")
            .limit(1)
        )
    ).scalar_one_or_none()
    if ev_pair is not None:
        lines.extend([
            "",
            f"#### 所属聚合事件：`event_id={ev_pair.id}` — {ev_pair.title}",
            f"- 链接：{_event_url(ctx.base_url, ev_pair.id)}",
        ])
        if ev_pair.summary:
            lines.append(f"- 摘要：{_clip(ev_pair.summary, 300)}")

    if include_diff and version.version > 1:
        vlist = (await load_version_inputs(session, [raw.id])).get(raw.id, [])
        history = build_version_history(raw.id, vlist, platform=raw.platform)
        if history["revision_count"] > 0 and history.get("diff"):
            diff_obj = history["diff"]
            change_label, snippets = diff_change_summary(diff_obj, max_lines=8)
            lines.extend([
                "",
                f"#### 官方版本修订对比（共 {history['revision_count']} 次实质修订 · v{diff_obj['from_version']}→v{diff_obj['to_version']}）",
                f"- 变更概览：{change_label}",
                f"- 对比直达：{_content_url(ctx.base_url, raw.id, anchor='#revisions')}",
            ])
            for snippet in snippets:
                lines.append(f"  - {snippet}")

    return "\n".join(lines)


async def _tool_list_events(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services.search import event_search_condition, supports_mysql_fts
    from app.services.views import events_to_out

    await ensure_communities_synced(session)
    raw_comm = _str_arg(args, "community", max_len=40)
    q = _str_arg(args, "q", max_len=100)
    importance_only = _bool_arg(args, "importance_only", False)
    hours_raw = args.get("hours")
    hours = _clamp_int(hours_raw, 0, 1, 720, "hours") if hours_raw not in (None, "") else None
    limit = _clamp_int(args.get("limit"), 10, 1, 20, "limit")

    _comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, raw_comm)
    conditions: list[Any] = [Event.is_active.is_(True), Event.category != "ai_news"]
    if comm_list is not None:
        conditions.append(Event.community.in_(comm_list))
    elif exclude_comms:
        conditions.append(Event.community.notin_(exclude_comms))
    if importance_only:
        conditions.append(Event.importance.is_(True))
    if hours is not None:
        cutoff = utcnow() - timedelta(hours=hours)
        recent_ids = (
            select(Evidence.event_id)
            .join(RawContent, RawContent.id == Evidence.raw_content_id)
            .where(RawContent.published_at >= cutoff, Evidence.role != "excluded", RawContent.hidden.is_(False))
        )
        conditions.append(Event.id.in_(recent_ids))
    if q:
        search_cond = event_search_condition(q, use_mysql_fts=await supports_mysql_fts(session))
        if search_cond is not None:
            conditions.append(search_cond)

    latest_ev_sq = (
        select(
            Evidence.event_id.label("event_id"),
            func.max(func.coalesce(RawContent.published_at, RawContent.collected_at)).label("latest_at"),
        )
        .join(RawContent, RawContent.id == Evidence.raw_content_id)
        .where(Evidence.role != "excluded", RawContent.hidden.is_(False))
        .group_by(Evidence.event_id)
        .subquery()
    )
    events = list(
        await session.scalars(
            select(Event)
            .join(latest_ev_sq, latest_ev_sq.c.event_id == Event.id)
            .where(*conditions)
            .order_by(latest_ev_sq.c.latest_at.desc(), Event.importance.desc(), Event.id.desc())
            .limit(limit * 2)
        )
    )
    excluded_kw = ctx.excluded_keywords
    events = [
        ev for ev in events
        if not _matches_exclude_keywords(f"{ev.title} {ev.summary or ''}", excluded_kw)
    ][:limit]
    if not events:
        return "当前条件下没有找到匹配的聚合事件。"

    outs = await events_to_out(session, events)
    lines = [f"### 聚合事件（共 {len(outs)} 条）"]
    for idx, ev in enumerate(outs, 1):
        meta = get_community(ev.community)
        star = "★ 重要 · " if ev.importance else ""
        lines.append(f"\n{idx}. **[{meta.label}] {star}{ev.title}** (`event_id={ev.id}` · {ev.category} · 证据 {ev.evidence_count} 条)")
        if ev.summary:
            lines.append(f"   摘要：{_clip(ev.summary, 240)}")
        if ev.facts:
            fact_bits = []
            for entry in ev.facts[:4]:
                if isinstance(entry, dict):
                    label = entry.get("label") or entry.get("key") or entry.get("name") or ""
                    val = entry.get("value") or entry.get("text") or ""
                    if label and val:
                        fact_bits.append(f"{label}：{val}")
                    elif val:
                        fact_bits.append(str(val))
                elif entry:
                    fact_bits.append(str(entry))
            if fact_bits:
                lines.append("   关键事实：" + "；".join(fact_bits))
        lines.append(f"   更新：{_fmt_bj(ev.updated_at)} · 链接：{_event_url(ctx.base_url, ev.id)}")
    return "\n".join(lines)


async def _tool_get_calendar(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services.calendar import calendar_entries

    await ensure_communities_synced(session)
    raw_comm = _str_arg(args, "community", max_len=40)
    status_filter = _str_arg(args, "status", max_len=20) or "all"
    if status_filter not in {"all", "ongoing", "upcoming", "ending_soon"}:
        raise McpToolError("status 仅支持 all / ongoing / upcoming / ending_soon")
    kind_filter = _str_arg(args, "kind", max_len=20) or "all"
    if kind_filter not in {"all", "activity", "pool", "maintenance", "broadcast", "delay", "deadline"}:
        raise McpToolError("kind 参数无效")
    days_ahead = _clamp_int(args.get("days_ahead"), 30, 1, 90, "days_ahead")
    limit = _clamp_int(args.get("limit"), 15, 1, 30, "limit")

    comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, raw_comm)
    now = utcnow()
    entries = await calendar_entries(
        session,
        comm_slug,
        now=now,
        window_start=now - timedelta(days=14),
        window_end=now + timedelta(days=days_ahead),
        exclude=exclude_comms,
    )
    excluded_kw = ctx.excluded_keywords
    matched = []
    for entry in entries:
        if comm_list is not None and entry["community"] not in comm_list:
            continue
        if kind_filter != "all" and entry["kind"] != kind_filter:
            continue
        if _matches_exclude_keywords(entry["title"], excluded_kw):
            continue
        start, end = entry["start_at"], entry.get("end_at")
        is_upcoming = start > now
        is_ongoing = (start <= now) and (end is not None and end > now) and entry["kind"] != "deadline"
        if not (is_upcoming or is_ongoing):
            continue
        if status_filter == "ongoing" and not is_ongoing:
            continue
        if status_filter == "upcoming" and not is_upcoming:
            continue
        if status_filter == "ending_soon":
            deadline_ts = end if is_ongoing else start
            if deadline_ts is None or deadline_ts > now + timedelta(hours=72):
                continue
        matched.append((is_ongoing, entry))

    matched.sort(key=lambda pair: (pair[1]["end_at"] if pair[0] else pair[1]["start_at"], pair[1]["id"]))
    matched = matched[:limit]
    if not matched:
        return "当前时间窗口内暂无符合条件的活动或维护日程。"

    lines = [f"### 活动与维护日历（北京时间 · 当前 {_fmt_bj(now)}）"]
    for idx, (is_ongoing, entry) in enumerate(matched, 1):
        meta = get_community(entry["community"])
        kind_label = _SCHEDULE_KIND_LABELS.get(entry["kind"], entry["kind"])
        fmt = "%Y-%m-%d" if entry.get("all_day") else "%Y-%m-%d %H:%M"
        if is_ongoing:
            when = f"进行中 · 截止 {_fmt_bj(entry['end_at'], fmt)}"
        else:
            verb = "截止" if entry["kind"] == "deadline" else "开始"
            when = f"未开始 · {_fmt_bj(entry['start_at'], fmt)} {verb}"
            if entry.get("end_at"):
                when += f" ~ {_fmt_bj(entry['end_at'], fmt)}"
        delay_note = "（已延期/时间调整）" if entry.get("delayed") else ""
        lines.append(f"\n{idx}. **[{meta.label}·{kind_label}] {entry['title']}**{delay_note}")
        lines.append(f"   时间：{when}")
        origin = safe_link(entry.get("url"))
        detail = _content_url(ctx.base_url, entry["content_id"]) if entry.get("content_id") else ""
        links = [x for x in (f"详情：{detail}" if detail else "", f"来源：{origin}" if origin else "") if x]
        if links:
            lines.append("   " + " | ".join(links))
    return "\n".join(lines)


async def _tool_list_redeem_codes(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services.redeem_codes import active_redeem_code_conditions

    await ensure_communities_synced(session)
    raw_comm = _str_arg(args, "community", max_len=40)
    limit = _clamp_int(args.get("limit"), 20, 1, 30, "limit")
    _comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, raw_comm)

    now = utcnow()
    conditions: list[Any] = active_redeem_code_conditions(now)
    if comm_list is not None:
        conditions.append(RedeemCode.community.in_(comm_list))
    elif exclude_comms:
        conditions.append(RedeemCode.community.notin_(exclude_comms))

    rows = list(
        (
            await session.execute(
                select(RedeemCode, RawContent.id, RawContent.url, ContentVersion.title)
                .join(RawContent, RawContent.id == RedeemCode.raw_content_id)
                .outerjoin(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
                .where(*conditions)
                .order_by(
                    (RedeemCode.source_kind == "official").desc(),
                    RedeemCode.source_count.desc(),
                    RedeemCode.created_at.desc(),
                    RedeemCode.id.desc(),
                )
                .limit(limit)
            )
        ).all()
    )
    if not rows:
        return "当前范围内暂无可用兑换码。"

    lines = [f"### 当前可用兑换码（共 {len(rows)} 个 · 更新于 {_fmt_bj(now)}）"]
    for idx, (code, raw_id, url, title) in enumerate(rows, 1):
        meta = get_community(code.community)
        badges: list[str] = []
        if code.server == "cn":
            badges.append("国服")
        elif code.server == "global":
            badges.append("国际服")
        if code.source_kind == "official":
            badges.append("官方")
        else:
            badges.append(f"×{code.source_count} 确认" if (code.source_count or 1) >= 2 else "社区整理")
        badge_str = f" [{' · '.join(badges)}]" if badges else ""
        reward_str = f" · {code.reward}" if code.reward else ""
        expiry = (
            f"{'预计截止' if code.expiry_estimated else '截止'}：{_fmt_bj(code.expires_at)}"
            if code.expires_at
            else "未注明截止时间（14天保鲜期）"
        )
        origin = safe_link(url) or _content_url(ctx.base_url, raw_id)
        note = f"（来源：{_clip(title, 40)}）" if title else ""
        lines.append(f"{idx}. `[{meta.label}]` **`{code.code}`**{badge_str}{reward_str} — {expiry}{note} · {origin}")
    return "\n".join(lines)


async def _tool_list_official_revisions(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services.revision_notifications import load_official_revision_events

    await ensure_communities_synced(session)
    raw_comm = _str_arg(args, "community", max_len=40)
    q = _str_arg(args, "q", max_len=100)
    days = _clamp_int(args.get("days"), 14, 1, 90, "days")
    limit = _clamp_int(args.get("limit"), 10, 1, 20, "limit")
    _comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, raw_comm)

    active_comms = comm_list
    if active_comms is None and exclude_comms:
        active_comms = [k for k in COMMUNITIES if k not in exclude_comms]

    events, _ = await load_official_revision_events(
        session,
        since_parsed_at=utcnow() - timedelta(days=days),
        communities=active_comms,
        exclude_keywords=ctx.excluded_keywords,
        limit=120,
    )
    if q:
        terms = [t.casefold() for t in q.split() if t.strip()]
        events = [
            it for it in events
            if all(
                term in " ".join([it["title"], it.get("text") or "", " ".join(it.get("change_lines") or [])]).casefold()
                for term in terms
            )
        ]
    events = events[:limit]
    if not events:
        return f"近 {days} 天内未检测到符合条件的官方公告实质修订" + (f"（关键词：{q}）" if q else "") + "。"

    lines = [f"### 官方公告实质修订（近 {days} 天，共 {len(events)} 条）"]
    for idx, it in enumerate(events, 1):
        lines.append(
            f"\n{idx}. **[{it['community_label']}] {it['title']}** (`content_id={it['content_id']}` · v{it['from_version']}→v{it['to_version']} · {it['change_label']})"
        )
        lines.append(f"   来源：{it['source']} · 修订于：{_fmt_bj(it['parsed_at'])}")
        for snippet in (it.get("change_lines") or [])[:4]:
            lines.append(f"   - {snippet}")
        lines.append(f"   完整对比：{it['detail_url']}")
    return "\n".join(lines)


async def _tool_get_product_fulfillment(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services.fulfillment import list_product_signals
    from app.services.views import product_to_out, products_to_out

    await ensure_communities_synced(session)
    slug = _str_arg(args, "slug", max_len=100)
    q = _str_arg(args, "q", max_len=100)
    raw_comm = _str_arg(args, "community", max_len=40)
    limit = _clamp_int(args.get("limit"), 8, 1, 15, "limit")

    if slug:
        product = await session.scalar(select(Product).where(Product.slug == slug))
        if product is None:
            raise McpToolError(f"未找到商品档案 slug='{slug}'。")
        out = await product_to_out(session, product)
        signals = await list_product_signals(session, product.id, verified_only=True, include_notes=False)
        meta = get_community(product.community)
        lines = [
            f"### [{meta.label}] {out.name} (`slug={out.slug}`)",
            f"- 追踪状态：{out.monitor_status} · 站内页面：{_product_url(ctx.base_url, out.slug, out.community)}",
            f"- 官方说明：{out.official_status}",
            f"- 玩家实拍/到货进展：{out.consumer_status}",
        ]
        if out.latest_candidate:
            lines.append(f"- 待核验线索（未证实）：{out.latest_candidate.get('summary')}")
        if signals:
            lines.append("\n#### 已核验履约信号（最新 8 条）")
            for sig in signals[:8]:
                acq = f"/{sig['acquisition_type']}" if sig.get("acquisition_type") and sig["acquisition_type"] != "unknown" else ""
                sig_url = safe_link(sig.get("url")) or ""
                lines.append(
                    f"- [{sig.get('signal_label') or sig.get('signal_type')}{acq}] {sig.get('summary') or ''}（{_fmt_bj(sig.get('occurred_at'))}"
                    + (f" · {sig_url}" if sig_url else "")
                    + "）"
                )
        return "\n".join(lines)

    _comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, raw_comm)
    stmt = select(Product).order_by(Product.id.desc())
    if comm_list is not None:
        stmt = stmt.where(Product.community.in_(comm_list))
    elif exclude_comms:
        stmt = stmt.where(Product.community.notin_(exclude_comms))
    products = list(await session.scalars(stmt))
    if q:
        terms = [t.casefold() for t in q.split() if t.strip()]
        products = [
            p for p in products
            if all(term in f"{p.name} {p.slug} {' '.join(p.watch_terms or [])}".casefold() for term in terms)
        ]
    products = products[:limit]
    if not products:
        return "没有找到匹配的周边/商品履约档案。"

    outs = await products_to_out(session, products)
    lines = [f"### 周边/商品履约进展（共 {len(outs)} 款）"]
    for idx, out in enumerate(outs, 1):
        meta = get_community(out.community)
        lines.append(f"\n{idx}. **[{meta.label}] {out.name}** (`slug={out.slug}`)")
        lines.append(f"   官方：{out.official_status}")
        lines.append(f"   用户到货：{out.consumer_status}")
        if out.latest_candidate:
            lines.append(f"   待核验：{out.latest_candidate.get('summary')}")
        lines.append(f"   详情：{_product_url(ctx.base_url, out.slug, out.community)}")
    return "\n".join(lines)


async def _tool_get_steam_pulse(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services.steam import get_steam_app_pulse, get_steam_pulse

    await ensure_communities_synced(session)
    app_id_raw = args.get("app_id")
    hours = _clamp_int(args.get("hours"), 168, 24, 720, "hours")

    if app_id_raw not in (None, ""):
        app_id = _clamp_int(app_id_raw, 0, 1, 2**31 - 1, "app_id")
        app = await get_steam_app_pulse(session, app_id, hours=hours)
        if app is None:
            return f"未找到 Steam AppID={app_id} 的遥测快照。"
        apps = [app]
    else:
        raw_comm = _str_arg(args, "community", max_len=40) or "all"
        comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, raw_comm)
        pulse = await get_steam_pulse(session, comm_slug, hours=hours, exclude_communities=exclude_comms)
        apps = pulse.get("apps") or []
        if comm_list is not None:
            apps = [a for a in apps if a.get("community") in comm_list]

    if not apps:
        return "当前范围内暂无 Steam 遥测快照数据。"

    lines = [f"### Steam 实时在线与口碑遥测（窗口 {hours} 小时，共 {len(apps)} 款）"]
    for idx, app in enumerate(apps[:20], 1):
        comm_label = get_community(app["community"]).label if app.get("community") else "定制"
        ccu = f"{app['player_count']:,}" if app.get("player_count") is not None else "未知"
        peak24 = f"{app['peak_24h']:,}" if app.get("peak_24h") is not None else "-"
        peak_w = f"{app['peak_window']:,}" if app.get("peak_window") is not None else "-"
        g_rate = f"{app['global_rate']}%（{app.get('global_score_label') or ''}）" if app.get("global_rate") is not None else "暂无"
        cn_rate = f"{app['cn_rate']}%" if app.get("cn_rate") is not None else "暂无"
        price = app.get("price_formatted") or "免费/未收录"
        discount = f" -{app['discount_percent']}%" if app.get("discount_percent") else ""
        lines.append(f"\n{idx}. **[{comm_label}] {app['name']}** (`app_id={app['app_id']}`)")
        lines.append(f"   在线：{ccu} · 24h 峰值：{peak24} · {hours}h 峰值：{peak_w}")
        lines.append(f"   口碑：全区 {g_rate} · 简中 {cn_rate} · 价格：{price}{discount}")
        lines.append(f"   商店：{app['store_url']} · 采样：{app['captured_at']}")
    return "\n".join(lines)


async def _tool_get_ai_news(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services import aihot
    from app.services.news_publication import list_stories, read_news_edition

    mode = _str_arg(args, "mode", max_len=16) or "events"
    if mode not in {"events", "edition"}:
        raise McpToolError("mode 仅支持 events 或 edition")
    limit = _clamp_int(args.get("limit"), 10, 1, 20, "limit")

    if mode == "edition":
        day_str = _str_arg(args, "day", max_len=16)
        target_day: date | None = None
        if day_str:
            try:
                target_day = date.fromisoformat(day_str)
            except ValueError as exc:
                raise McpToolError("day 应为 YYYY-MM-DD 格式") from exc
        data = await aihot.edition(target_day) if aihot.enabled() else await read_news_edition(session, day=target_day)
        items = (data.get("items") or [])[:limit]
        if not items:
            return f"暂无 {day_str or '今日'} AI 精选日报成刊数据。"
        lines = [f"### AI 精选日报（{data.get('day') or day_str or '最新'}，共 {len(items)} 条）"]
        for idx, it in enumerate(items, 1):
            headline = it.get("headline") or it.get("title") or "未命名"
            summary = _clip(it.get("summary"), 220)
            lines.append(f"\n{idx}. **{headline}**")
            if summary:
                lines.append(f"   {summary}")
            srcs = it.get("sources") or []
            if srcs:
                src = srcs[0]
                lines.append(f"   来源：{src.get('source_name') or src.get('platform') or ''} · {safe_link(src.get('url')) or ''}")
        return "\n".join(lines)

    q = _str_arg(args, "q", max_len=100)
    topic = _str_arg(args, "topic", max_len=32)
    selected_only = _bool_arg(args, "selected_only", False)
    hours = _clamp_int(args.get("hours"), 72, 1, 720, "hours")

    if aihot.enabled():
        res = await aihot.feed(q=q, topic=topic, hours=hours, selected=selected_only, limit=limit, offset=0)
        items = res.get("items") or []
    else:
        items = (await list_stories(session, q=q, topic=topic, hours=hours, selected=selected_only))[:limit]

    if not items:
        return f"近 {hours} 小时暂无匹配的 AI 资讯事件。"

    lines = [f"### AI 资讯事件（近 {hours} 小时，共 {len(items)} 条；均为外部报道转述，请保留出处）"]
    for idx, it in enumerate(items, 1):
        headline = it.get("headline") or it.get("title") or "未命名"
        kind_tag = "传闻/待核验" if it.get("kind") == "rumor" else "报道"
        lines.append(f"\n{idx}. **[{kind_tag}] {headline}**")
        if it.get("summary"):
            lines.append(f"   {_clip(it['summary'], 220)}")
        url = safe_link(it.get("url")) or (
            _content_url(ctx.base_url, it["id"]) if isinstance(it.get("id"), int) else ""
        )
        ts = it.get("latest_at") or it.get("published_at")
        ts_str = _fmt_bj(ts) if isinstance(ts, datetime) else str(ts or "")
        lines.append(f"   时间：{ts_str} · 链接：{url}")
    return "\n".join(lines)


async def _tool_get_trending(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    from app.services.curation import select_trending

    await ensure_communities_synced(session)
    raw_comm = _str_arg(args, "community", max_len=40)
    hours = _clamp_int(args.get("hours"), 24, 6, 168, "hours")
    limit = _clamp_int(args.get("limit"), 10, 1, 20, "limit")
    comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, raw_comm)

    excluded_kw = ctx.excluded_keywords

    def _item_filter(item: dict) -> bool:
        if comm_list is not None and item.get("community") not in comm_list:
            return False
        return not _matches_exclude_keywords(f"{item.get('title') or ''} {item.get('source_name') or ''}", excluded_kw)

    items = await select_trending(
        session,
        limit,
        community=comm_slug,
        hours=hours,
        exclude=exclude_comms,
        item_filter=_item_filter,
    )
    if not items:
        return f"近 {hours} 小时窗口内暂无明显播放量飙升内容。"

    lines = [f"### 热度飙升榜（近 {hours} 小时播放增量，共 {len(items)} 条）"]
    for idx, it in enumerate(items, 1):
        meta = get_community(it.get("community"))
        views = int((it.get("metrics") or {}).get("view") or 0)
        delta = int(it.get("delta_view") or 0)
        detail = _content_url(ctx.base_url, it["id"])
        origin = safe_link(it.get("url"))
        lines.append(f"\n{idx}. **[{meta.label}] {it['title']}** (`content_id={it['id']}`)")
        lines.append(f"   {hours}h 播放增量：+{delta:,} · 当前总播放：{views:,} · 发布：{_fmt_bj(it.get('published_at'))}")
        lines.append(f"   详情：{detail}" + (f" | 原文：{origin}" if origin else ""))
    return "\n".join(lines)


async def _tool_get_my_watch_feed(session: AsyncSession, ctx: McpContext, args: dict) -> str:
    if ctx.user is None:
        raise McpToolError("该工具需要个人认证：请在 MCP 地址或 Authorization Header 中附带账号中心的个人令牌。")

    await ensure_communities_synced(session)
    kw_filter = _str_arg(args, "keyword", max_len=64)
    include_custom = _bool_arg(args, "include_custom", True)
    limit = _clamp_int(args.get("limit"), 10, 1, 20, "limit")

    keywords = list(
        await session.scalars(
            select(UserKeyword)
            .where(UserKeyword.user_id == ctx.user.id, UserKeyword.enabled.is_(True))
            .order_by(UserKeyword.id.desc())
        )
    )
    if kw_filter:
        keywords = [k for k in keywords if k.keyword.casefold() == kw_filter.casefold()]

    sections: list[str] = []
    if keywords:
        title_conds = [func.lower(ContentVersion.title).contains(k.keyword.lower(), autoescape=True) for k in keywords]
        rows = list(
            (
                await session.execute(
                    select(RawContent, Source.name, ContentVersion)
                    .options(defer(RawContent.raw_json))
                    .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
                    .outerjoin(Source, Source.id == RawContent.source_id)
                    .where(
                        or_(*title_conds),
                        RawContent.hidden.is_(False),
                        *preference_conditions(ctx.user.feed_preferences),
                    )
                    .order_by(RawContent.collected_at.desc(), RawContent.id.desc())
                    .limit(limit)
                )
            ).all()
        )
        kw_lines = [f"### 我的关键词关注流（启用词：{', '.join(k.keyword for k in keywords)}）"]
        if not rows:
            kw_lines.append("暂无命中关注词的新内容。")
        for idx, (raw, src_name, ver) in enumerate(rows, 1):
            meta = get_community(raw.community)
            title = ver.title or raw.external_id
            hits = [k.keyword for k in keywords if k.keyword.lower() in title.lower()]
            kw_lines.append(
                f"{idx}. **[{meta.label}] {title}** (`content_id={raw.id}` · 命中：{', '.join(hits)})"
            )
            kw_lines.append(
                f"   来源：{src_name or raw.platform} · 发布：{_fmt_bj(raw.published_at or raw.collected_at)} · {_content_url(ctx.base_url, raw.id)}"
            )
        sections.append("\n".join(kw_lines))
    else:
        sections.append("### 我的关键词关注流\n尚未添加或启用匹配的关注关键词。")

    if include_custom:
        feeds = list(
            await session.scalars(
                select(CustomFeed)
                .where(CustomFeed.user_id == ctx.user.id, CustomFeed.enabled.is_(True), CustomFeed.status == "active")
                .order_by(CustomFeed.id.desc())
            )
        )
        if feeds:
            feed_map = {f.id: f for f in feeds}
            c_items = list(
                await session.scalars(
                    select(CustomFeedItem)
                    .where(CustomFeedItem.feed_id.in_(list(feed_map)))
                    .order_by(func.coalesce(CustomFeedItem.published_at, CustomFeedItem.collected_at).desc(), CustomFeedItem.id.desc())
                    .limit(limit)
                )
            )
            c_lines = [f"### 我的定制监控（共 {len(feeds)} 个启用源）"]
            if not c_items:
                c_lines.append("定制监控暂无抓取条目。")
            for idx, item in enumerate(c_items, 1):
                feed = feed_map[item.feed_id]
                url = safe_link(item.url) or ""
                c_lines.append(
                    f"{idx}. **[{feed.name}] {item.title or item.external_id}** · {_fmt_bj(item.published_at or item.collected_at)} · {url}"
                )
            sections.append("\n".join(c_lines))

    return "\n\n".join(sections)


_TOOL_HANDLERS = {
    "list_communities": _tool_list_communities,
    "search_signals": _tool_search_signals,
    "get_content_detail": _tool_get_content_detail,
    "list_events": _tool_list_events,
    "get_calendar": _tool_get_calendar,
    "list_redeem_codes": _tool_list_redeem_codes,
    "list_official_revisions": _tool_list_official_revisions,
    "get_product_fulfillment": _tool_get_product_fulfillment,
    "get_steam_pulse": _tool_get_steam_pulse,
    "get_ai_news": _tool_get_ai_news,
    "get_trending": _tool_get_trending,
    "get_my_watch_feed": _tool_get_my_watch_feed,
}


async def call_tool(
    session: AsyncSession, ctx: McpContext, name: str, arguments: dict[str, Any] | None
) -> dict[str, Any]:
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return {
            "content": [{"type": "text", "text": f"未知工具：{name}"}],
            "isError": True,
        }
    args = arguments if isinstance(arguments, dict) else {}
    try:
        text = await handler(session, ctx, args)
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }
    except McpToolError as exc:
        return {
            "content": [{"type": "text", "text": str(exc)}],
            "isError": True,
        }


# ---------- MCP Resources ----------


def list_resources(ctx: McpContext) -> list[dict[str, Any]]:
    resources = [
        {
            "uri": "anisignal://communities",
            "name": "communities",
            "title": "可用游戏与资讯分区目录",
            "description": "站内所有启用分区的 slug、中文名称与领域类型。",
            "mimeType": "application/json",
        },
        {
            "uri": "anisignal://highlights",
            "name": "highlights",
            "title": "跨分区今日精选信号",
            "description": "官方实质动态与高热度社区内容混排精选。",
            "mimeType": "application/json",
        },
        {
            "uri": "anisignal://calendar",
            "name": "calendar",
            "title": "近期活动与维护日程",
            "description": "正在进行与即将开始的活动、卡池、维护与截止时间。",
            "mimeType": "application/json",
        },
        {
            "uri": "anisignal://codes",
            "name": "codes",
            "title": "当前有效兑换码",
            "description": "未过期的官方兑换码清单与来源链接。",
            "mimeType": "application/json",
        },
        {
            "uri": "anisignal://revisions",
            "name": "revisions",
            "title": "近 14 天官方公告修订（抓暗改）",
            "description": "官方原文实质修订记录及关键增删摘录。",
            "mimeType": "application/json",
        },
    ]
    if ctx.user is not None:
        resources.append(
            {
                "uri": "anisignal://account/preferences",
                "name": "account_preferences",
                "title": "我的账号订阅偏好与关注词",
                "description": "当前认证账号的偏好分区、排除词、启用关键词与定制监控列表（不含敏感凭据）。",
                "mimeType": "application/json",
            }
        )
    return resources


async def read_resource(session: AsyncSession, ctx: McpContext, uri: str) -> dict[str, Any]:
    await ensure_communities_synced(session)

    if uri == "anisignal://communities":
        payload = [
            {"slug": key, "label": meta.label, "domain": meta.domain}
            for key, meta in COMMUNITIES.items()
            if meta.active
        ]
    elif uri == "anisignal://highlights":
        from app.services.curation import select_highlights

        comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, "")
        items = await select_highlights(session, 8, community=comm_slug, exclude=exclude_comms)
        if comm_list is not None:
            items = [it for it in items if it.get("community") in comm_list]
        payload = [
            {
                "content_id": it["id"],
                "community": it.get("community"),
                "title": it.get("title"),
                "identity": it.get("identity"),
                "source": it.get("source_name"),
                "published_at": it["published_at"].isoformat() if isinstance(it.get("published_at"), datetime) else None,
                "url": safe_link(it.get("url")),
                "detail_url": _content_url(ctx.base_url, it["id"]),
            }
            for it in items
        ]
    elif uri == "anisignal://calendar":
        from app.services.calendar import calendar_groups

        comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, "")
        groups = await calendar_groups(session, comm_slug, exclude=exclude_comms)
        if comm_list is not None:
            groups = {k: [e for e in v if e.get("community") in comm_list] for k, v in groups.items()}
        payload = {
            group: [
                {
                    "id": e["id"],
                    "community": e["community"],
                    "kind": e["kind"],
                    "title": e["title"],
                    "start_at": e["start_at"].isoformat() if isinstance(e.get("start_at"), datetime) else None,
                    "end_at": e["end_at"].isoformat() if isinstance(e.get("end_at"), datetime) else None,
                    "all_day": e.get("all_day"),
                    "delayed": e.get("delayed"),
                    "url": safe_link(e.get("url")),
                }
                for e in rows
            ]
            for group, rows in groups.items()
        }
    elif uri == "anisignal://codes":
        from app.services.redeem_codes import active_redeem_code_conditions

        now = utcnow()
        _comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, "")
        conds = active_redeem_code_conditions(now)
        if comm_list is not None:
            conds.append(RedeemCode.community.in_(comm_list))
        elif exclude_comms:
            conds.append(RedeemCode.community.notin_(exclude_comms))
        rows = (
            await session.execute(
                select(RedeemCode, RawContent.url)
                .join(RawContent, RawContent.id == RedeemCode.raw_content_id)
                .where(*conds)
                .order_by(
                    (RedeemCode.source_kind == "official").desc(),
                    RedeemCode.source_count.desc(),
                    RedeemCode.created_at.desc(),
                )
                .limit(30)
            )
        ).all()
        payload = [
            {
                "code": code.code,
                "community": code.community,
                "expires_at": code.expires_at.isoformat() if code.expires_at else None,
                "expiry_estimated": bool(code.expiry_estimated),
                "source_kind": code.source_kind or "official",
                "source_count": code.source_count or 1,
                "server": code.server,
                "reward": code.reward,
                "url": safe_link(url),
            }
            for code, url in rows
        ]
    elif uri == "anisignal://revisions":
        from app.services.revision_notifications import load_official_revision_events

        _comm_slug, comm_list, exclude_comms = _resolve_community_scope(ctx, "")
        active_comms = comm_list or ([k for k in COMMUNITIES if k not in exclude_comms] if exclude_comms else None)
        events, _ = await load_official_revision_events(
            session,
            since_parsed_at=utcnow() - timedelta(days=14),
            communities=active_comms,
            exclude_keywords=ctx.excluded_keywords,
            limit=30,
        )
        payload = [
            {
                "content_id": it["content_id"],
                "community": it["community"],
                "title": it["title"],
                "source": it["source"],
                "from_version": it["from_version"],
                "to_version": it["to_version"],
                "change_label": it["change_label"],
                "change_lines": it["change_lines"],
                "parsed_at": it["parsed_at"].isoformat(),
                "detail_url": it["detail_url"],
            }
            for it in events[:20]
        ]
    elif uri == "anisignal://account/preferences":
        if ctx.user is None:
            raise McpToolError("需要个人认证 Token 才能读取账号偏好资源。")
        keywords = list(
            await session.scalars(
                select(UserKeyword.keyword).where(UserKeyword.user_id == ctx.user.id, UserKeyword.enabled.is_(True))
            )
        )
        feeds = list(
            await session.scalars(
                select(CustomFeed).where(
                    CustomFeed.user_id == ctx.user.id, CustomFeed.enabled.is_(True), CustomFeed.status == "active"
                )
            )
        )
        payload = {
            "display_name": ctx.user.display_name,
            "preferred_communities": ctx.preferred_communities,
            "exclude_keywords": ctx.excluded_keywords,
            "keywords": keywords,
            "custom_feeds": [{"id": f.id, "name": f.name, "platform": f.platform, "kind": f.kind} for f in feeds],
        }
    else:
        raise McpToolError(f"未知资源 URI：{uri}")

    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ]
    }


# ---------- MCP Prompts ----------


def list_prompts() -> list[dict[str, Any]]:
    return [
        {
            "name": "daily_briefing",
            "title": "今日情报简报",
            "description": "汇总近 24 小时官方重要事件、快截止活动、可用兑换码与官方暗改，生成结构化简报。",
            "arguments": [
                {"name": "community", "description": "可选分区代码（如 wuwa / hsr / all），留空则按偏好或全站。", "required": False},
            ],
        },
        {
            "name": "check_deadlines_and_codes",
            "title": "防漏提醒：临期活动与兑换码",
            "description": "排查未来 72 小时即将截止的活动、卡池、维护时间以及当前全部有效兑换码。",
            "arguments": [
                {"name": "community", "description": "可选分区代码，留空则按偏好或全站。", "required": False},
            ],
        },
        {
            "name": "audit_official_revisions",
            "title": "官方暗改盘点",
            "description": "检查近期官方公告实质修订记录，提炼时间、奖励或规则改动的要点。",
            "arguments": [
                {"name": "community", "description": "可选分区代码，留空则按偏好或全站。", "required": False},
            ],
        },
    ]


def get_prompt(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    community = str(args.get("community") or "").strip()
    scope_hint = f"限定分区 `{community}`" if community else "按当前偏好或全站范围"

    if name == "daily_briefing":
        text = (
            f"请使用 AniSignal 工具（{scope_hint}）依次调用 `list_events`（近 24 小时）、"
            "`get_calendar`（status=ending_soon）、`list_redeem_codes` 和 `list_official_revisions`（近 3 天），"
            "为我整理一份简洁清晰的今日情报简报。要求：保留来源与北京时间，区分官方已证实与待核验信息，给出可点击链接。"
        )
    elif name == "check_deadlines_and_codes":
        text = (
            f"请使用 AniSignal 工具（{scope_hint}）调用 `get_calendar`（status=ending_soon）和 `list_redeem_codes`，"
            "按紧急程度列出 72 小时内要截止的活动、卡池、维护节点和目前还能用的兑换码，标清北京时间。"
        )
    elif name == "audit_official_revisions":
        text = (
            f"请使用 AniSignal 工具（{scope_hint}）调用 `list_official_revisions`（近 14 天），"
            "如有值得关注的重磅公告再调用 `get_content_detail` 查看完整 Diff，总结官方近期悄悄改了哪些时间、奖励或规则。"
        )
    else:
        raise McpToolError(f"未知提示词模板：{name}")

    return {
        "description": next(p["description"] for p in list_prompts() if p["name"] == name),
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }
