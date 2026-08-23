"""规则分类：按管理端词典给内容打类目标签（v1 纯关键词，AI 候选留给后续阶段）。

匹配顺序：词典 priority 升序 → id 升序；命中即返回。
官方身份 + 公告类平台且未命中词典 → official_announce 兜底。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClassificationRule, Identity
from app.models.enums import Category
from app.schemas.content import NormalizedContent

logger = logging.getLogger(__name__)

# 首次建库播种的履约词典（管理端可改可删）
DEFAULT_RULES: list[tuple[str, str, int]] = [
    # (category, pattern, priority) —— 越具体越靠前
    ("presale", "预售", 10),
    ("presale", "开启预约", 11),
    ("balance", "尾款", 20),
    ("delay", "延期", 30),
    ("delay", "推迟", 31),
    ("delay", "跳票", 32),
    ("unboxing", "开箱", 40),
    ("unboxing", "晒单", 41),
    ("quality", "质量", 50),
    ("quality", "售后", 51),
    ("quality", "故障", 52),
    ("quality", "瑕疵", 53),
    ("review", "评测", 60),
    ("review", "测评", 61),
    ("arrived", "到货", 70),
    ("arrived", "已收到", 71),
    ("shipping", "发货", 80),
    ("shipping", "物流", 81),
    ("version_update", "版本", 90),
    ("version_update", "维护公告", 91),
    ("version_update", "更新", 92),
]

# 官方身份 + 这些平台的内容未命中词典时按官方公告归类
_OFFICIAL_FALLBACK_PLATFORMS = {"kuro_cn", "kuro_global"}


async def seed_defaults(session: AsyncSession) -> int:
    """词典为空时播种默认规则；返回播种条数。"""
    count = len((await session.execute(select(ClassificationRule.id))).all())
    if count:
        return 0
    session.add_all(
        ClassificationRule(category=category, pattern=pattern, priority=priority)
        for category, pattern, priority in DEFAULT_RULES
    )
    await session.commit()
    return len(DEFAULT_RULES)


async def load_rules(session: AsyncSession) -> list[ClassificationRule]:
    result = await session.execute(
        select(ClassificationRule)
        .where(ClassificationRule.enabled.is_(True))
        .order_by(ClassificationRule.priority.asc(), ClassificationRule.id.asc())
    )
    return list(result.scalars())


def classify_content(
    normalized: NormalizedContent, rules: list[ClassificationRule]
) -> str | None:
    haystack = " ".join(
        part for part in (normalized.title or "", normalized.text or "") if part
    )
    if haystack:
        for rule in rules:
            if rule.platform and rule.platform != normalized.platform:
                continue
            if rule.pattern in haystack:
                return rule.category
    identity = normalized.author.identity if normalized.author else Identity.UNKNOWN
    if (
        identity == Identity.OFFICIAL
        and str(normalized.platform) in _OFFICIAL_FALLBACK_PLATFORMS
    ):
        return Category.OFFICIAL_ANNOUNCE
    # 官方动态（B 站官方账号）未命中词典 → version_update 更合适？保持 None，
    # 让公开流按未分类展示，聚合引擎只处理已分类官方公告
    if identity == Identity.OFFICIAL and normalized.content_type == "notice":
        return Category.OFFICIAL_ANNOUNCE
    return None
