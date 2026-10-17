"""声量异常告警：按监控任务统计单位时间命中数，超过滑动基线即预警。

思路借鉴 Brand24 Anomaly Detector（自写实现）：
- 分组 = 平台 + 采集时写下的关键词溯源（keyword_search / account_* 目标入库时都会
  打溯源词，因此分组能对应回监控任务）；
- 每个时间窗记录一次命中数，历史窗口滚动算出均值与标准差作为基线；
- 新窗口命中数超过基线均值 N 倍（N 可配置，默认 3）即判定「热度预警」，
  按现有通知渠道以 critical 级别推送，同一分组有冷却期避免刷屏。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import fmean, pstdev

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_naive_utc, utcnow
from app.models import BotTarget, RawContent, User, VolumeBaseline, WebhookConfig, WebhookDelivery
from app.models.rules import VOLUME_ANOMALY, VOLUME_NORMAL
from app.services.settings import get_rules_config

logger = logging.getLogger(__name__)

# 单个 sweep 最多评估的分组数（防脏数据把一轮打爆）
MAX_GROUPS = 200


def group_key(platform: str, keyword: str) -> str:
    return f"{platform}|{keyword}"[:190]


def align_window(now: datetime, minutes: int) -> datetime:
    """把时间对齐到窗口边界（naive UTC），保证同一窗内多次 sweep 落在同一行。"""
    epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    size = max(1, int(minutes)) * 60
    aligned = epoch - (epoch % size)
    return datetime.fromtimestamp(aligned, timezone.utc).replace(tzinfo=None)


def is_anomaly(
    count: int,
    mean: float,
    stddev: float = 0.0,
    *,
    multiplier: float = 3.0,
    min_samples: int = 5,
    min_hits: int = 5,
    samples: int = 0,
) -> bool:
    """新窗口命中数是否算热度预警。

    判定严格大于基线均值的 N 倍（正好 N 倍不算）；样本不足或命中数太少时一律不告警，
    避免刚上线/低频任务被噪声触发。stddev 参与展示，不参与判定（口径保持简单可预期）。
    """
    if count < min_hits or count <= 0:
        return False
    if min_samples > 0 and samples < min_samples:
        return False
    if mean <= 0:
        return False
    if multiplier <= 0:
        return False
    return count > mean * multiplier


def _in_cooldown(row: VolumeBaseline, now: datetime, cooldown_minutes: int) -> bool:
    if cooldown_minutes <= 0 or row.notified_at is None:
        return False
    return now - row.notified_at < timedelta(minutes=cooldown_minutes)


async def _target_for(
    session: AsyncSession, platform: str, keyword: str, cache: dict
) -> int | None:
    """尽力把分组对应回采集目标（仅用于展示，找不到不影响统计）。"""
    from app.models import CollectTarget

    cache_key = f"{platform}|{keyword}"
    if cache_key in cache:
        return cache[cache_key]
    row = await session.scalar(
        select(CollectTarget.id)
        .where(
            CollectTarget.platform == platform,
            CollectTarget.enabled.is_(True),
            (CollectTarget.target_value == keyword)
            | (CollectTarget.display_name == keyword),
        )
        .order_by(CollectTarget.id)
        .limit(1)
    )
    cache[cache_key] = row
    return row


async def _window_counts(
    session: AsyncSession, window_start: datetime, window_end: datetime
) -> dict[tuple[str, str], int]:
    """当前窗口内各 (平台, 溯源关键词) 的命中数（下架内容不计入声量）。"""
    rows = await session.execute(
        select(RawContent.platform, RawContent.matched_keywords).where(
            RawContent.collected_at >= window_start,
            RawContent.collected_at < window_end,
            RawContent.hidden.is_(False),
        )
    )
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for platform, keywords in rows:
        for keyword in dict.fromkeys(str(item) for item in (keywords or []) if item):
            counts[(str(platform), keyword[:255])] += 1
    return dict(counts)


def _history(
    session: AsyncSession, window_start: datetime, minutes: int, windows: int
):
    return select(VolumeBaseline).where(
        VolumeBaseline.window_started_at < window_start,
        VolumeBaseline.window_started_at
        >= window_start - timedelta(minutes=minutes * max(1, windows)),
    )


async def sweep_volume(
    session: AsyncSession,
    now: datetime | None = None,
    *,
    config: dict | None = None,
) -> dict:
    """扫一遍当前窗口：更新基线、判定异常、对触发项发 critical 通知。"""
    cfg = config or await get_rules_config(session)
    # 数据库存 naive UTC：入参带时区也先规整，避免冷却判定混用时区
    now = to_naive_utc(now or utcnow())
    minutes = int(cfg["window_minutes"])
    window_start = align_window(now, minutes)
    window_end = window_start + timedelta(minutes=minutes)

    stats = {"groups": 0, "windows": 0, "anomalies": 0, "notified": 0}
    counts = await _window_counts(session, window_start, window_end)
    if not counts:
        return stats

    history_rows = list(
        (await session.execute(_history(session, window_start, minutes, cfg["history_windows"])))
        .scalars()
    )
    by_group: dict[str, list[VolumeBaseline]] = defaultdict(list)
    for row in history_rows:
        by_group[row.group_key].append(row)

    target_cache: dict[str, int | None] = {}
    alerts: list[tuple[VolumeBaseline, float, float]] = []
    for (platform, keyword), count in sorted(
        counts.items(), key=lambda item: (-item[1], item[0])
    )[:MAX_GROUPS]:
        key = group_key(platform, keyword)
        prior = sorted(by_group.get(key, []), key=lambda row: row.window_started_at)
        samples = [float(row.hit_count) for row in prior]
        mean = fmean(samples) if samples else 0.0
        stddev = pstdev(samples) if len(samples) > 1 else 0.0

        row = await session.scalar(
            select(VolumeBaseline).where(
                VolumeBaseline.group_key == key,
                VolumeBaseline.window_started_at == window_start,
            )
        )
        if row is None:
            row = VolumeBaseline(
                group_key=key,
                platform=platform,
                keyword=keyword,
                window_started_at=window_start,
                window_minutes=minutes,
                target_id=await _target_for(session, platform, keyword, target_cache),
            )
            session.add(row)
        row.hit_count = count
        row.mean = round(mean, 3)
        row.stddev = round(stddev, 3)
        row.samples = len(samples)
        row.multiplier = float(cfg["anomaly_multiplier"])
        anomaly = is_anomaly(
            count,
            mean,
            stddev,
            multiplier=float(cfg["anomaly_multiplier"]),
            min_samples=int(cfg["min_samples"]),
            min_hits=int(cfg["min_hits"]),
            samples=len(samples),
        )
        row.status = VOLUME_ANOMALY if anomaly else VOLUME_NORMAL
        stats["groups"] += 1
        stats["windows"] += 1
        if anomaly:
            stats["anomalies"] += 1
            if not _in_cooldown(row, now, int(cfg["cooldown_minutes"])):
                alerts.append((row, mean, stddev))
        await session.commit()

    for row, mean, stddev in alerts:
        delivered = await _send_alert(session, row, mean, stddev, now)
        row.notified_at = now
        await session.commit()
        stats["notified"] += delivered
        logger.info(
            "volume alert %s hits=%s mean=%.2f stddev=%.2f notified=%s",
            row.group_key,
            row.hit_count,
            mean,
            stddev,
            delivered,
        )
    return stats


def _alert_text(row: VolumeBaseline, mean: float, stddev: float) -> tuple[str, str]:
    ratio = (row.hit_count / mean) if mean > 0 else 0.0
    title = f"热度预警：{row.keyword} 声量异常"
    body = (
        f"监控分组 {row.platform}/{row.keyword} 在最近 {row.window_minutes} 分钟"
        f"命中 {row.hit_count} 条，基线均值 {mean:.1f}（±{stddev:.1f}），"
        f"约为基线的 {ratio:.1f} 倍（阈值 {row.multiplier:g} 倍）。\n"
        f"窗口起点：{row.window_started_at:%Y-%m-%d %H:%M} UTC"
    )
    return title, body


async def _send_alert(
    session: AsyncSession, row: VolumeBaseline, mean: float, stddev: float, now: datetime
) -> int:
    """按现有渠道发 critical 预警：QQ 机器人（critical 穿透静默）+ OnePush + webhook。"""
    title, body = _alert_text(row, mean, stddev)
    delivered = 0

    # QQ：给每个启用的机器人目标建 critical 投递（dedupe key 按分组+窗口）
    try:
        from app.services.bot import create_delivery

        targets = list(
            (
                await session.execute(
                    select(BotTarget).where(BotTarget.enabled.is_(True))
                )
            ).scalars()
        )
        stamp = int(row.window_started_at.replace(tzinfo=timezone.utc).timestamp())
        for target in targets:
            await create_delivery(
                session, target, "critical", f"{title}\n{body}", f"volume:{row.group_key}:{stamp}"
            )
            delivered += 1
    except Exception as exc:  # noqa: BLE001 - 单渠道失败不影响其它渠道
        logger.warning("volume alert qq push failed: %r", exc)

    # OnePush：站内管理员推送
    try:
        from app.services.notifier import send_information

        result = await send_information(session, title, body)
        delivered += int(bool((result.get("onepush") or {}).get("ok")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("volume alert onepush failed: %r", exc)

    # Webhook：推给启用中的渠道（payload 手工构造，重试与记录复用既有实现）
    try:
        from app.services.webhooks import deliver_webhook

        hooks = list(
            (
                await session.execute(
                    select(WebhookConfig)
                    .join(User, User.id == WebhookConfig.user_id)
                    .where(WebhookConfig.enabled.is_(True), User.status == "active")
                    .order_by(WebhookConfig.id)
                )
            ).scalars()
        )
        payload = {
            "title": title,
            "link": "",
            "content": body,
            "source": f"{row.platform}/{row.keyword}",
            "timestamp": now.replace(tzinfo=timezone.utc).isoformat(),
            "tier": "critical",
            "id": None,
            "community": None,
            "keywords": [row.keyword],
        }
        stamp = int(row.window_started_at.replace(tzinfo=timezone.utc).timestamp())
        for hook in hooks:
            dedupe_key = f"volume:{row.group_key}:{stamp}:webhook:{hook.id}"
            existing = await session.scalar(
                select(WebhookDelivery.dedupe_key).where(
                    WebhookDelivery.dedupe_key == dedupe_key
                )
            )
            if existing is not None:
                continue
            row_delivery = await deliver_webhook(
                session, hook, payload, kind="volume", dedupe_key=dedupe_key
            )
            delivered += int(row_delivery.status == "sent")
    except Exception as exc:  # noqa: BLE001
        logger.warning("volume alert webhook push failed: %r", exc)

    return delivered


async def list_baselines(
    session: AsyncSession,
    *,
    status: str = "",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """管理端查看声量基线（分页，默认最新的窗口在前）。"""
    conditions = []
    if status:
        conditions.append(VolumeBaseline.status == status)
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        conditions.append(
            VolumeBaseline.keyword.like(like) | VolumeBaseline.group_key.like(like)
        )
    total = await session.scalar(
        select(func.count()).select_from(VolumeBaseline).where(*conditions)
    ) or 0
    rows = list(
        (
            await session.execute(
                select(VolumeBaseline)
                .where(*conditions)
                .order_by(VolumeBaseline.window_started_at.desc(), VolumeBaseline.id.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
    )
    return {
        "items": [_baseline_view(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _baseline_view(row: VolumeBaseline) -> dict:
    ratio = (row.hit_count / row.mean) if row.mean and row.mean > 0 else None
    return {
        "id": row.id,
        "group_key": row.group_key,
        "platform": row.platform,
        "keyword": row.keyword,
        "target_id": row.target_id,
        "window_started_at": row.window_started_at,
        "window_minutes": row.window_minutes,
        "hit_count": row.hit_count,
        "mean": row.mean,
        "stddev": row.stddev,
        "samples": row.samples,
        "multiplier": row.multiplier,
        "status": row.status,
        "ratio": round(ratio, 2) if ratio else None,
        "notified_at": row.notified_at,
    }
