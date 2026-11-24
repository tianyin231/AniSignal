"""站内全文检索：MySQL 8.4 ngram FULLTEXT 优先，短词/标点/SQLite 自动回退安全 LIKE。

特性：
- 标题 + 正文 + 外文机翻（content_translations.text）跨语言统一检索；
- MySQL 下各表独立走 FULLTEXT 索引再 UNION 合并主键，避免 OR 导致索引失效；
- 自动剥离 BOOLEAN MODE 运算符，避开 InnoDB 默认英文停用词与标点分词陷阱（如 "2.4"）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from sqlalchemy import and_, exists, or_, select, text, union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import ContentTranslation, ContentVersion, Event, Evidence, RawContent

logger = logging.getLogger(__name__)

_REQUIRED_FTS_INDEXES = (
    "ft_content_versions_title_text",
    "ft_content_translations_text",
    "ft_events_title_summary",
)
_FTS_CAPABILITY_CACHE: WeakKeyDictionary[object, bool] = WeakKeyDictionary()

# MySQL InnoDB 内置默认停用词（INFORMATION_SCHEMA.INNODB_FT_DEFAULT_STOPWORD）。
# ngram_token_size=2 时，若词项的所有 2-gram 均在停用词表中，BOOLEAN MODE 必填项会返回 0 行。
_INNODB_STOPWORDS = frozenset({
    "a", "about", "an", "are", "as", "at", "be", "by", "com", "de", "en",
    "for", "from", "how", "i", "in", "is", "it", "la", "of", "on", "or",
    "that", "the", "this", "to", "was", "what", "when", "where", "who",
    "will", "with", "und", "www",
})

_NGRAM_SIZE = 2
_MAX_TERMS = 8
# InnoDB ngram 索引内的有效字符段（数字、拉丁字母、中日韩文字）
_ALNUM_SEGMENT = re.compile(r"[0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
# MySQL FULLTEXT BOOLEAN MODE 特殊字符（防止语法报错或非预期语义）
_BOOLEAN_META = re.compile(r'[+\-<>()~*"@]+')


@dataclass(frozen=True)
class ParsedSearchQuery:
    raw: str
    terms: tuple[str, ...]
    fts_segments: tuple[str, ...]
    like_terms: tuple[str, ...]

    @property
    def boolean_expr(self) -> str:
        if not self.fts_segments:
            return ""
        return " ".join(f'+"{seg}"' for seg in self.fts_segments)


def _has_indexed_ngram(segment: str) -> bool:
    """判断分段在 ngram_token_size=2 下是否至少包含一个非停用词 bigram。"""
    if len(segment) < _NGRAM_SIZE:
        return False
    lowered = segment.lower()
    if lowered in _INNODB_STOPWORDS:
        return False
    for i in range(len(lowered) - _NGRAM_SIZE + 1):
        if lowered[i : i + _NGRAM_SIZE] not in _INNODB_STOPWORDS:
            return True
    return False


def _contains_stopword_ngram(segment: str) -> bool:
    """若英文段内含 2 字符停用词 bigram（如 discord 中的 is/or），FTS 短语可能略宽，需用 LIKE 复核。"""
    lowered = segment.lower()
    if not lowered.isascii():
        return False
    for i in range(len(lowered) - _NGRAM_SIZE + 1):
        if lowered[i : i + _NGRAM_SIZE] in _INNODB_STOPWORDS:
            return True
    return False


def parse_search_query(q: str | None) -> ParsedSearchQuery:
    """拆解用户查询为 MySQL ngram BOOLEAN 表达式 + 精确 LIKE 复核词。"""
    raw = (q or "").strip()
    if not raw:
        return ParsedSearchQuery(raw="", terms=(), fts_segments=(), like_terms=())

    terms: list[str] = []
    seen_terms: set[str] = set()
    for token in raw.split():
        cleaned = token.strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key not in seen_terms:
            seen_terms.add(key)
            terms.append(cleaned)
        if len(terms) >= _MAX_TERMS:
            break

    fts_segments: list[str] = []
    seen_fts: set[str] = set()
    like_terms: list[str] = []

    for term in terms:
        safe = _BOOLEAN_META.sub(" ", term)
        segments = _ALNUM_SEGMENT.findall(safe)
        eligible = [seg for seg in segments if _has_indexed_ngram(seg)]
        for seg in eligible:
            key = seg.casefold()
            if key not in seen_fts:
                seen_fts.add(key)
                fts_segments.append(seg)

        # 以下情况须保留原词做精确 LIKE 复核：
        # 1) 含标点/多段（如 "2.4"、"v2.4"、"鸣潮·前瞻"）；
        # 2) 无可用 FTS 段（单字、纯符号、纯停用词）；
        # 3) 纯 ASCII 词包含 2-char 停用词 bigram（防止 FTS 跳词产生宽匹配）。
        needs_like = (
            not eligible
            or len(segments) != 1
            or segments[0] != term
            or _contains_stopword_ngram(segments[0])
        )
        if needs_like:
            like_terms.append(term)

    return ParsedSearchQuery(
        raw=raw,
        terms=tuple(terms),
        fts_segments=tuple(fts_segments),
        like_terms=tuple(like_terms),
    )


def is_mysql_session(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", None) in ("mysql", "mariadb")


def reset_fts_capability_cache() -> None:
    _FTS_CAPABILITY_CACHE.clear()


async def supports_mysql_fts(session: AsyncSession) -> bool:
    """探测并缓存当前数据库是否已具备全部 3 个 ngram FULLTEXT 索引。

    MySQL 8.4 标准部署仅首次查询一次 information_schema.statistics（~0.5ms）即缓存 True；
    外置 MariaDB / 无 ngram 插件的数据库会缓存 False 并无缝回退到安全 LIKE，避免运行时报错。
    """
    if not is_mysql_session(session):
        return False
    bind = session.get_bind()
    cache_key = getattr(bind, "engine", bind)
    try:
        cached = _FTS_CAPABILITY_CACHE.get(cache_key)
    except TypeError:
        cache_key = None
        cached = None
    if cached is not None:
        return cached
    try:
        count = await session.scalar(
            text(
                "SELECT COUNT(DISTINCT index_name) "
                "FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() "
                "AND index_name IN (:i1, :i2, :i3)"
            ).bindparams(
                i1=_REQUIRED_FTS_INDEXES[0],
                i2=_REQUIRED_FTS_INDEXES[1],
                i3=_REQUIRED_FTS_INDEXES[2],
            )
        )
        supported = int(count or 0) == len(_REQUIRED_FTS_INDEXES)
    except Exception:  # noqa: BLE001 - 临时异常不写缓存，本次安全回退 LIKE
        logger.warning("探测 MySQL FULLTEXT 索引状态失败，本次查询回退 LIKE", exc_info=True)
        return False
    if cache_key is not None:
        _FTS_CAPABILITY_CACHE[cache_key] = supported
    if not supported:
        logger.info("未检测到完整的 ngram FULLTEXT 索引，站内搜索自动使用安全 LIKE 回退")
    return supported


def _content_term_like(term: str):
    """单条词项的安全 LIKE 匹配：覆盖标题、正文及中文译文。"""
    ct_exists = exists(
        select(1).where(
            ContentTranslation.raw_content_id == RawContent.id,
            ContentTranslation.text.icontains(term, autoescape=True),
        )
    )
    return or_(
        ContentVersion.title.icontains(term, autoescape=True),
        ContentVersion.text.icontains(term, autoescape=True),
        ct_exists,
    )


def content_search_condition(q: str | None, *, use_mysql_fts: bool):
    """构造内容列表检索条件（假定外层已关联 RawContent 与最新版本 ContentVersion）。"""
    parsed = parse_search_query(q)
    if not parsed.terms:
        return None

    if not use_mysql_fts or not parsed.boolean_expr:
        return and_(*(_content_term_like(term) for term in parsed.terms))

    cv_ft = aliased(ContentVersion, name="cv_ft")
    rc_ft = aliased(RawContent, name="rc_ft")
    ct_ft = aliased(ContentTranslation, name="ct_ft")

    ft_expr = parsed.boolean_expr
    # 各表独立走 FULLTEXT 索引后 UNION 汇总主键，避免顶层 OR 导致 MySQL 退化为全表扫描。
    version_hits = (
        select(cv_ft.raw_content_id.label("rid"))
        .join(rc_ft, rc_ft.latest_version_id == cv_ft.id)
        .where(
            text("MATCH(cv_ft.title, cv_ft.text) AGAINST(:ft_q_cv IN BOOLEAN MODE)").bindparams(
                ft_q_cv=ft_expr
            )
        )
    )
    translation_hits = select(ct_ft.raw_content_id.label("rid")).where(
        text("MATCH(ct_ft.text) AGAINST(:ft_q_ct IN BOOLEAN MODE)").bindparams(ft_q_ct=ft_expr)
    )
    fts_ids = union(version_hits, translation_hits).subquery("ft_content_hits")
    cond = RawContent.id.in_(select(fts_ids.c.rid))
    if parsed.like_terms:
        return and_(cond, *(_content_term_like(term) for term in parsed.like_terms))
    return cond


def _event_term_like(term: str):
    """单条事件检索词的安全 LIKE 匹配：覆盖事件标题、摘要、有效证据正文及译文。"""
    ev_cv = aliased(ContentVersion)
    ev_rc = aliased(RawContent)
    ev_ct = aliased(ContentTranslation)

    evidence_text_hit = exists(
        select(1)
        .select_from(Evidence)
        .join(ev_rc, ev_rc.id == Evidence.raw_content_id)
        .join(ev_cv, ev_cv.id == ev_rc.latest_version_id)
        .where(
            Evidence.event_id == Event.id,
            Evidence.role != "excluded",
            ev_rc.hidden.is_(False),
            or_(
                ev_cv.title.icontains(term, autoescape=True),
                ev_cv.text.icontains(term, autoescape=True),
            ),
        )
    )
    evidence_translation_hit = exists(
        select(1)
        .select_from(Evidence)
        .join(ev_rc, ev_rc.id == Evidence.raw_content_id)
        .join(ev_ct, ev_ct.raw_content_id == ev_rc.id)
        .where(
            Evidence.event_id == Event.id,
            Evidence.role != "excluded",
            ev_rc.hidden.is_(False),
            ev_ct.text.icontains(term, autoescape=True),
        )
    )
    return or_(
        Event.title.icontains(term, autoescape=True),
        Event.summary.icontains(term, autoescape=True),
        evidence_text_hit,
        evidence_translation_hit,
    )


def event_search_condition(q: str | None, *, use_mysql_fts: bool):
    """构造事件列表检索条件（覆盖事件标题/摘要及关联证据正文/译文）。"""
    parsed = parse_search_query(q)
    if not parsed.terms:
        return None

    if not use_mysql_fts or not parsed.boolean_expr:
        return and_(*(_event_term_like(term) for term in parsed.terms))

    ev_ft = aliased(Event, name="e_ft")
    evi_ft = aliased(Evidence, name="evi_ft")
    rc_ft = aliased(RawContent, name="rc_ft")
    cv_ft = aliased(ContentVersion, name="cv_ft")
    evi_tr_ft = aliased(Evidence, name="evi_tr_ft")
    rc_tr_ft = aliased(RawContent, name="rc_tr_ft")
    ct_ft = aliased(ContentTranslation, name="ct_ft")

    ft_expr = parsed.boolean_expr
    event_hits = select(ev_ft.id.label("eid")).where(
        text("MATCH(e_ft.title, e_ft.summary) AGAINST(:ft_q_ev IN BOOLEAN MODE)").bindparams(
            ft_q_ev=ft_expr
        )
    )
    evidence_version_hits = (
        select(evi_ft.event_id.label("eid"))
        .join(rc_ft, rc_ft.id == evi_ft.raw_content_id)
        .join(cv_ft, cv_ft.id == rc_ft.latest_version_id)
        .where(
            evi_ft.role != "excluded",
            rc_ft.hidden.is_(False),
            text("MATCH(cv_ft.title, cv_ft.text) AGAINST(:ft_q_evi_cv IN BOOLEAN MODE)").bindparams(
                ft_q_evi_cv=ft_expr
            ),
        )
    )
    evidence_translation_hits = (
        select(evi_tr_ft.event_id.label("eid"))
        .join(rc_tr_ft, rc_tr_ft.id == evi_tr_ft.raw_content_id)
        .join(ct_ft, ct_ft.raw_content_id == rc_tr_ft.id)
        .where(
            evi_tr_ft.role != "excluded",
            rc_tr_ft.hidden.is_(False),
            text("MATCH(ct_ft.text) AGAINST(:ft_q_evi_ct IN BOOLEAN MODE)").bindparams(
                ft_q_evi_ct=ft_expr
            ),
        )
    )
    fts_ids = union(event_hits, evidence_version_hits, evidence_translation_hits).subquery(
        "ft_event_hits"
    )
    cond = Event.id.in_(select(fts_ids.c.eid))
    if parsed.like_terms:
        return and_(cond, *(_event_term_like(term) for term in parsed.like_terms))
    return cond
