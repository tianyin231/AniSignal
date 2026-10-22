"""从官方内容发现周边，接受后准备一路搜索，安静期满后归档。

关键词搜索本身没有做降频。每个窗口中的商品一路搜索词，匹配词两到四个，
并且必须带商品品类。归档后退出履约审核上下文，专属搜索停下。
"""

from datetime import datetime, timedelta
import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.communities import get_community
from app.models import (
    ContentVersion,
    MerchScan,
    Product,
    ProductCandidate,
    ProductSignal,
    RawContent,
    Source,
)
from app.services.urls import safe_link

GOODS_NOUNS = (
    "机械键盘", "鼠标垫", "亚克力", "键盘", "键帽", "手办", "立牌", "徽章",
    "色纸", "耳机", "挂件", "海报", "卫衣", "吧唧",
)
_GOODS_SORTED = tuple(sorted(GOODS_NOUNS, key=len, reverse=True))
_QUIET_TYPES = frozenset({"consumer_received", "shipping_started", "delayed"})
QUIET_DAYS = 30
LOOKBACK_DAYS = 45
SCAN_LIMIT = 20
_PREFIX_NOISE = re.compile(r"^(?:鸣潮|库洛游戏|库洛|官方|关于|现已|正式|开启|公布)")
_TERM_FILLER = re.compile(r"联名|机械|限定|周边")
_PRICE = re.compile(r"[¥￥]\s*(\d{2,5}(?:\.\d{1,2})?)")
_SENTENCE = re.compile(r"[。！？!?；;\n]+")


