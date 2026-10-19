"""商品档案服务（批次 3b）：档案头、价格/热度曲线、AI 差评聚合、用户状态与到价提醒。

思路借鉴（均为自写实现）：
- MyFigureCitation 的「一物一档」：档案头 + 官方时间线 + 证据分开呈现；
- 什么值得买的降价提醒：用户设心理价位，观测价低于它即推送；
- Keepa 的价格曲线：按时间点采样，价格与热度画在同一张图上。

三条边界：
1. 只读不改 `products` 的履约字段——履约结论仍由 fulfillment 引擎负责；
2. 价格/热度都是「观测值」，接口同时给出观测时间与来源，不当成官方定价；
3. 到价提醒走既有通知渠道（QQ 机器人 / OnePush / Webhook / 邮件），不新增投递链路。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_naive_utc, utcnow
from app.models import (
    Event,
    Evidence,
    FEEDBACK_SENTIMENTS,
    Product,
    ProductFeedback,
    ProductPriceAlert,
    ProductPricePoint,
    ProductProfile,
    ProductTrackState,
    RELEASE_STATES,
    RawContent,
    TRACK_STATES,
)
from app.models.content import ContentVersion

logger = logging.getLogger(__name__)

# 一次采样最多回看多少天的正文（价格抽取只扫近期内容，避免全表）
PRICE_LOOKBACK_DAYS = 30
# 单个商品单次最多记录多少条价格采样（防脏数据刷爆曲线）
MAX_PRICE_POINTS_PER_SWEEP = 4
# 曲线默认返回天数
DEFAULT_SERIES_DAYS = 90
# 到价提醒通知标题前缀
ALERT_TITLE_PREFIX = "到价提醒"

# 正文价格模式：¥199 / 199元 / RMB 199 / 199 CNY / 定价：199
_PRICE_PATTERNS = (
    re.compile(r"[¥￥]\s*(\d{1,5}(?:\.\d{1,2})?)"),
    re.compile(r"(\d{1,5}(?:\.\d{1,2})?)\s*元"),
    re.compile(r"(?:RMB|CNY|rmb|cny)\s*(\d{1,5}(?:\.\d{1,2})?)"),
    re.compile(r"定价[：:]?\s*(\d{1,5}(?:\.\d{1,2})?)"),
    re.compile(r"售价[：:]?\s*(\d{1,5}(?:\.\d{1,2})?)"),
    re.compile(r"到手价[：:]?\s*(\d{1,5}(?:\.\d{1,2})?)"),
)
# 明显不是商品定价的数字上下文（版本号/日期/时长/人数），命中就跳过该数字
_PRICE_NOISE = re.compile(r"^\s*(?:v|ver|version|第|第?[0-9一二三四五六七八九十]+[期阶段版])", re.I)


# ---------- 档案头 ----------


async def get_profile(session: AsyncSession, product_id: int) -> ProductProfile | None:
    return await session.scalar(
        select(ProductProfile).where(ProductProfile.product_id == product_id)
    )


async def ensure_profile(session: AsyncSession, product_id: int) -> ProductProfile:
    """取档案，没有就建（幂等；新建时不覆盖任何已有字段）。"""
    profile = await get_profile(session, product_id)
    if profile is None:
        profile = ProductProfile(product_id=product_id)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
    return profile


def _clean_price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:  # NaN / 非正数都视为无效
        return None
    return round(number, 2)


def normalize_release_state(value) -> str:
    state = str(value or "unknown").strip()
    return state if state in RELEASE_STATES else "unknown"


def normalize_track_state(value) -> str:
    state = str(value or "watching").strip()
    return state if state in TRACK_STATES else "watching"


def normalize_sentiment(value) -> str:
    state = str(value or "negative").strip()
    return state if state in FEEDBACK_SENTIMENTS else "negative"


async def upsert_profile(session: AsyncSession, product_id: int, payload: dict) -> ProductProfile:
    """管理端维护档案头：只更新显式给出的字段，未给出的保持原值。"""
    profile = await ensure_profile(session, product_id)
    text_fields = {
        "cover_url": 512,
        "official_url": 512,
        "official_name": 255,
        "release_note": 255,
        "price_source": 64,
        "note": None,
    }
    for field, limit in text_fields.items():
        if field not in payload:
            continue
        value = payload.get(field)
        value = None if value is None else str(value).strip()
        if limit and value:
            value = value[:limit]
        setattr(profile, field, value or None)
    if "aliases" in payload:
        aliases = [str(a).strip()[:64] for a in (payload.get("aliases") or []) if str(a).strip()]
        profile.aliases = list(dict.fromkeys(aliases))[:20]
    if "release_state" in payload:
        profile.release_state = normalize_release_state(payload.get("release_state"))
    if "release_at" in payload:
        profile.release_at = _coerce_dt(payload.get("release_at"))
    if "price_current" in payload:
        price = _clean_price(payload.get("price_current"))
        profile.price_current = price
        profile.price_observed_at = utcnow() if price is not None else None
    await session.commit()
    await session.refresh(profile)
    return profile


def _coerce_dt(value) -> datetime | None:
    """接受 naive/aware datetime 或 ISO 字符串，统一成 naive UTC 入库。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_naive_utc(value) if value.tzinfo else value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return to_naive_utc(parsed) if parsed.tzinfo else parsed


