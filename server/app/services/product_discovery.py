"""AI 从官方正文自动建档、关联同款与启动搜索；交付后的安静期才归档。"""

from datetime import datetime, timedelta
import logging
import re
import time
import unicodedata

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.communities import COMMUNITIES, get_community
from app.models import (
    ContentVersion,
    MerchScan,
    Product,
    ProductCandidate,
    RawContent,
    Source,
)
from app.services.urls import safe_link
from app.services.ai import ai_available, chat, get_ai_config, record_usage
from app.services.product_discovery_ai import MERCH_SYSTEM, MerchItem, compact_name, parse_merch_response
from app.services.product_media import merch_message, official_image_candidates

logger = logging.getLogger(__name__)

GOODS_NOUNS = (
    "机械键盘", "鼠标垫", "亚克力", "键盘", "键帽", "手办", "立牌", "徽章",
    "色纸", "耳机", "挂件", "海报", "卫衣", "吧唧",
)
_GOODS_SORTED = tuple(sorted(GOODS_NOUNS, key=len, reverse=True))
QUIET_DAYS = 30
LOOKBACK_DAYS = 45
SCAN_LIMIT = 20
_TERM_FILLER = re.compile(r"联名|机械|限定|周边")


def _plain(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", "", text).strip()


def goods_noun(text: str) -> str | None:
    for noun in _GOODS_SORTED:
        if noun in text:
            return noun
    return None


def _valid_term(term: str, product_type: str | None = None) -> bool:
    compact = _plain(term)
    return len(compact) >= (3 if product_type else 4) and (goods_noun(compact) is not None or bool(
        product_type and _plain(product_type).casefold() in compact.casefold()
    ))


def _game_aliases(community: str | None) -> set[str]:
    if not community:
        return set()
    try:
        meta = get_community(community)
    except Exception:  # noqa: BLE001 - 未知分区当作没有可剥的游戏名
        return set()
    return {
        _plain(alias).casefold()
        for alias in (*meta.game_names, meta.label, *meta.source_aliases, *meta.extra_names)
        if alias
    } - {""}


def _search_watch_terms(
    search_keyword: str | None, product_type: str | None, community: str | None,
) -> list[str]:
    """搜索词去掉游戏名后的紧凑组合：「鸣潮 长离 手办」→「长离手办」。

    监控搜索抓回的标题常只写“角色名+品类”（长离手办开箱），官方全名匹配不上；
    这组词就是搜索引擎实际命中的组合，必须进词表，否则专属监控空转。
    """
    tokens = [token for token in (_plain(part) for part in (search_keyword or "").split()) if token]
    if not tokens:
        return []
    aliases = _game_aliases(community)
    core: list[str] = []
    for token in tokens:
        folded = token.casefold()
        if folded in aliases:
            continue
        for alias in sorted(aliases, key=len, reverse=True):
            if len(alias) >= 2 and folded.startswith(alias):
                token = token[len(alias):]
                break
        if token:
            core.append(token)
    terms: list[str] = []
    for joined in ("".join(core), _TERM_FILLER.sub("", "".join(core))):
        if len(core) >= 2 and _valid_term(joined, product_type) and joined not in terms:
            terms.append(joined)
    return terms


def build_watch_terms(
    name: str, aliases: list[str] | None = None, product_type: str | None = None,
    *, search_keyword: str | None = None, community: str | None = None,
) -> list[str]:
    """四到六个匹配词。过短或没有品类的叫法丢掉，避免角色名把审核上下文撑大。"""
    terms: list[str] = []
    for raw in (name, *(aliases or [])):
        compact = _plain(raw)
        if _valid_term(compact, product_type) and compact not in terms:
            terms.append(compact)
        shorter = _TERM_FILLER.sub("", compact)
        if _valid_term(shorter, product_type) and shorter not in terms:
            terms.append(shorter)
        if len(terms) >= 4:
            break
    for casual in _search_watch_terms(search_keyword, product_type, community):
        if casual not in terms:
            terms.append(casual)
    return terms[:6]


def build_search_keyword(name: str, product_type: str | None = None) -> str:
    """一路稍宽的搜索词：名字里的具体部分加品类，别名不再各开一路。"""
    compact = _plain(name)
    noun = goods_noun(compact) or product_type or ""
    search_noun = "键盘" if noun == "机械键盘" else noun
    prefix = _TERM_FILLER.sub("", compact.replace(noun, "", 1)).strip()
    if len(prefix) >= 2 and search_noun:
        keyword = f"{prefix} {search_noun}"
    else:
        keyword = compact
    keyword = keyword.strip()[:64]
    if keyword.startswith("-") or not keyword:
        return compact[:64]
    return keyword


async def _store_discovered_product(session: AsyncSession, item: MerchItem, raw: RawContent, version: ContentVersion,
                                    images: list[str] | None = None) -> tuple[Product, bool]:
    from app.services.product_monitoring import configure_product_monitor

    catalog = list(await session.scalars(select(Product).where(Product.community == raw.community).with_for_update()))
    product = next((p for p in catalog if p.id == item.existing_product_id), None)
    if item.existing_product_id and product is None:
        raise ValueError("AI 关联了本分区不存在的商品")
    if product is None:
        # 精确名称去重；有歧义的简称由 AI 对照已有型号判断，不能按包含关系吞并新款。
        product = next((p for p in catalog if compact_name(p.name) == compact_name(item.name)), None)
    candidate = await session.scalar(select(ProductCandidate).where(
        ProductCandidate.community == raw.community, ProductCandidate.name == item.name,
    ).order_by(ProductCandidate.id.desc()).limit(1))
    if candidate and candidate.status == "ignored":
        raise ValueError("该商品已被管理员忽略")
    terms = build_watch_terms(
        item.name, item.aliases, item.product_type,
        search_keyword=item.search_keyword or build_search_keyword(item.name, item.product_type),
        community=raw.community,
    )
    if not terms:
        raise ValueError("商品缺少包含品类的完整匹配词")
    if candidate is None:
        candidate = ProductCandidate(community=raw.community, name=item.name)
        session.add(candidate)
    candidate.aliases = item.aliases
    candidate.watch_terms = terms
    candidate.search_keyword = item.search_keyword or build_search_keyword(item.name, item.product_type)
    candidate.price_note = item.price_note
    candidate.presale_note = item.presale_note
    candidate.ship_note = item.ship_note
    candidate.source_url = safe_link(raw.url)
    candidate.raw_content_id = raw.id
    candidate.details = item.model_dump(mode="json")
    await session.flush()
    created = product is None
    if product is None:
        product = Product(slug=f"merch-{candidate.id}", name=item.name, community=raw.community,
                          watch_terms=terms, monitor_status="watching", monitor_started_at=utcnow())
        session.add(product)
        await session.flush()
    else:
        product.watch_terms = list(dict.fromkeys([*(product.watch_terms or []), *terms]))[:8]
    if product.monitor_target_id is None and product.monitor_status == "watching":
        try:
            async with session.begin_nested():
                await configure_product_monitor(session, product, enabled=True, keyword=candidate.search_keyword)
        except ValueError:
            keyword = f"{get_community(raw.community).bare_name} {item.name}"[:64]
            await configure_product_monitor(session, product, enabled=True, keyword=keyword)
    # 新公告只更新其实际给出的资料，旧公告回填不能覆盖较新的登记。
    published = raw.published_at or raw.collected_at
    previous_at = (product.discovery or {}).get("published_at")
    if not previous_at or published.isoformat() >= previous_at:
        for field, value in (("price_note", item.price_note), ("presale_at", item.presale_note),
                             ("balance_window", item.balance_note), ("ship_note", item.ship_note),
                             ("expected_ship_at", item.expected_ship_at)):
            if value is not None:
                setattr(product, field, value)
        product.source_url = safe_link(raw.url)
        previous = product.discovery or {}
        product.discovery = {**previous, "raw_content_id": raw.id, "content_version": version.version,
                             "product_type": item.product_type,
                             "images": images or previous.get("images") or [],
                             "images_source_url": safe_link(raw.url) if images else previous.get("images_source_url"),
                             "quote": item.quote, "published_at": published.isoformat()}
    # 较早公告也能补上缺图，商品价格和进度仍由较新的公告决定。
    if images and not (product.discovery or {}).get("images"):
        product.discovery = {**(product.discovery or {}), "images": images, "images_source_url": safe_link(raw.url)}
    candidate.status = "accepted"
    candidate.product_id = product.id
    return product, created


async def discover_merch_candidates(
    session: AsyncSession, *, limit: int = SCAN_LIMIT, now: datetime | None = None,
    content_ids: list[int] | None = None,
) -> dict:
    """逐版本 AI 识别并直接启动追踪。模型/协议失败十分钟后自动重试。"""
    now = now or utcnow()
    config = await get_ai_config(session)
    if not ai_available(config):
        return {"scanned": 0, "created": 0, "skipped_reason": "ai_not_configured"}
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    latest = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    rows = list(await session.execute(
        select(RawContent, ContentVersion, Source)
        .join(Source, Source.id == RawContent.source_id)
        .join(latest, latest.c.rid == RawContent.id)
        .join(
            ContentVersion,
            (ContentVersion.raw_content_id == latest.c.rid)
            & (ContentVersion.version == latest.c.version),
        )
        .outerjoin(MerchScan, MerchScan.raw_content_id == RawContent.id)
        .where(
            Source.identity == "official",
            RawContent.community.in_([key for key, meta in COMMUNITIES.items() if meta.domain == "game"]),
            *([RawContent.id.in_(content_ids)] if content_ids is not None else []),
            RawContent.hidden.is_(False),
            or_(RawContent.collected_at >= cutoff, MerchScan.raw_content_id.is_not(None)),
            or_(MerchScan.raw_content_id.is_(None),
                (MerchScan.content_version != ContentVersion.version) & (
                    (MerchScan.scanned_at <= now - timedelta(minutes=10))
                    | (ContentVersion.parsed_at > MerchScan.scanned_at))),
        )
        .order_by(MerchScan.scanned_at, RawContent.id.desc())
        .limit(limit)
    ))
    created = 0
    scanned = 0
    content_versions = [(raw.id, version.version) for raw, version, _source in rows]
    for raw_id, version_number in content_versions:
        raw = await session.get(RawContent, raw_id, populate_existing=True)
        version = await session.scalar(select(ContentVersion).where(
            ContentVersion.raw_content_id == raw_id, ContentVersion.version == version_number))
        if raw is None or version is None:
            continue
        scan = await session.get(MerchScan, raw_id)
        if scan is None:
            scan = MerchScan(raw_content_id=raw_id)
            session.add(scan)
        scan.scanned_at = max(now, utcnow())
        await session.commit()
        community = raw.community or ""
        if get_community(community).domain != "game":
            scan.content_version = version_number
            scan.result = {}
            await session.commit()
            continue
        catalog = list(await session.scalars(select(Product).where(Product.community == community)))
        from app.services.ai import _plain_evidence
        title, text = version.title or "", _plain_evidence(version.text or "")[:12000]
        images = official_image_candidates(version, raw.url)
        payload = {
            "game": get_community(community).game_name, "title": title, "text": text,
            "published_at": (raw.published_at or raw.collected_at).isoformat() + "Z",
            "existing_products": [{"id": p.id, "name": p.name, "aliases": p.watch_terms or []} for p in catalog],
        }
        await session.commit()
        started, usage = time.monotonic(), None
        try:
            message = await merch_message(payload, images)
            response, usage = await chat(config, MERCH_SYSTEM, message, json_mode=True)
            items = parse_merch_response(response, title=title, text=text, published_at=raw.published_at or raw.collected_at,
                                         image_ids={image["id"] for image in images})
            confirmed = [item for item in items if item.confidence >= 85]
            # 模型运行期间可能有内容修订/下架，旧版本不能落地建档。
            current = await session.scalar(select(ContentVersion.version).where(
                ContentVersion.raw_content_id == raw_id).order_by(ContentVersion.version.desc()).limit(1))
            await session.refresh(raw)
            if raw.hidden or current != version_number:
                await record_usage(session, task="discover_products", config=config, usage=usage,
                                   duration_ms=int((time.monotonic() - started) * 1000), ref_id=raw_id)
                continue
            from app.services.fulfillment_automation import SIGNAL_TYPES
            from app.services.product_matching import matches_product_text
            signals = {}
            matched_ids = {p.id for p in catalog if matches_product_text(title, text, p.watch_terms or [])}
            new_count = 0
            async with session.begin_nested():
                ignored = set(await session.scalars(select(ProductCandidate.name).where(
                    ProductCandidate.community == community, ProductCandidate.status == "ignored")))
                for item in confirmed:
                    if item.name in ignored:
                        continue
                    selected_images = [image["url"] for image in images if image["id"] in item.image_ids]
                    product, is_new = await _store_discovered_product(session, item, raw, version, selected_images)
                    new_count += int(is_new)
                    matched_ids.add(product.id)
                    for fact in item.facts:
                        signals[(product.id, fact.signal_type)] = {
                            "product_id": product.id, "signal_type": fact.signal_type, "verdict": "supported",
                            "confidence": fact.confidence, "quote": fact.quote,
                            "reason": "官方正文明确说明", "acquisition_type": "unknown",
                        }
                for product_id in matched_ids:
                    for kind in SIGNAL_TYPES:
                        signals.setdefault((product_id, kind), {
                            "product_id": product_id, "signal_type": kind, "verdict": "uncertain",
                            "confidence": 0, "quote": "", "reason": "原文未涉及", "acquisition_type": "unknown",
                        })
                scan.content_version = version_number if len(confirmed) == len(items) else 0
                scan.result = {"products": [item.model_dump(mode="json") for item in items], "fulfillment": {
                    "schema_version": 2, "content_version": version_number, "kind": "none", "confidence": 95,
                    "reason": "官方周边正文分析", "signals": list(signals.values()),
                }}
                await session.flush()
                if matched_ids:
                    from app.services.events import run_aggregation
                    await run_aggregation(session)
            await record_usage(session, task="discover_products", config=config, usage=usage,
                               duration_ms=int((time.monotonic() - started) * 1000), ref_id=raw_id)
            created += new_count
            scanned += 1
        except Exception:
            await session.rollback()
            logger.exception("商品 AI 发现失败，稍后重试 content_id=%s", raw_id)
            await record_usage(session, task="discover_products", config=config, usage=usage,
                               duration_ms=int((time.monotonic() - started) * 1000), ref_id=raw_id, status="failed")
    return {"scanned": scanned, "created": created}


async def accept_product_candidate(session: AsyncSession, candidate_id: int) -> Product:
    from app.services.events import run_aggregation
    from app.services.product_monitoring import configure_product_monitor

    candidate = await session.get(ProductCandidate, candidate_id, with_for_update=True)
    if candidate is None:
        raise ValueError("candidate_not_found")
    if candidate.status != "pending":
        raise ValueError("candidate_not_pending")
    terms = [term for term in (candidate.watch_terms or []) if _valid_term(str(term))]
    if not terms:
        terms = build_watch_terms(
            candidate.name, candidate.aliases or [],
            search_keyword=candidate.search_keyword or build_search_keyword(candidate.name),
            community=candidate.community,
        )
    if not terms:
        raise ValueError("candidate_terms_invalid")
    product = Product(
        slug=f"merch-{candidate.id}",
        name=candidate.name,
        community=candidate.community,
        price_note=candidate.price_note,
        presale_at=candidate.presale_note,
        ship_note=candidate.ship_note,
        watch_terms=terms,
        source_url=safe_link(candidate.source_url),
        monitor_status="watching",
    )
    session.add(product)
    await session.flush()
    await configure_product_monitor(
        session, product, enabled=True, keyword=candidate.search_keyword or build_search_keyword(candidate.name),
    )
    candidate.status = "accepted"
    candidate.product_id = product.id
    await run_aggregation(session)
    await session.commit()
    await session.refresh(product)
    return product


async def ignore_product_candidate(session: AsyncSession, candidate_id: int) -> None:
    candidate = await session.get(ProductCandidate, candidate_id, with_for_update=True)
    if candidate is None:
        raise ValueError("candidate_not_found")
    if candidate.status != "pending":
        raise ValueError("candidate_not_pending")
    candidate.status = "ignored"
    await session.commit()


async def rebuild_product_watch_terms(session: AsyncSession, product: Product) -> list[str]:
    """按最新登记与现役监控词重算匹配词，并与旧词合并。

    早期建档的商品词表里只有官方全名，监控搜索抓回的口语标题
    （长离手办开箱）匹配不上、专属监控空转；重建把搜索词派生的
    口语组合补进来。旧词保留，避免正在跟踪的写法被清掉。
    """
    from app.models import CollectTarget

    aliases: list[str] = []
    product_type = (product.discovery or {}).get("product_type")
    keyword: str | None = None
    candidates = list(await session.scalars(
        select(ProductCandidate).where(ProductCandidate.product_id == product.id)
        .order_by(ProductCandidate.id.desc()).limit(5)
    ))
    for candidate in candidates:
        details = candidate.details if isinstance(candidate.details, dict) else {}
        aliases.extend(alias for alias in (details.get("aliases") or []) if isinstance(alias, str))
        product_type = product_type or details.get("product_type")
        keyword = keyword or candidate.search_keyword
    if product.monitor_target_id:
        target = await session.get(CollectTarget, product.monitor_target_id)
        if target is not None and target.target_type == "keyword_search":
            keyword = target.target_value or keyword
    rebuilt = build_watch_terms(
        product.name, aliases, product_type,
        search_keyword=keyword or build_search_keyword(product.name, product_type),
        community=product.community,
    )
    merged = list(dict.fromkeys([*(product.watch_terms or []), *rebuilt]))[:8]
    product.watch_terms = merged
    return merged


async def archive_product(session: AsyncSession, product: Product, *, now: datetime | None = None) -> None:
    from app.models import CollectTarget

    now = now or utcnow()
    product.monitor_status = "archived"
    product.archived_at = now
    product.stage_hint = -1
    product.stage_hint_source = None
    if product.monitor_target_id:
        target = await session.get(CollectTarget, product.monitor_target_id)
        if target is not None:
            target.enabled = False


async def archive_quiet_products(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """已有交付证据，且最近一次进展与本轮跟踪都安静 30 天，才结束专属搜索。"""
    now = now or utcnow()
    products = list(await session.scalars(
        select(Product).where(Product.monitor_status == "watching").with_for_update()
    ))
    archived = 0
    deadline = now - timedelta(days=QUIET_DAYS)
    for product in products:
        from app.services.fulfillment import _active_signal_rows

        rows = [(signal, evidence.acquisition_type) for signal, evidence, _, _ in await _active_signal_rows(session, product.id)
                if signal.review_status == "verified"]
        delivery_times = [signal.occurred_at for signal, acquisition in rows if signal.occurred_at and (
            (signal.signal_type == "shipping_started" and signal.actor_type == "official")
            or (signal.signal_type == "consumer_received" and acquisition != "seeded" and signal.actor_type != "seeded")
        )]
        if not delivery_times or (product.expected_ship_at and product.expected_ship_at > now):
            continue
        delivery_at = max(delivery_times)
        # 新一轮预售、补款或延期意味着仍在等交付，不能把早期样品/旧批次当作完成。
        if any(signal.actor_type == "official" and signal.signal_type in {"presale_open", "balance_open", "delayed"}
               and signal.occurred_at and signal.occurred_at > delivery_at for signal, _ in rows):
            continue
        times = [signal.occurred_at for signal, _ in rows if signal.occurred_at]
        baseline = max([*times, product.monitor_started_at or product.created_at])
        if baseline is not None and baseline <= deadline:
            await archive_product(session, product, now=now)
            archived += 1
    await session.commit()
    return {"checked": len(products), "archived": archived}


def public_brief(product: Product, fulfillment: dict, signals: list[dict]) -> dict:
    """公开页的三件事：实物开箱、质量反馈、官方说明。不把未写明的开箱说成自购到货。"""
    unbox_times = [
        signal["occurred_at"] for signal in signals
        if signal.get("signal_type") == "consumer_received"
        and signal.get("review_status") == "verified"
        and signal.get("occurred_at")
    ]
    quality_times = [
        signal["occurred_at"] for signal in signals
        if signal.get("signal_type") == "quality_issue"
        and signal.get("review_status") == "verified"
        and signal.get("occurred_at")
    ]
    consumer = fulfillment["consumer"]
    accounts = (
        int(fulfillment.get("observation_count") or 0)
        + int(fulfillment.get("sample_count") or 0)
        + int(consumer.get("verified_count") or 0)
    )
    if accounts == 0:
        unboxing_note = "尚无开箱分享"
    elif int(consumer.get("verified_count") or 0) == 0 and int(fulfillment.get("sample_count") or 0) == 0:
        unboxing_note = ""
    elif int(consumer.get("verified_count") or 0) == 0:
        unboxing_note = "含送测样品，未计入自购"
    else:
        unboxing_note = f"其中 {int(consumer['verified_count'])} 个独立账号写明自购"
    official = fulfillment["official"]
    if int(official.get("verified_count") or 0):
        latest = next((signal for signal in signals if signal.get("actor_type") == "official"
                       and signal.get("review_status") == "verified"
                       and signal.get("signal_type") == official.get("state")), None)
        official_text = ((latest or {}).get("claim_quote") or official.get("summary")
                         or official.get("state_label") or "")
        if official.get("state") in {"presale_open", "balance_open", "delayed"}:
            for note in (product.price_note, product.presale_at, product.balance_window, product.ship_note):
                if note and note not in official_text:
                    official_text += f"；{note}"
        official_registered = False
    else:
        parts = []
        if product.price_note:
            parts.append(product.price_note)
        if product.presale_at:
            parts.append(f"预售 {product.presale_at}")
        if product.balance_window:
            parts.append(f"尾款 {product.balance_window}")
        if product.original_ship_at:
            parts.append(f"原定发货 {product.original_ship_at}")
        if product.ship_note:
            parts.append(product.ship_note)
        official_text = "；".join(parts)
        official_registered = bool(parts) and not bool(product.discovery)
        if not official_text:
            official_text = (product.discovery or {}).get("quote") or "暂无新公告"
    return {
        "archived": product.monitor_status == "archived",
        "unboxing_accounts": accounts,
        "unboxing_from": min(unbox_times) if unbox_times else None,
        "unboxing_to": max(unbox_times) if unbox_times else None,
        "unboxing_note": unboxing_note,
        "quality_accounts": int(fulfillment["quality"].get("verified_count") or 0),
        "quality_latest": max(quality_times) if quality_times else None,
        "official_text": official_text,
        "official_registered": official_registered,
    }


async def run_product_monitor_loop(session: AsyncSession, *, now: datetime | None = None) -> dict:
    from app.services.product_media import backfill_product_images

    discovered = await discover_merch_candidates(session, now=now)
    images = await backfill_product_images(session, now=now)
    archived = await archive_quiet_products(session, now=now)
    return {"discover": discovered, "images": images, "archive": archived}