def _plain(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", "", text).strip()


def goods_noun(text: str) -> str | None:
    for noun in _GOODS_SORTED:
        if noun in text:
            return noun
    return None


def extract_merch_name(title: str | None) -> str | None:
    """标题里要有品类，而且品类前后还得有具体名字。单独的「键盘」不算。"""
    plain = _plain(title)
    noun = goods_noun(plain)
    if noun is None:
        return None
    left = re.split(r"[，,。！？!?；;：:《》「」\"'（）()【】\[\]|｜/\\\-]+", plain[:plain.find(noun)])[-1]
    left = _PREFIX_NOISE.sub("", left)
    name = f"{left}{noun}"
    if name == noun or len(name) < 4 or len(name) > 32:
        return None
    return name


def _valid_term(term: str) -> bool:
    compact = _plain(term)
    return len(compact) >= 4 and goods_noun(compact) is not None


def build_watch_terms(name: str, aliases: list[str] | None = None) -> list[str]:
    """两到四个匹配词。过短或没有品类的叫法丢掉，避免角色名把审核上下文撑大。"""
    terms: list[str] = []
    for raw in (name, *(aliases or [])):
        compact = _plain(raw)
        if _valid_term(compact) and compact not in terms:
            terms.append(compact)
        shorter = _TERM_FILLER.sub("", compact)
        if _valid_term(shorter) and shorter not in terms:
            terms.append(shorter)
        if len(terms) >= 4:
            break
    return terms[:4]


def build_search_keyword(name: str) -> str:
    """一路稍宽的搜索词：名字里的具体部分加品类，别名不再各开一路。"""
    compact = _plain(name)
    noun = goods_noun(compact) or ""
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


def _sentence_with(text: str, pattern: str, limit: int = 64) -> str | None:
    for part in _SENTENCE.split(text or ""):
        piece = re.sub(r"\s+", " ", part).strip()
        if piece and re.search(pattern, piece):
            return piece[:limit]
    return None


def notes_from_text(text: str | None) -> dict[str, str | None]:
    raw = text or ""
    price = _PRICE.search(raw)
    return {
        "price_note": f"¥{price.group(1)}" if price else None,
        "presale_note": _sentence_with(raw, r"预售|预订|预约"),
        "ship_note": _sentence_with(raw, r"发货|出货|尾款"),
    }


def _compact_key(value: str) -> str:
    return _plain(value).casefold()


async def _tracked_names(session: AsyncSession, community: str) -> set[str]:
    rows = await session.execute(
        select(Product.name, Product.watch_terms).where(Product.community == community)
    )
    names: set[str] = set()
    for name, terms in rows:
        names.add(_compact_key(name))
        for term in terms or []:
            if isinstance(term, str):
                names.add(_compact_key(term))
    pending = await session.scalars(
        select(ProductCandidate.name).where(
            ProductCandidate.community == community,
            ProductCandidate.status == "pending",
        )
    )
    names.update(_compact_key(item) for item in pending)
    return {item for item in names if item}


def _already_known(name: str, known: set[str]) -> bool:
    key = _compact_key(name)
    if key in known:
        return True
    return any(key in item or item in key for item in known if len(item) >= 4)


async def discover_merch_candidates(
    session: AsyncSession, *, limit: int = SCAN_LIMIT, now: datetime | None = None,
) -> dict:
    """扫尚未看过的官方内容。没有候选也会记下，下一轮不重复扫。"""
    now = now or utcnow()
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
        .where(
            Source.identity == "official",
            RawContent.hidden.is_(False),
            RawContent.collected_at >= cutoff,
            ~select(MerchScan.raw_content_id).where(MerchScan.raw_content_id == RawContent.id).exists(),
        )
        .order_by(RawContent.id.desc())
        .limit(limit)
    ))
    created = 0
    known_by_community: dict[str, set[str]] = {}
    for raw, version, _source in rows:
        session.add(MerchScan(raw_content_id=raw.id, scanned_at=now))
        community = raw.community or ""
        if get_community(community).domain != "game":
            continue
        name = extract_merch_name(version.title)
        if name is None:
            continue
        if community not in known_by_community:
            known_by_community[community] = await _tracked_names(session, community)
        known = known_by_community[community]
        if _already_known(name, known):
            continue
        terms = build_watch_terms(name)
        if not terms:
            continue
        notes = notes_from_text(version.text or version.title)
        session.add(ProductCandidate(
            community=community,
            name=name,
            aliases=terms[1:],
            watch_terms=terms,
            search_keyword=build_search_keyword(name),
            price_note=notes["price_note"],
            presale_note=notes["presale_note"],
            ship_note=notes["ship_note"],
            source_url=safe_link(raw.url),
            raw_content_id=raw.id,
            status="pending",
        ))
        known.add(_compact_key(name))
        known.update(_compact_key(term) for term in terms)
        created += 1
    await session.commit()
    return {"scanned": len(rows), "created": created}


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
        terms = build_watch_terms(candidate.name, candidate.aliases or [])
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
    """连续约 30 天没有新开箱，也没有新的官方延期或发货，就归档并停掉专属搜索。"""
    now = now or utcnow()
    products = list(await session.scalars(
        select(Product).where(Product.monitor_status == "watching").with_for_update()
    ))
    archived = 0
    deadline = now - timedelta(days=QUIET_DAYS)
    for product in products:
        latest = await session.scalar(
            select(func.max(ProductSignal.occurred_at)).where(
                ProductSignal.product_id == product.id,
                ProductSignal.active.is_(True),
                ProductSignal.review_status == "verified",
                ProductSignal.signal_type.in_(_QUIET_TYPES),
                ProductSignal.community == product.community,
            )
        )
        baseline = latest or product.created_at
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
        unboxing_note = "还没有采纳的实物开箱"
    elif int(consumer.get("verified_count") or 0) == 0 and int(fulfillment.get("sample_count") or 0) == 0:
        unboxing_note = "购买方式未注明"
    elif int(consumer.get("verified_count") or 0) == 0:
        unboxing_note = "含送测样品，未计入自购"
    else:
        unboxing_note = f"其中 {int(consumer['verified_count'])} 个独立账号写明自购"
    official = fulfillment["official"]
    if int(official.get("verified_count") or 0):
        official_text = official.get("state_label") or ""
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
        official_registered = bool(parts)
        if not official_text:
            official_text = "官方来源里还没有这件商品的发货或延期原文"
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
    discovered = await discover_merch_candidates(session, now=now)
    archived = await archive_quiet_products(session, now=now)
    return {"discover": discovered, "archive": archived}