# ---------- 价格 / 热度曲线 ----------


def extract_price(text: str | None) -> float | None:
    """从正文里抽一个「像是商品定价」的数字；抽不到返回 None。

    保守策略：只认带货币符号/单位或明确「定价/售价/到手价」前缀的数字，
    且排除版本号、期数一类的上下文。宁可漏报也不要错报——到价提醒误报
    比漏报更烦人。
    """
    if not text:
        return None
    plain = re.sub(r"<[^>]+>", " ", text)
    best: float | None = None
    for pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(plain):
            start = max(0, match.start() - 12)
            prefix = plain[start : match.start()]
            if _PRICE_NOISE.match(prefix):
                continue
            # 数字被上下文里的更长数字截断（"编号 123456 元" / "¥1234567"）时
            # 匹配到的只是尾巴，不是价格，跳过
            head = plain[match.start(1) - 1] if match.start(1) > 0 else ""
            tail = plain[match.end(1)] if match.end(1) < len(plain) else ""
            if head.isdigit() or tail.isdigit():
                continue
            value = _clean_price(match.group(1))
            if value is None or value > 100_000:
                continue
            if best is None or value < best:
                best = value
    return best


async def _watch_terms(session: AsyncSession, product: Product) -> list[str]:
    """商品监控词 + 档案别名（去重、保序）。"""
    terms = [str(t).strip() for t in (product.watch_terms or []) if str(t).strip()]
    profile = await get_profile(session, product.id)
    if profile is not None:
        terms.extend(str(a).strip() for a in (profile.aliases or []) if str(a).strip())
    return list(dict.fromkeys(terms))


def _match_conditions(terms: list[str]) -> list:
    """命中条件：标题或正文 LIKE '%词%'（前导通配必然全表扫，见 _daily_hit_counts）。"""
    return [
        ContentVersion.title.like(f"%{term}%") | ContentVersion.text.like(f"%{term}%")
        for term in terms
    ]


async def _matched_contents(
    session: AsyncSession, product: Product, since: datetime, limit: int = 200
) -> list[tuple[RawContent, ContentVersion | None]]:
    """命中商品 watch_terms（+ 档案别名）的近期内容。"""
    terms = await _watch_terms(session, product)
    if not terms:
        return []
    rows = await session.execute(
        select(RawContent, ContentVersion)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .where(
            RawContent.hidden.is_(False),
            RawContent.community == product.community,
            RawContent.collected_at >= since,
            or_(*_match_conditions(terms)),
        )
        .order_by(RawContent.collected_at.desc())
        .limit(limit)
    )
    seen: set[int] = set()
    out: list[tuple[RawContent, ContentVersion | None]] = []
    for raw, version in rows:
        if raw.id in seen:
            continue
        seen.add(raw.id)
        out.append((raw, version))
    return out


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day)


async def _daily_hit_counts(
    session: AsyncSession, product: Product, since: datetime
) -> dict[date, int]:
    """一条 SQL 按天分组统计命中数。

    原来 `heat_score` 要跑 7 次 `daily_hits`，每次都是一次 `LIKE '%词%'` 全表扫；
    一个商品常配 5-10 个监控词，OR 出 10-20 个前导通配，50 个商品就是上千次全表扫
    （PERF-015）。这里合并成一次带 GROUP BY 的扫描， sweep 也复用同一份结果。
    彻底解法是 FULLTEXT 或 (term, collected_at) 反查表，见报告 PERF-015 建议①。
    """
    terms = await _watch_terms(session, product)
    if not terms:
        return {}
    rows = await session.execute(
        select(func.date(RawContent.collected_at), func.count(RawContent.id))
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .where(
            RawContent.hidden.is_(False),
            RawContent.community == product.community,
            RawContent.collected_at >= since,
            or_(*_match_conditions(terms)),
        )
        .group_by(func.date(RawContent.collected_at))
    )
    counts: dict[date, int] = {}
    for day, count in rows:
        try:
            counts[date.fromisoformat(str(day))] = int(count)
        except ValueError:
            continue  # 方言返回了非日期形态，宁缺勿错
    return counts


