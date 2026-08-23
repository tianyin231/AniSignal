"""官方跨平台内容匹配：规则缩小候选，LLM 只做同一事件二选一判断。"""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import ContentMatch, ContentVersion, RawContent, Source
from app.services.ai import ai_available, chat, get_ai_config, record_usage

logger = logging.getLogger(__name__)

MATCH_WINDOW = timedelta(hours=72)
MATCH_CONFIDENCE = 0.9
_LOOKBACK = timedelta(days=45)
_MAX_CANDIDATES_PER_YOUTUBE = 8
_PROMPT_BATCH_SIZE = 8


@dataclass(frozen=True)
class MatchContent:
    id: int
    platform: str
    published_at: datetime
    title: str
    text: str
    category: str | None


@dataclass(frozen=True)
class MatchPair:
    left: MatchContent
    right: MatchContent

    @property
    def key(self) -> tuple[int, int]:
        return tuple(sorted((self.left.id, self.right.id)))


@dataclass
class MatchStats:
    candidates: int = 0
    decisions: int = 0
    positive: int = 0
    failed_batches: int = 0
    skipped_reason: str | None = None


async def match_official_contents(
    session: AsyncSession, content_ids: list[int] | None = None
) -> MatchStats:
    """匹配近期 B 站/YouTube 官方内容；指定 content_ids 时重新判断相关旧配对。"""
    stats = MatchStats()
    config = await get_ai_config(session)
    if not ai_available(config):
        stats.skipped_reason = "ai_not_configured"
        return stats

    rows = await _load_recent_official(session)
    if not rows:
        return stats
    by_id = {row.id: row for row in rows}
    base_ids = set(content_ids or []) & by_id.keys()
    if content_ids and not base_ids:
        return stats

    existing = {
        (row.left_raw_id, row.right_raw_id): row
        for row in await session.scalars(select(ContentMatch))
    }
    candidates = _candidate_pairs(rows, base_ids or None)
    if not content_ids:
        candidates = [pair for pair in candidates if pair.key not in existing]
    stats.candidates = len(candidates)

    for start in range(0, len(candidates), _PROMPT_BATCH_SIZE):
        batch = candidates[start : start + _PROMPT_BATCH_SIZE]
        prompt = _build_prompt(batch)
        started = time.monotonic()
        try:
            raw_response, usage = await chat(
                config,
                _MATCH_SYSTEM_PROMPT,
                prompt,
                json_mode=True,
            )
            allowed_pairs = {pair.key for pair in batch}
            decisions = _complete_missing_decisions(
                _parse_decisions(raw_response, allowed_pairs), allowed_pairs
            )
        except Exception as exc:  # noqa: BLE001 - 匹配失败不阻断采集
            stats.failed_batches += 1
            logger.warning("official content match failed: %r", exc)
            await record_usage(
                session,
                task="match_official_content",
                config=config,
                usage=None,
                duration_ms=int((time.monotonic() - started) * 1000),
                ref_id=batch[0].left.id,
                status="failed",
            )
            continue

        await record_usage(
            session,
            task="match_official_content",
            config=config,
            usage=usage,
            duration_ms=int((time.monotonic() - started) * 1000),
            ref_id=batch[0].left.id,
        )
        for key, decision in decisions.items():
            row = existing.get(key)
            if row is None:
                row = ContentMatch(left_raw_id=key[0], right_raw_id=key[1])
                session.add(row)
                existing[key] = row
            row.is_same_event = decision["same_event"]
            row.confidence = decision["confidence"]
            row.reason = decision["reason"][:500]
            row.model = config["model"]
            row.updated_at = utcnow()
            stats.decisions += 1
            if row.is_same_event and row.confidence >= MATCH_CONFIDENCE:
                stats.positive += 1
        await session.commit()
    return stats


