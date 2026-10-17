"""采集意图打标：自然语言意图 → LLM 三档判定 → 放行/拦截。

借鉴 changedetection 的「意图 → 模型判定 → 结构化结论 → 放行/拦截」机制：
管理员给采集目标配一句自然语言意图（"只在乎官方公告和发货信息"），模型据此
对本轮每条命中输出 important / normal / ignore 与 30 字内中文依据，采集链路按
档位放行、入库或丢弃。这里是自有实现，只复用 app.services.ai 的密钥池、模型
通道与用量记账（task=intent_triage，管理端 LLM 页可见）。

容错与兜底（模型输出不可信，结构必须服务端兜住）：
- 输出可能是 Code Fence、包裹层（{"results": [...]}）、裸数组或 {id: tier} 映射；
- 档位别名（含中文）归一化，漏报与非法档位一律按 normal；
- fail-open：未配意图、AI 未配置或调用失败时全部按 normal——不丢数据、不误推送。
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import RawContent
from app.schemas.content import NormalizedContent
from app.services.ai import ai_available, chat, get_ai_config, record_usage

logger = logging.getLogger(__name__)

# 用量记账任务名：走现有 AI 通道，管理端 LLM 页按 task 统计
INTENT_TASK = "intent_triage"

TIER_IMPORTANT = "important"
TIER_NORMAL = "normal"
TIER_IGNORE = "ignore"

# 未打标（目标没配意图）：不写库、不计入统计
TIER_UNLABELED = ""

BATCH_SIZE = 20
REASON_MAX = 30
TITLE_MAX = 120
TEXT_MAX = 600

_SYSTEM = (
    "你是游戏资讯监控编辑，只根据给定监控意图判断每条内容对订阅者是否重要，"
    '并只输出 JSON：{"results":[{"id":"内容id","tier":"important|normal|ignore",'
    '"reason":"30字以内中文依据"}]}。'
    "tier 只能取三档：important=必须马上知道的重大信息（官宣、开服停服、重大变更、"
    "限时活动、抽奖、联名、发货、事故）；normal=相关但不必打扰；"
    "ignore=与意图无关、灌水、重复或无信息量。"
    "只能基于输入的标题与正文判断，禁止臆测；拿不准的一律 normal。"
    "reason 用面向普通用户的中文，不要出现 tier、id 等字段名。"
)

# 档位别名：模型大小写/中英混用都能归一化，未命中按 normal 兜底
_TIER_ALIASES = {
    TIER_IMPORTANT: {
        "important", "imp", "high", "higher", "critical", "urgent", "must", "p0",
        "重要", "高", "较高", "立即", "必推", "重大",
    },
    TIER_IGNORE: {
        "ignore", "ignored", "skip", "skipped", "drop", "dropped", "irrelevant",
        "noise", "spam", "low", "p2",
        "忽略", "丢弃", "无关", "低", "噪音", "灌水", "不相关",
    },
    TIER_NORMAL: {
        "normal", "medium", "mid", "keep", "ok", "p1",
        "一般", "普通", "中", "中等", "保留", "正常",
    },
}

_ID_KEYS = ("id", "content_id", "raw_id", "item_id", "external_id", "no", "index")
_TIER_KEYS = ("tier", "level", "label", "verdict", "judgement", "judgment", "档位", "等级")
_REASON_KEYS = ("reason", "why", "note", "comment", "依据", "理由", "原因")
_WRAPPER_KEYS = ("results", "items", "labels", "data", "verdicts", "judgements", "list")


@dataclass(frozen=True)
class IntentVerdict:
    tier: str
    reason: str = ""


@dataclass
class TriageHit:
    """一条放行命中的打标结果：raw 供 ingest 入库，external_id 供写回与投递。"""

    raw: dict
    external_id: str
    tier: str = TIER_UNLABELED
    reason: str = ""


@dataclass
class TriageOutcome:
    hits: list[TriageHit] = field(default_factory=list)
    ignored: int = 0  # 判忽略丢弃的条数（丢弃可见，计入 collect_runs）
    labeled: int = 0  # 实际送模型打标的条数

    @property
    def kept(self) -> list[dict]:
        return [hit.raw for hit in self.hits]

    @property
    def important_ids(self) -> list[str]:
        return [hit.external_id for hit in self.hits if hit.tier == TIER_IMPORTANT]


# ---------- 输出解析容错 ----------


def normalize_tier(value) -> str:
    """档位归一化：别名表命中就用，空值与非法值按 normal 兜底。"""
    key = str(value if value is not None else "").strip().casefold()
    if not key:
        return TIER_NORMAL
    for tier, aliases in _TIER_ALIASES.items():
        if key in aliases:
            return tier
    return TIER_NORMAL


def _first(entry: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in entry and entry[key] not in (None, ""):
            return entry[key]
    return None


def _clean_reason(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:REASON_MAX]


def _strip_code_fence(text: str) -> str:
    fenced = re.findall(r"```(?:json|JSON)?\s*(.*?)```", text or "", re.DOTALL)
    if fenced:
        return "\n".join(fenced).strip()
    return (text or "").strip()


def _json_spans(text: str) -> list[str]:
    """模型在 JSON 前后加说明文字时，抓取最外层 {...} / [...] 片段。"""
    spans: list[str] = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            spans.append(text[start : end + 1])
    return spans


def _loads_lenient(text: str):
    cleaned = _strip_code_fence(text)
    for candidate in (cleaned, *_json_spans(cleaned)):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None


def _iter_entries(payload) -> list[tuple[str, object]]:
    """模型输出 → (定位键, 条目)；定位键是 1 起始序号或映射表的 id。"""
    if isinstance(payload, dict):
        inner = _first(payload, _WRAPPER_KEYS)
        if isinstance(inner, (list, dict)):
            payload = inner
    if isinstance(payload, list):
        return [(str(position), entry) for position, entry in enumerate(payload, 1)]
    if isinstance(payload, dict):
        return [(str(key).strip(), entry) for key, entry in payload.items()]
    return []


def parse_verdicts(text: str) -> dict[str, IntentVerdict]:
    """模型回复 → {定位键: 结论}；解析不出任何条目时返回空字典（全部按 normal）。"""
    payload = _loads_lenient(text)
    if payload is None:
        return {}
    verdicts: dict[str, IntentVerdict] = {}
    for position, entry in _iter_entries(payload):
        if isinstance(entry, str):
            verdict = IntentVerdict(normalize_tier(entry))
        elif isinstance(entry, dict):
            verdict = IntentVerdict(
                normalize_tier(_first(entry, _TIER_KEYS)),
                _clean_reason(_first(entry, _REASON_KEYS)),
            )
        else:
            continue
        verdicts[position] = verdict
        raw_id = _first(entry, _ID_KEYS) if isinstance(entry, dict) else None
        if raw_id not in (None, ""):
            verdicts[str(raw_id).strip()] = verdict
    return verdicts


def match_verdicts(text: str, hits: list[tuple[dict, NormalizedContent | None]]) -> list[IntentVerdict]:
    """按序号/id 把模型结论对齐到命中列表；漏报与无法定位一律按 normal。"""
    verdicts = parse_verdicts(text)
    matched: list[IntentVerdict] = []
    for position, (raw, normalized) in enumerate(hits, 1):
        verdict = verdicts.get(str(position))
        if verdict is None:
            external_id = (normalized.external_id if normalized else None) or raw.get("id") or ""
            verdict = verdicts.get(str(external_id).strip()) if str(external_id).strip() else None
        matched.append(verdict or IntentVerdict(TIER_NORMAL))
    return matched


# ---------- 采集链路 ----------


def normalize_hits(items: list[dict], fetch_kind: str) -> list[tuple[dict, NormalizedContent | None]]:
    """parser 归一化命中：拿标题/正文/id 喂模型；解析失败的按未打标放行（ingest 自己记账）。"""
    from app.services.ingest import PARSERS

    registered = PARSERS.get(fetch_kind)
    if registered is None:
        return [(item, None) for item in items]
    parse, _ = registered
    hits: list[tuple[dict, NormalizedContent | None]] = []
    for item in items:
        try:
            normalized = parse(item)
        except Exception:  # noqa: BLE001 - 单条解析失败不阻断打标，交由 ingest 记 parse_failed
            normalized = None
        hits.append((item, normalized))
    return hits


def _render_user(intent: str, batch: list[tuple[dict, NormalizedContent | None]]) -> str:
    lines = [f"监控意图：{intent}", "待判定内容（编号从 1 开始，id 与编号都可定位）："]
    for position, (_, normalized) in enumerate(batch, 1):
        if normalized is None:
            lines.append(f"{position}. [无法解析的内容]")
            continue
        title = re.sub(r"\s+", " ", (normalized.title or "")).strip()[:TITLE_MAX]
        text = re.sub(r"\s+", " ", (normalized.text or "")).strip()[:TEXT_MAX]
        lines.append(f"{position}. [id] {normalized.external_id}")
        lines.append(f"   标题：{title or '（无）'}")
        lines.append(f"   正文：{text or '（无）'}")
    return "\n".join(lines)


async def _label_with_ai(
    session: AsyncSession, target, hits: list[tuple[dict, NormalizedContent | None]], intent: str
) -> list[IntentVerdict]:
    """批量送模型打标；任何失败都 fail-open 成 normal（不丢数据、不误推送）。"""
    config = await get_ai_config(session)
    if not ai_available(config):
        logger.info("intent triage skipped for target %s: AI not configured", target.id)
        return [IntentVerdict(TIER_NORMAL) for _ in hits]

    started = time.monotonic()
    verdicts: list[IntentVerdict] = []
    prompt_tokens = 0
    completion_tokens = 0
    try:
        for offset in range(0, len(hits), BATCH_SIZE):
            batch = hits[offset : offset + BATCH_SIZE]
            reply, usage = await chat(
                config, _SYSTEM, _render_user(intent, batch), json_mode=True
            )
            verdicts.extend(match_verdicts(reply, batch))
            prompt_tokens += int((usage or {}).get("prompt_tokens") or 0)
            completion_tokens += int((usage or {}).get("completion_tokens") or 0)
    except Exception as exc:  # noqa: BLE001 - 打标失败绝不影响采集主链路
        await record_usage(
            session,
            task=INTENT_TASK,
            config=config,
            usage=None,
            duration_ms=int((time.monotonic() - started) * 1000),
            ref_id=target.id,
            status="failed",
        )
        logger.warning("intent triage for target %s failed: %r", target.id, exc)
        return [IntentVerdict(TIER_NORMAL) for _ in hits]

    await record_usage(
        session,
        task=INTENT_TASK,
        config=config,
        usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        duration_ms=int((time.monotonic() - started) * 1000),
        ref_id=target.id,
    )
    return verdicts


async def triage_hits(
    session: AsyncSession,
    target,
    items: list[dict],
    fetch_kind: str,
) -> TriageOutcome:
    """意图 → LLM 判定 → 放行/拦截，返回保留的命中与忽略计数。

    目标没配意图时原样放行且不写库；配了意图但 AI 不可用/调用失败时全部按
    normal 放行（fail-open）。
    """
    hits = normalize_hits(items, fetch_kind)
    if not hits:
        return TriageOutcome()
    intent = (getattr(target, "intent", None) or "").strip()
    if not intent:
        return TriageOutcome(
            hits=[
                TriageHit(raw=raw, external_id=(normalized.external_id if normalized else ""))
                for raw, normalized in hits
            ]
        )

    verdicts = await _label_with_ai(session, target, hits, intent)
    outcome = TriageOutcome(labeled=len(hits))
    for (raw, normalized), verdict in zip(hits, verdicts):
        if verdict.tier == TIER_IGNORE:
            outcome.ignored += 1
            continue
        outcome.hits.append(
            TriageHit(
                raw=raw,
                external_id=(normalized.external_id if normalized else ""),
                tier=verdict.tier,
                reason=verdict.reason,
            )
        )
    logger.info(
        "intent triage target %s: labeled=%s kept=%s ignored=%s",
        target.id, outcome.labeled, len(outcome.hits), outcome.ignored,
    )
    return outcome


async def persist_intent_tiers(
    session: AsyncSession, platform: str, hits: list[TriageHit]
) -> dict[str, int]:
    """打标结果写回 raw_contents（AI 层产物，可重算）；返回 external_id → 内容 id。"""
    labeled = [hit for hit in hits if hit.tier and hit.external_id]
    if not labeled:
        return {}
    external_ids = [hit.external_id for hit in labeled]
    rows = (
        await session.execute(
            select(RawContent).where(
                RawContent.platform == platform, RawContent.external_id.in_(external_ids)
            )
        )
    ).scalars()
    by_external = {row.external_id: row for row in rows}
    now = utcnow()
    for hit in labeled:
        row = by_external.get(hit.external_id)
        if row is None:
            continue
        row.intent_tier = hit.tier
        row.intent_reason = hit.reason or None
        row.intent_labeled_at = now
    await session.commit()
    return {external_id: row.id for external_id, row in by_external.items()}