def _heat_from_counts(counts: dict[date, int], today: date) -> float:
    """热度分 = 当日命中 + 近 7 日日均命中（让新爆发比长期平稳更突出）。"""
    week = [counts.get(today - timedelta(days=offset), 0) for offset in range(7)]
    return round(week[0] + (sum(week[1:]) / 6), 2)


async def daily_hits(session: AsyncSession, product: Product, day: date) -> int:
    """某天命中该商品监控词的内容条数（热度曲线的原始信号）。"""
    counts = await _daily_hit_counts(session, product, _day_start(day))
    return counts.get(day, 0)


async def heat_score(session: AsyncSession, product: Product, now: datetime | None = None) -> float:
    """热度分 = 当日命中 + 近 7 日日均命中（让新爆发比长期平稳更突出）。"""
    now = now or utcnow()
    counts = await _daily_hit_counts(session, product, _day_start(now.date() - timedelta(days=6)))
    return _heat_from_counts(counts, now.date())


async def record_price_point(
    session: AsyncSession,
    product_id: int,
    *,
    price: float | None = None,
    heat: float | None = None,
    hits: int = 0,
    source: str = "manual",
    note: str | None = None,
) -> ProductPricePoint:
    """记一个采样点，并把最新价格回写到档案头（价格曲线与当前价同源）。"""
    row = ProductPricePoint(
        product_id=product_id,
        captured_at=utcnow(),
        price=_clean_price(price),
        heat=None if heat is None else round(float(heat), 2),
        hits=int(hits or 0),
        source=source,
        note=(note or "")[:255] or None,
    )
    session.add(row)
    await session.commit()
    if row.price is not None:
        profile = await ensure_profile(session, product_id)
        profile.price_current = row.price
        profile.price_observed_at = row.captured_at
        profile.price_source = source
        await session.commit()
    return row


async def series(
    session: AsyncSession, product_id: int, days: int = DEFAULT_SERIES_DAYS
) -> list[dict]:
    """价格/热度曲线数据（按天聚合，同一天多个采样取最后一条）。"""
    days = max(1, min(int(days or DEFAULT_SERIES_DAYS), 365))
    since = utcnow() - timedelta(days=days)
    rows = list(
        (
            await session.execute(
                select(ProductPricePoint)
                .where(
                    ProductPricePoint.product_id == product_id,
                    ProductPricePoint.captured_at >= since,
                )
                .order_by(ProductPricePoint.captured_at.asc())
            )
        ).scalars()
    )
    by_day: dict[str, dict] = {}
    for row in rows:
        key = row.captured_at.date().isoformat()
        current = by_day.setdefault(key, {"date": key, "price": None, "heat": None, "hits": 0})
        if row.price is not None:
            current["price"] = row.price
        if row.heat is not None:
            current["heat"] = row.heat
        current["hits"] = max(current["hits"], int(row.hits or 0))
    return list(by_day.values())


async def sweep_price_points(session: AsyncSession, now: datetime | None = None) -> dict:
    """扫一遍近期正文，抽取商品价格并采样（采集链路之外的一次旁路观察）。

    每个商品单轮最多记 MAX_PRICE_POINTS_PER_SWEEP 条，只记「比当前价更低」
    或「当前还没有价」的观测，避免同一条内容反复刷曲线。
    """
    now = now or utcnow()
    since = now - timedelta(days=PRICE_LOOKBACK_DAYS)
    products = list((await session.execute(select(Product))).scalars())
    stats = {"scanned": len(products), "points": 0, "priced": 0, "lowered": 0}
    for product in products:
        profile = await get_profile(session, product.id)
        rows = await _matched_contents(session, product, since, limit=60)
        # 每商品只扫一次：热度与当日命中都从同一份按天分组结果里取（PERF-015）
        counts = await _daily_hit_counts(session, product, _day_start(now.date() - timedelta(days=6)))
        heat = _heat_from_counts(counts, now.date())
        recorded = 0
        for raw, version in rows:
            if recorded >= MAX_PRICE_POINTS_PER_SWEEP:
                break
            text = " ".join(
                part for part in [(version.title if version else None), (version.text if version else None)] if part
            )
            price = extract_price(text)
            if price is None:
                continue
            current = profile.price_current if profile is not None else None
            if current is not None and price >= current:
                continue
            hits = counts.get(raw.collected_at.date(), 0) if raw.collected_at else 0
            await record_price_point(
                session,
                product.id,
                price=price,
                heat=heat,
                hits=hits,
                source="auto",
                note=f"正文抽取：内容 #{raw.id}",
            )
            recorded += 1
            stats["points"] += 1
            if current is None:
                stats["priced"] += 1
            else:
                stats["lowered"] += 1
    return stats