async def _load_recent_official(session: AsyncSession) -> list[MatchContent]:
    latest = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    result = await session.execute(
        select(RawContent, ContentVersion)
        .join(latest, latest.c.rid == RawContent.id)
        .join(
            ContentVersion,
            (ContentVersion.raw_content_id == latest.c.rid)
            & (ContentVersion.version == latest.c.version),
        )
        .join(Source, RawContent.source_id == Source.id)
        .where(
            Source.identity == "official",
            RawContent.platform.in_(("bilibili", "youtube")),
            RawContent.published_at.is_not(None),
            RawContent.published_at >= utcnow() - _LOOKBACK,
        )
    )
    return [
        MatchContent(
            id=raw.id,
            platform=raw.platform,
            published_at=raw.published_at,
            title=(version.title or raw.external_id).strip(),
            text=_plain(version.text or "")[:800],
            category=version.category,
        )
        for raw, version in result
    ]


def _candidate_pairs(
    rows: list[MatchContent], base_ids: set[int] | None
) -> list[MatchPair]:
    pairs: list[tuple[timedelta, MatchPair]] = []
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            if left.platform == right.platform or "youtube" not in (left.platform, right.platform):
                continue
            if base_ids and left.id not in base_ids and right.id not in base_ids:
                continue
            distance = abs(left.published_at - right.published_at)
            if distance <= MATCH_WINDOW:
                ordered = (left, right) if left.id < right.id else (right, left)
                pairs.append((distance, MatchPair(*ordered)))

    # 同一 YouTube 内容只送最接近的少量候选，避免把整段时间窗交给模型盲猜。
    selected: list[MatchPair] = []
    counts: dict[int, int] = {}
    seen: set[tuple[int, int]] = set()
    for _, pair in sorted(pairs, key=lambda item: item[0]):
        if pair.key in seen:
            continue
        youtube_id = pair.left.id if pair.left.platform == "youtube" else pair.right.id
        if counts.get(youtube_id, 0) >= _MAX_CANDIDATES_PER_YOUTUBE:
            continue
        counts[youtube_id] = counts.get(youtube_id, 0) + 1
        seen.add(pair.key)
        selected.append(pair)
    return selected


_MATCH_SYSTEM_PROMPT = """你是跨平台官方内容核对器。判断两条内容是否是同一次发布的同一公告、同一视频或同一素材。
同角色、同版本、同活动但内容不同，必须判定为 false。只有标题翻译、文案翻译或平台转发形式不同，核心发布物一致时才判定为 true。
只能依据给出的标题、正文和发布时间判断，不得补充外部事实。返回 JSON：
{"matches":[{"left_id":1,"right_id":2,"same_event":true,"confidence":0.96,"reason":"简短证据"}]}
必须逐项返回所有候选，confidence 为 0 到 1。"""


def _build_prompt(pairs: list[MatchPair]) -> str:
    payload = []
    for pair in pairs:
        payload.append(
            {
                "left": {
                    "id": pair.left.id,
                    "platform": pair.left.platform,
                    "published_at": pair.left.published_at.isoformat(),
                    "title": pair.left.title,
                    "text": pair.left.text,
                },
                "right": {
                    "id": pair.right.id,
                    "platform": pair.right.platform,
                    "published_at": pair.right.published_at.isoformat(),
                    "title": pair.right.title,
                    "text": pair.right.text,
                },
            }
        )
    return json.dumps({"candidates": payload}, ensure_ascii=False)


def _parse_decisions(
    response: str, allowed_pairs: set[tuple[int, int]]
) -> dict[tuple[int, int], dict]:
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    data = json.loads(cleaned)
    result: dict[tuple[int, int], dict] = {}
    for item in data.get("matches") or []:
        key = tuple(sorted((int(item["left_id"]), int(item["right_id"]))))
        same_event = item.get("same_event")
        if key not in allowed_pairs or not isinstance(same_event, bool):
            continue
        confidence = max(0.0, min(1.0, float(item.get("confidence") or 0)))
        result[key] = {
            "same_event": same_event,
            "confidence": confidence,
            "reason": str(item.get("reason") or "模型未提供理由"),
        }
    return result


def _complete_missing_decisions(
    decisions: dict[tuple[int, int], dict],
    allowed_pairs: set[tuple[int, int]],
) -> dict[tuple[int, int], dict]:
    for key in allowed_pairs - decisions.keys():
        decisions[key] = {
            "same_event": False,
            "confidence": 0.0,
            "reason": "模型未返回该候选，按保守策略不合并",
        }
    return decisions


def _plain(value: str) -> str:
    import bleach

    return re.sub(r"\s+", " ", bleach.clean(value, tags=[], strip=True)).strip()