# ---------- AI 质量反馈聚合（差评为重点） ----------

_FEEDBACK_SYSTEM = (
    "你是商品质量反馈编辑。给定一条玩家反馈正文，判断它对商品质量的情感极性"
    "（negative=差评/吐槽质量问题，mixed=褒贬不一，positive=好评），"
    "并用一句 60 字以内中文概括它到底在说什么问题。只输出 JSON："
    '{"sentiment": "negative|mixed|positive", "summary": "...", "quote": "原文关键句"}。'
    "禁止补充正文没有的信息；无法判断时 sentiment 给 mixed、summary 说明信息不足。"
)


def _fallback_feedback(text: str) -> dict:
    plain = re.sub(r"\s+", " ", (text or "")).strip()
    excerpt = plain[:120]
    return {
        "sentiment": "negative",
        "summary": excerpt or "未配置 AI，暂以原文片段作为反馈摘要。",
        "quote": excerpt[:200] or None,
    }


async def aggregate_feedback(
    session: AsyncSession, product: Product, *, limit: int = 20, use_ai: bool = True
) -> dict:
    """对质量反馈类命中做摘要（复用现有 AI 通道；未配置 AI 时安全回退）。

    幂等：同一 content_id 只保留一条聚合，重复跑只更新结论。
    """
    from app.services.ai import ai_available, chat, get_ai_config, record_usage

    since = utcnow() - timedelta(days=PRICE_LOOKBACK_DAYS)
    rows = await _matched_contents(session, product, since, limit=limit * 3)
    config = await get_ai_config(session)
    can_ai = use_ai and ai_available(config)
    stats = {"scanned": len(rows), "created": 0, "updated": 0, "skipped": 0, "ai": can_ai}
    for raw, version in rows[:limit]:
        if version is None:
            stats["skipped"] += 1
            continue
        existing = await session.scalar(
            select(ProductFeedback).where(
                ProductFeedback.product_id == product.id, ProductFeedback.content_id == raw.id
            )
        )
        text = re.sub(r"<[^>]+>", " ", (version.text or version.title or "")).strip()
        if not text:
            stats["skipped"] += 1
            continue
        if can_ai:
            try:
                started = time.monotonic()
                out, usage = await chat(
                    config,
                    _FEEDBACK_SYSTEM,
                    f"商品：{product.name}\n反馈正文：{text[:1500]}",
                    json_mode=True,
                )
                parsed = json.loads(out)
                sentiment = normalize_sentiment(parsed.get("sentiment"))
                summary = str(parsed.get("summary") or "").strip()[:500]
                quote = str(parsed.get("quote") or "").strip()[:500] or None
                score = 0.8
                await record_usage(
                    session,
                    task="product_feedback",
                    config=config,
                    usage=usage,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    ref_id=product.id,
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败回退，不中断整轮
                logger.warning("feedback aggregate failed for content %s: %r", raw.id, exc)
                parsed = _fallback_feedback(text)
                sentiment, summary, quote, score = (
                    parsed["sentiment"],
                    parsed["summary"],
                    parsed["quote"],
                    0.0,
                )
                can_ai = False  # 连续失败时本轮不再打 AI，省额度
        else:
            parsed = _fallback_feedback(text)
            sentiment, summary, quote, score = (
                parsed["sentiment"],
                parsed["summary"],
                parsed["quote"],
                0.0,
            )
        if existing is None:
            session.add(
                ProductFeedback(
                    product_id=product.id,
                    content_id=raw.id,
                    sentiment=sentiment,
                    score=score,
                    summary=summary or text[:200],
                    quote=quote,
                    source_method="ai" if can_ai else "fallback",
                    model=config.get("model") if can_ai else None,
                )
            )
            stats["created"] += 1
        else:
            existing.sentiment = sentiment
            existing.score = score
            existing.summary = summary or text[:200]
            existing.quote = quote
            existing.source_method = "ai" if can_ai else "fallback"
            existing.model = config.get("model") if can_ai else None
            stats["updated"] += 1
    await session.commit()
    return stats


async def list_feedback(session: AsyncSession, product_id: int, limit: int = 50) -> list[dict]:
    rows = list(
        (
            await session.execute(
                select(ProductFeedback, RawContent.url)
                .join(RawContent, RawContent.id == ProductFeedback.content_id, isouter=True)
                .where(ProductFeedback.product_id == product_id)
                .order_by(
                    ProductFeedback.sentiment.asc(),
                    ProductFeedback.score.desc(),
                    ProductFeedback.id.desc(),
                )
                .limit(limit)
            )
        ).all()
    )
    return [
        {
            "id": row.id,
            "sentiment": row.sentiment,
            "sentiment_label": _sentiment_label(row.sentiment),
            "score": row.score,
            "summary": row.summary,
            "quote": row.quote,
            "source_method": row.source_method,
            "reviewed": row.reviewed,
            "content_id": row.content_id,
            "url": url,
            "created_at": row.created_at,
        }
        for row, url in rows
    ]


def _sentiment_label(sentiment: str) -> str:
    from app.models import FEEDBACK_SENTIMENT_LABELS

    return FEEDBACK_SENTIMENT_LABELS.get(sentiment, sentiment)


async def set_feedback_reviewed(
    session: AsyncSession, feedback_id: int, reviewed: bool
) -> ProductFeedback | None:
    row = await session.get(ProductFeedback, feedback_id)
    if row is None:
        return None
    row.reviewed = bool(reviewed)
    await session.commit()
    await session.refresh(row)
    return row


# ---------- 用户状态与到价提醒 ----------


async def set_track_state(
    session: AsyncSession, user_id: int, product_id: int, state: str, note: str | None = None
) -> ProductTrackState:
    row = await session.scalar(
        select(ProductTrackState).where(
            ProductTrackState.user_id == user_id, ProductTrackState.product_id == product_id
        )
    )
    state = normalize_track_state(state)
    if row is None:
        row = ProductTrackState(user_id=user_id, product_id=product_id, state=state, note=note)
        session.add(row)
    else:
        row.state = state
        row.note = note
        row.marked_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return row


async def clear_track_state(session: AsyncSession, user_id: int, product_id: int) -> bool:
    row = await session.scalar(
        select(ProductTrackState).where(
            ProductTrackState.user_id == user_id, ProductTrackState.product_id == product_id
        )
    )
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def set_price_alert(
    session: AsyncSession, user_id: int, product_id: int, target_price: float
) -> ProductPriceAlert:
    price = _clean_price(target_price)
    if price is None:
        raise ValueError("心理价位必须是大于 0 的数字")
    row = await session.scalar(
        select(ProductPriceAlert).where(
            ProductPriceAlert.user_id == user_id, ProductPriceAlert.product_id == product_id
        )
    )
    if row is None:
        row = ProductPriceAlert(user_id=user_id, product_id=product_id, target_price=price)
        session.add(row)
    else:
        row.target_price = price
        row.enabled = True
        # 重新设价视为新一轮提醒：允许再次触发，计数从零开始
        # （推送的 dedupe key 用 trigger_count + 1，不复零会和上一轮撞号）
        row.triggered_at = None
        row.triggered_price = None
        row.trigger_count = 0
    await session.commit()
    await session.refresh(row)
    return row


async def clear_price_alert(session: AsyncSession, user_id: int, product_id: int) -> bool:
    row = await session.scalar(
        select(ProductPriceAlert).where(
            ProductPriceAlert.user_id == user_id, ProductPriceAlert.product_id == product_id
        )
    )
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def list_tracker(session: AsyncSession, user_id: int) -> list[dict]:
    """我的商品跟踪：手动状态 + 到价提醒 + 当前观测价。"""
    states = list(
        (
            await session.execute(
                select(ProductTrackState, Product, ProductProfile)
                .join(Product, Product.id == ProductTrackState.product_id)
                .join(ProductProfile, ProductProfile.product_id == Product.id, isouter=True)
                .where(ProductTrackState.user_id == user_id)
                .order_by(ProductTrackState.marked_at.desc(), ProductTrackState.id.desc())
            )
        ).all()
    )
    alerts = {
        row.product_id: row
        for row in (
            await session.execute(
                select(ProductPriceAlert).where(ProductPriceAlert.user_id == user_id)
            )
        ).scalars()
    }
    items = []
    for state, product, profile in states:
        alert = alerts.get(product.id)
        items.append(
            {
                "product_id": product.id,
                "slug": product.slug,
                "name": product.name,
                "cover_url": profile.cover_url if profile else None,
                "state": state.state,
                "state_label": _track_label(state.state),
                "note": state.note,
                "marked_at": state.marked_at,
                "price_current": profile.price_current if profile else None,
                "price_observed_at": profile.price_observed_at if profile else None,
                "alert": None
                if alert is None
                else {
                    "id": alert.id,
                    "target_price": alert.target_price,
                    "enabled": alert.enabled,
                    "triggered_at": alert.triggered_at,
                    "triggered_price": alert.triggered_price,
                    "trigger_count": alert.trigger_count,
                },
            }
        )
    return items


def _track_label(state: str) -> str:
    from app.models import TRACK_STATE_LABELS

    return TRACK_STATE_LABELS.get(state, state)


# ---------- 到价提醒扫描 ----------


def _alert_text(product: Product, price: float, target: float, currency: str) -> tuple[str, str]:
    title = f"{ALERT_TITLE_PREFIX}：{product.name} 低于你的心理价位"
    body = (
        f"{product.name} 当前观测价 {price:g} {currency}，低于你设定的 {target:g} {currency}。\n"
        f"观测来自商品档案的价格曲线，非官方定价，下单前请以店铺页面为准。"
    )
    return title, body


async def _notify_alert(
    session: AsyncSession,
    alert: ProductPriceAlert,
    product: Product,
    profile: ProductProfile | None,
    price: float,
    now: datetime,
) -> int:
    """按既有渠道发到价提醒：QQ 机器人（critical 穿透静默）+ OnePush + Webhook + 邮件。"""
    currency = (profile.price_currency if profile else None) or "CNY"
    title, body = _alert_text(product, price, alert.target_price, currency)
    delivered = 0
    stamp = int(now.replace(tzinfo=timezone.utc).timestamp())

    # 邮件：给启用通知的已验证用户（用户自己设的提醒，直接投给他）
    try:
        from app.models import NotificationDelivery, User
        from app.services.email_templates import notice_email
        from app.services.mailer import send_email
        from app.services.settings import get_effective_smtp

        user = await session.get(User, alert.user_id)
        if user is not None and user.status == "active" and user.is_verified and user.notification_enabled:
            dedupe_key = f"price-alert:{alert.id}:{alert.trigger_count + 1}"
            if (
                await session.scalar(
                    select(NotificationDelivery.id).where(
                        NotificationDelivery.dedupe_key == dedupe_key
                    )
                )
                is None
            ):
                delivery = NotificationDelivery(
                    user_id=user.id,
                    kind="brief",
                    source="schedule",
                    period_date=now.date(),
                    dedupe_key=dedupe_key,
                    status="pending",
                    subject=title,
                    body=body,
                    html_body=notice_email(title, body),
                )
                session.add(delivery)
                await session.commit()
                sent = await send_email(
                    user.email,
                    title,
                    body,
                    smtp=await get_effective_smtp(session),
                    html_body=notice_email(title, body),
                )
                delivery.status = "sent" if sent else "failed"
                delivery.error = None if sent else "SMTP 发送失败，请查看服务端日志"
                delivery.sent_at = utcnow() if sent else None
                await session.commit()
                delivered += int(sent)
    except Exception as exc:  # noqa: BLE001 - 单渠道失败不影响其它渠道
        logger.warning("price alert email failed for alert %s: %r", alert.id, exc)

    # QQ：每个启用的机器人目标建一条 critical 投递（穿透静默时段）
    try:
        from app.models import BotTarget
        from app.services.bot import create_delivery

        targets = list(
            (
                await session.execute(
                    select(BotTarget).where(BotTarget.enabled.is_(True))
                )
            ).scalars()
        )
        for target in targets:
            await create_delivery(
                session,
                target,
                "critical",
                f"{title}\n{body}",
                f"price-alert:{alert.id}:{alert.trigger_count + 1}",
            )
            delivered += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("price alert qq push failed for alert %s: %r", alert.id, exc)

    # OnePush：站内管理员推送
    try:
        from app.services.notifier import send_information

        result = await send_information(session, title, body)
        delivered += int(bool((result.get("onepush") or {}).get("ok")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("price alert onepush failed for alert %s: %r", alert.id, exc)

    # Webhook：推给启用中的渠道
    try:
        from app.models import User, WebhookConfig, WebhookDelivery
        from app.services.webhooks import deliver_webhook

        hooks = list(
            (
                await session.execute(
                    select(WebhookConfig)
                    .join(User, User.id == WebhookConfig.user_id)
                    .where(
                        # 到价提醒是用户级事件：只能投给设提醒的这个人自己的渠道。
                        # 漏掉 user_id 条件会把他的商品名/心理价位/监控词广播给全站 webhook
                        # （含第三方群机器人），属于跨租户信息泄露。
                        WebhookConfig.user_id == alert.user_id,
                        WebhookConfig.enabled.is_(True),
                        User.status == "active",
                    )
                    .order_by(WebhookConfig.id)
                )
            ).scalars()
        )
        payload = {
            "title": title,
            "link": "",
            "content": body,
            "source": f"product/{product.slug}",
            "timestamp": now.replace(tzinfo=timezone.utc).isoformat(),
            "tier": "critical",
            "id": None,
            "community": product.community,
            "keywords": list(product.watch_terms or [])[:8],
        }
        for hook in hooks:
            dedupe_key = f"price-alert:{alert.id}:{alert.trigger_count + 1}:webhook:{hook.id}"
            if (
                await session.scalar(
                    select(WebhookDelivery.id).where(WebhookDelivery.dedupe_key == dedupe_key)
                )
                is not None
            ):
                continue
            row = await deliver_webhook(
                session, hook, payload, kind="price_alert", dedupe_key=dedupe_key
            )
            delivered += int(row.status == "sent")
    except Exception as exc:  # noqa: BLE001
        logger.warning("price alert webhook push failed for alert %s: %r", alert.id, exc)

    logger.info(
        "price alert %s fired: product=%s price=%s target=%s delivered=%s stamp=%s",
        alert.id,
        product.id,
        price,
        alert.target_price,
        delivered,
        stamp,
    )
    return delivered


async def sweep_price_alerts(session: AsyncSession, now: datetime | None = None) -> dict:
    """扫一遍启用中的到价提醒：当前观测价低于心理价位即通知（同一轮一次）。"""
    now = now or utcnow()
    alerts = list(
        (
            await session.execute(
                select(ProductPriceAlert, Product, ProductProfile)
                .join(Product, Product.id == ProductPriceAlert.product_id)
                .join(ProductProfile, ProductProfile.product_id == Product.id, isouter=True)
                .where(ProductPriceAlert.enabled.is_(True))
                .order_by(ProductPriceAlert.id)
            )
        ).all()
    )
    stats = {"checked": len(alerts), "triggered": 0, "delivered": 0, "skipped": 0}
    for alert, product, profile in alerts:
        price = profile.price_current if profile is not None else None
        if price is None or price >= alert.target_price:
            stats["skipped"] += 1
            continue
        delivered = await _notify_alert(session, alert, product, profile, price, now)
        alert.triggered_at = now
        alert.triggered_price = price
        alert.trigger_count = int(alert.trigger_count or 0) + 1
        await session.commit()
        stats["triggered"] += 1
        stats["delivered"] += delivered
    return stats


async def list_alerts(session: AsyncSession, page: int = 1, page_size: int = 50) -> dict:
    """管理端到价提醒台账（含触发次数与最近触发时间）。"""
    from sqlalchemy import func as sa_func

    total = await session.scalar(select(sa_func.count(ProductPriceAlert.id))) or 0
    rows = list(
        (
            await session.execute(
                select(ProductPriceAlert, Product.name, Product.slug)
                .join(Product, Product.id == ProductPriceAlert.product_id)
                .order_by(
                    ProductPriceAlert.triggered_at.desc().nullslast(),
                    ProductPriceAlert.id.desc(),
                )
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": alert.id,
                "user_id": alert.user_id,
                "product_id": alert.product_id,
                "product_name": name,
                "product_slug": slug,
                "target_price": alert.target_price,
                "enabled": alert.enabled,
                "triggered_at": alert.triggered_at,
                "triggered_price": alert.triggered_price,
                "trigger_count": alert.trigger_count,
                "created_at": alert.created_at,
            }
            for alert, name, slug in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------- 发售日历 ----------


def _calendar_entry(product: Product, profile: ProductProfile | None, now: datetime) -> dict | None:
    release_at = profile.release_at if profile is not None else None
    presale = _coerce_dt(product.presale_at)
    ship = _coerce_dt(product.original_ship_at)
    if release_at is None and presale is None and ship is None:
        return None
    state = profile.release_state if profile is not None else "unknown"
    # 没显式维护状态时，按时间推断，避免日历里一片「未公布」
    if state == "unknown":
        anchor = release_at or presale or ship
        if anchor is not None:
            state = "on_sale" if anchor <= now else "announced"
    return {
        "product_id": product.id,
        "slug": product.slug,
        "name": product.name,
        "publisher": product.publisher,
        "cover_url": profile.cover_url if profile else None,
        "official_url": profile.official_url if profile else None,
        "release_at": release_at,
        "presale_at": presale,
        "original_ship_at": ship,
        "release_state": state,
        "release_state_label": _release_label(state),
        "release_note": profile.release_note if profile else None,
        "community": product.community,
    }


def _release_label(state: str) -> str:
    from app.models import RELEASE_STATE_LABELS

    return RELEASE_STATE_LABELS.get(state, state)


async def calendar(session: AsyncSession, now: datetime | None = None) -> dict:
    """发售日历：即将发售 / 进行中（预售或已发售未到发货窗口）/ 已结束。"""
    now = now or utcnow()
    rows = (
        await session.execute(
            select(Product, ProductProfile)
            .join(ProductProfile, ProductProfile.product_id == Product.id, isouter=True)
            .order_by(Product.id)
        )
    ).all()
    upcoming, ongoing, ended = [], [], []
    horizon = now + timedelta(days=180)
    for product, profile in rows:
        entry = _calendar_entry(product, profile, now)
        if entry is None:
            continue
        anchor = entry["release_at"] or entry["presale_at"] or entry["original_ship_at"]
        state = entry["release_state"]
        if state == "shipped" or (anchor is not None and anchor < now - timedelta(days=30)):
            ended.append(entry)
        elif anchor is not None and anchor <= now:
            ongoing.append(entry)
        elif anchor is None or anchor > horizon:
            continue  # 没有可用时间又不处于进行中的，不进日历
        else:
            upcoming.append(entry)
    def _anchor(item: dict):
        return item["release_at"] or item["presale_at"] or item["original_ship_at"]

    upcoming.sort(key=_anchor)
    ongoing.sort(key=_anchor, reverse=True)
    ended.sort(key=_anchor, reverse=True)
    return {
        "generated_at": now,
        "upcoming": upcoming[:60],
        "ongoing": ongoing[:60],
        "ended": ended[:60],
    }


# ---------- 档案聚合视图 ----------


async def dossier(session: AsyncSession, product_id: int, series_days: int = DEFAULT_SERIES_DAYS) -> dict:
    """一物一档的完整载荷：档案头 + 官方公告时间线 + 曲线 + 反馈 + 发售日历条目。"""
    product = await session.get(Product, product_id)
    if product is None:
        return {}
    profile = await get_profile(session, product_id)
    now = utcnow()
    timeline = list(
        (
            await session.execute(
                select(Event)
                .where(Event.product_id == product_id, Event.is_active.is_(True))
                .order_by(Event.last_updated_at.desc(), Event.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    evidence_counts: dict[int, int] = {}
    if timeline:
        counts = (
            await session.execute(
                select(Evidence.event_id, func.count(Evidence.id))
                .where(Evidence.event_id.in_([event.id for event in timeline]))
                .group_by(Evidence.event_id)
            )
        ).all()
        evidence_counts = {event_id: int(count) for event_id, count in counts}
    return {
        "product_id": product.id,
        "slug": product.slug,
        "name": product.name,
        "publisher": product.publisher,
        "community": product.community,
        "skus": list(product.skus or []),
        "price_note": product.price_note,
        "profile": None
        if profile is None
        else {
            "cover_url": profile.cover_url,
            "official_url": profile.official_url,
            "official_name": profile.official_name,
            "aliases": list(profile.aliases or []),
            "release_at": profile.release_at,
            "release_state": profile.release_state,
            "release_state_label": _release_label(profile.release_state),
            "release_note": profile.release_note,
            "price_current": profile.price_current,
            "price_currency": profile.price_currency,
            "price_source": profile.price_source,
            "price_observed_at": profile.price_observed_at,
            "heat_score": profile.heat_score,
            "note": profile.note,
            "updated_at": profile.updated_at,
        },
        "series": await series(session, product_id, series_days),
        "timeline": [
            {
                "event_id": event.id,
                "title": event.title,
                "category": event.category,
                "identity": event.identity,
                "importance": event.importance,
                "summary": event.summary,
                "first_seen_at": event.first_seen_at,
                "latest_evidence_at": event.last_updated_at,
                "evidence_count": evidence_counts.get(event.id, 0),
                "manual_locked": event.manual_locked,
            }
            for event in timeline
        ],
        "feedback": await list_feedback(session, product_id),
        "calendar_entry": _calendar_entry(product, profile, now),
        "fulfillment": {
            "stage": product.computed_stage,
            "stage_label": _stage_label(product.computed_stage),
        },
        "generated_at": now,
    }


def _stage_label(stage: int) -> str:
    from app.models import FULFILLMENT_STAGES

    index = int(stage or 0)
    if 0 <= index < len(FULFILLMENT_STAGES):
        return FULFILLMENT_STAGES[index]
    return FULFILLMENT_STAGES[0]


async def refresh_heat(session: AsyncSession, now: datetime | None = None) -> dict:
    """刷新所有商品的热度分（曲线采样与到价提醒扫描时一起跑）。"""
    now = now or utcnow()
    products = list((await session.execute(select(Product))).scalars())
    updated = 0
    for product in products:
        profile = await ensure_profile(session, product.id)
        profile.heat_score = await heat_score(session, product, now)
        updated += 1
    await session.commit()
    return {"updated": updated}
