"""分区注册表：支持内置分区与后台动态自定义分区（SiteSetting('communities') 持久化）。

校验、审核/摘要提示词、标题去噪、防串扰词表、B 站官方身份判定均从本表实时派生，
新增或启停分区无需修改代码或重新构建镜像。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
import time
from typing import Annotated, Any

from fastapi import Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import AfterValidator

from app.db import get_session

COMMUNITY_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{0,15}$")
VALID_COLORS = ("yellow", "sky", "violet", "orange", "cyan", "lime", "teal", "blue")
_SYNC_TTL_SECONDS = 5.0
_last_sync_at: float = 0.0


@dataclass(frozen=True)
class CommunityMeta:
    key: str
    label: str  # 展示名：前端分区 tabs、徽章、site-config
    game_name: str  # 提示词用全称（含出品方，如"库洛游戏《鸣潮》"）
    bare_name: str  # 裸名：标题去噪、防串扰
    game_note: str  # 摘要提示词里防写成其他主题的备注句
    other_games_examples: str  # 审核提示词"其他"示例（含括号）
    color: str  # 分区语义色键：前端据此映射 Tailwind 类
    bili_mid_setting: str | None  # config 中官方 B 站 UID 属性名
    official_bili_mid_default: str | None  # 配置缺失或自定义分区的官方 UID
    extra_names: tuple[str, ...] = ()
    domain: str = "game"  # game | news（news 分区采用资讯筛选）
    source_aliases: tuple[str, ...] = ()
    source_exclusions: tuple[str, ...] = ()
    official_merch_bili_sources: tuple[tuple[str, str], ...] = ()
    official_notice_region: str = "cn"
    en: str = ""
    platforms: tuple[str, ...] = ("bilibili", "youtube", "rss")
    active: bool = True
    builtin: bool = True

    @property
    def game_names(self) -> tuple[str, ...]:
        """《X》与 X 两种书写形式 + 额外变体（标题去噪 / 防串扰都要匹配）。"""
        return (f"《{self.bare_name}》", self.bare_name, *self.extra_names)


BUILTIN_COMMUNITIES: dict[str, CommunityMeta] = {
    "wuwa": CommunityMeta(
        key="wuwa", label="鸣潮", en="Wuthering Waves",
        game_name="库洛游戏《鸣潮》", bare_name="鸣潮",
        game_note="不要把《鸣潮》写成其它游戏；",
        other_games_examples="（如崩坏系列、原神、碧蓝航线、星穹铁道、明日方舟等）",
        color="yellow", bili_mid_setting="wuthering_waves_bili_mid",
        official_bili_mid_default="1955897084",
        source_aliases=("Wuthering Waves", "WuWa", "鳴潮"),
        official_merch_bili_sources=(("3493082584582776", "库洛游戏周边"),),
        platforms=("bilibili", "youtube", "kuro"),
    ),
    "blhx": CommunityMeta(
        key="blhx", label="碧蓝航线", en="Azur Lane",
        game_name="《碧蓝航线》", bare_name="碧蓝航线",
        game_note="不要把《碧蓝航线》写成其它游戏；",
        other_games_examples="（如鸣潮、崩坏系列、原神、星穹铁道、明日方舟等）",
        color="sky", bili_mid_setting="azur_lane_bili_mid",
        official_bili_mid_default="233114659",
        source_aliases=("Azur Lane", "碧藍航線"),
        platforms=("bilibili", "youtube"),
    ),
    "hsr": CommunityMeta(
        key="hsr", label="崩坏星穹铁道", en="Honkai: Star Rail",
        game_name="《崩坏：星穹铁道》", bare_name="崩坏星穹铁道",
        game_note="不要把《崩坏：星穹铁道》写成其它游戏；",
        other_games_examples="（如鸣潮、碧蓝航线、明日方舟、原神等）",
        color="violet", bili_mid_setting="star_rail_bili_mid",
        official_bili_mid_default="1340190821",
        extra_names=("崩坏：星穹铁道", "崩壞星穹鐵道", "崩壞：星穹鐵道", "星穹铁道", "星穹鐵道"),
        source_aliases=("Honkai Star Rail", "Honkai: Star Rail", "Star Rail", "HSR"),
        official_merch_bili_sources=(("508103429", "帕姆的收藏夹"),),
        platforms=("bilibili", "youtube"),
    ),
    "arknights": CommunityMeta(
        key="arknights", label="明日方舟", en="Arknights",
        game_name="《明日方舟》", bare_name="明日方舟",
        game_note="不要把《明日方舟》写成其它游戏；",
        other_games_examples="（如鸣潮、碧蓝航线、星穹铁道、原神等）",
        color="orange", bili_mid_setting="arknights_bili_mid",
        official_bili_mid_default="161775300",
        source_aliases=("Arknights", "明日方舟"),
        source_exclusions=("Endfield", "终末地", "終末地"),
        official_merch_bili_sources=(("3546864272017883", "朝陇山播报站"),),
        platforms=("bilibili", "youtube"),
    ),
    "genshin": CommunityMeta(
        key="genshin", label="原神", en="Genshin Impact",
        game_name="米哈游《原神》", bare_name="原神",
        game_note="不要把《原神》写成星穹铁道或其他游戏；",
        other_games_examples="（如鸣潮、星穹铁道、碧蓝航线、明日方舟、终末地等）",
        color="cyan", bili_mid_setting="genshin_bili_mid", official_bili_mid_default="401742377",
        source_aliases=("Genshin Impact", "Genshin", "原神"),
        platforms=("bilibili", "youtube", "game_official", "x", "miyoushe"),
    ),
    "endfield": CommunityMeta(
        key="endfield", label="终末地", en="Arknights: Endfield",
        game_name="鹰角网络《明日方舟：终末地》", bare_name="终末地",
        game_note="《明日方舟：终末地》与《明日方舟》本体独立，不混入本体更新；",
        other_games_examples="（如明日方舟本体、原神、鸣潮、星穹铁道等）",
        color="lime", bili_mid_setting="endfield_bili_mid", official_bili_mid_default="1265652806",
        extra_names=("明日方舟：终末地", "明日方舟:终末地", "明日方舟终末地", "終末地"),
        source_aliases=("Arknights Endfield", "Arknights: Endfield", "Endfield", "终末地", "終末地"),
        platforms=("bilibili", "youtube", "game_official", "x"),
    ),
    "zzz": CommunityMeta(
        key="zzz", label="绝区零", en="Zenless Zone Zero",
        game_name="米哈游《绝区零》", bare_name="绝区零",
        game_note="不要把《绝区零》写成原神、崩坏系列或其他游戏；",
        other_games_examples="（如原神、星穹铁道、因缘精灵、鸣潮等）",
        color="orange", bili_mid_setting="zenless_bili_mid", official_bili_mid_default="1636034895",
        extra_names=("絕區零",), source_aliases=("Zenless Zone Zero", "ZZZ"),
        platforms=("bilibili", "miyoushe"),
    ),
    "petitplanet": CommunityMeta(
        key="petitplanet", label="星布谷地", en="Petit Planet",
        game_name="米哈游《星布谷地》", bare_name="星布谷地",
        game_note="《星布谷地》是星球生活模拟游戏，不要混入星露谷物语或其他游戏；",
        other_games_examples="（如星露谷物语、动物森友会、原神、因缘精灵等）",
        color="teal", bili_mid_setting="petit_planet_bili_mid", official_bili_mid_default="3546622923377024",
        source_aliases=("Petit Planet",), platforms=("bilibili",),
    ),
    "hna": CommunityMeta(
        key="hna", label="崩坏：因缘精灵", en="Honkai: Nexus Anima",
        game_name="米哈游《崩坏：因缘精灵》", bare_name="崩坏：因缘精灵",
        game_note="《崩坏：因缘精灵》与崩坏3、星穹铁道独立，不混入其他崩坏系列游戏更新；",
        other_games_examples="（如崩坏3、星穹铁道、星布谷地、原神等）",
        color="violet", bili_mid_setting="nexus_anima_bili_mid", official_bili_mid_default="3546886017387331",
        extra_names=("崩坏因缘精灵", "崩坏:因缘精灵", "因缘精灵", "崩壞：因緣精靈", "因緣精靈"),
        source_aliases=("Honkai Nexus Anima", "Honkai: Nexus Anima", "Nexus Anima", "HNA"),
        platforms=("bilibili",),
    ),
    "roco": CommunityMeta(
        key="roco", label="洛克王国：世界", en="Roco Kingdom",
        game_name="腾讯魔方工作室《洛克王国：世界》", bare_name="洛克王国：世界",
        game_note="只关注《洛克王国：世界》，不要混入《洛克王国》页游的活动与攻略；",
        other_games_examples="（如洛克王国页游、因缘精灵、原神、鸣潮等）",
        color="sky", bili_mid_setting="roco_bili_mid", official_bili_mid_default="626796832",
        extra_names=("洛克王国世界", "洛克王国:世界", "洛克王国 世界", "洛克王國：世界"),
        source_aliases=("Roco Kingdom",), platforms=("bilibili",),
    ),
    "promilia": CommunityMeta(
        key="promilia", label="蓝色星原：旅谣", en="Azur Promilia",
        game_name="蛮啾网络《蓝色星原：旅谣》", bare_name="蓝色星原：旅谣",
        game_note="《蓝色星原：旅谣》与《碧蓝航线》独立，不混入碧蓝航线的更新；",
        other_games_examples="（如碧蓝航线、鸣潮、原神、异环等）",
        color="cyan", bili_mid_setting="azur_promilia_bili_mid", official_bili_mid_default="3546569016085336",
        extra_names=("蓝色星原旅谣", "蓝色星原:旅谣", "蓝色星原", "藍色星原：旅謠", "藍色星原"),
        source_aliases=("Azur Promilia",), platforms=("bilibili",),
    ),
    "nte": CommunityMeta(
        key="nte", label="异环", en="Neverness to Everness",
        game_name="Hotta Studio《异环》", bare_name="异环",
        game_note="《异环》是超自然都市开放世界游戏，不要混入幻塔或无限大等游戏；",
        other_games_examples="（如幻塔、无限大、绝区零、鸣潮等）",
        color="yellow", bili_mid_setting="neverness_bili_mid", official_bili_mid_default="3546636978489848",
        extra_names=("異環",), source_aliases=("Neverness to Everness", "NTE"),
        platforms=("bilibili",),
    ),
    "nikke": CommunityMeta(
        key="nikke", label="NIKKE", en="Goddess of Victory: NIKKE",
        game_name="SHIFT UP《胜利女神：NIKKE》国际服", bare_name="胜利女神：NIKKE",
        game_note="沿用 NIKKE 国际服来源；不要混入国服《胜利女神：新的希望》的版本、活动和维护时间；",
        other_games_examples="（如胜利女神：新的希望、碧蓝航线、无限暖暖、鸣潮等）",
        color="orange", bili_mid_setting=None, official_bili_mid_default=None,
        extra_names=("胜利女神NIKKE", "勝利女神：NIKKE", "妮姬"),
        source_aliases=("NIKKE", "Goddess of Victory: NIKKE"), official_notice_region="global",
        platforms=("game_official", "youtube", "x"),
    ),
    "infinitynikki": CommunityMeta(
        key="infinitynikki", label="无限暖暖", en="Infinity Nikki",
        game_name="叠纸游戏《无限暖暖》", bare_name="无限暖暖",
        game_note="《无限暖暖》是开放世界换装冒险游戏，不混入奇迹暖暖、闪耀暖暖等系列作品的更新；",
        other_games_examples="（如奇迹暖暖、闪耀暖暖、星布谷地、原神等）",
        color="violet", bili_mid_setting="infinity_nikki_bili_mid", official_bili_mid_default="3461576715667734",
        extra_names=("無限暖暖",), source_aliases=("Infinity Nikki",),
        platforms=("bilibili",),
    ),
    "ai": CommunityMeta(
        key="ai", label="AI", en="AI News",
        game_name="人工智能", bare_name="AI",
        game_note="区分已发布、测试中与传闻；不把转述当作事实确认；",
        other_games_examples="", color="teal", bili_mid_setting=None,
        official_bili_mid_default=None, domain="news",
        platforms=("rss", "bilibili", "x"),
    ),
}

COMMUNITIES: dict[str, CommunityMeta] = dict(BUILTIN_COMMUNITIES)
DEFAULT_COMMUNITY = "wuwa"
COMMUNITY_ALL = "all"

_CROSSTALK_EXTRAS = (
    "战双帕弥什",
    "战双",
    "原神",
    "崩坏：星穹铁道",
    "崩坏星穹铁道",
    "绝区零",
)


def _refresh_dependents() -> None:
    import sys

    enums_mod = sys.modules.get("app.models.enums")
    if enums_mod is not None and hasattr(enums_mod, "COMMUNITY_LABELS"):
        enums_mod.COMMUNITY_LABELS.clear()
        enums_mod.COMMUNITY_LABELS.update({k: m.label for k, m in COMMUNITIES.items()})


def build_custom_meta(raw: dict) -> CommunityMeta:
    key = str(raw.get("key") or "").strip().lower()
    label = str(raw.get("label") or key).strip()[:32]
    en = str(raw.get("en") or label).strip()[:64]
    # 自定义分区统一走通用内容流（/?community=<key>），确保无 AI 时 RSS/视频/动态也能直接展示。
    domain = "game"
    color = str(raw.get("color") or "blue")
    if color not in VALID_COLORS:
        color = "blue"
    bili_mid = str(raw.get("official_bili_mid") or "").strip() or None
    aliases = tuple(dict.fromkeys(str(x).strip() for x in (raw.get("aliases") or []) if str(x).strip()))
    platforms = tuple(raw.get("platforms") or ("bilibili", "youtube", "rss", "x"))
    note = f"不要把《{label}》写成其它游戏或无关作品；"
    return CommunityMeta(
        key=key,
        label=label,
        en=en,
        game_name=label if domain == "news" else f"《{label}》",
        bare_name=label,
        game_note=note,
        other_games_examples="",
        color=color,
        bili_mid_setting=None,
        official_bili_mid_default=bili_mid,
        extra_names=aliases,
        source_aliases=aliases,
        domain=domain,
        platforms=platforms,
        active=bool(raw.get("active", True)),
        builtin=False,
    )


def apply_community_overrides(payload: dict | None) -> None:
    """将数据库中的分区配置原地同步到 COMMUNITIES（所有引用方即时可见）。"""
    disabled = set((payload or {}).get("disabled_builtins") or [])
    custom_list = (payload or {}).get("custom") or []

    next_map: dict[str, CommunityMeta] = {
        key: replace(meta, active=(key not in disabled))
        for key, meta in BUILTIN_COMMUNITIES.items()
    }
    for item in custom_list:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        if not COMMUNITY_SLUG_RE.fullmatch(key) or key in BUILTIN_COMMUNITIES or key == COMMUNITY_ALL:
            continue
        next_map[key] = build_custom_meta(item)

    COMMUNITIES.clear()
    COMMUNITIES.update(next_map)
    _refresh_dependents()


def reset_communities() -> None:
    global _last_sync_at
    COMMUNITIES.clear()
    COMMUNITIES.update(BUILTIN_COMMUNITIES)
    _last_sync_at = 0.0
    _refresh_dependents()


async def sync_communities(session) -> dict:
    global _last_sync_at
    from app.services.settings import get_setting

    raw = await get_setting(session, "communities")
    apply_community_overrides(raw if isinstance(raw, dict) else None)
    _last_sync_at = time.monotonic()
    return raw if isinstance(raw, dict) else {"disabled_builtins": [], "custom": []}


async def ensure_communities_synced(session, *, force: bool = False) -> None:
    if not force and _last_sync_at and (time.monotonic() - _last_sync_at) < _SYNC_TTL_SECONDS:
        return
    await sync_communities(session)


async def _dep_sync_communities(request: Request, session: Any = Depends(get_session)) -> None:
    await ensure_communities_synced(
        session, force=request.method not in {"GET", "HEAD", "OPTIONS"}
    )


def community_to_public_dict(meta: CommunityMeta) -> dict:
    return {
        "key": meta.key,
        "label": meta.label,
        "en": meta.en or meta.label,
        "color": meta.color,
        "domain": meta.domain,
        "platforms": list(meta.platforms),
        "active": meta.active,
        "builtin": meta.builtin,
    }


def validate_community_key(value: str) -> str:
    if value not in COMMUNITIES:
        raise ValueError(f"未知分区：{value}")
    return value


def validate_optional_community_key(value: str | None) -> str | None:
    if value is not None and value not in COMMUNITIES:
        raise ValueError(f"未知分区：{value}")
    return value


def validate_community_or_all(value: str) -> str:
    if value != COMMUNITY_ALL and value not in COMMUNITIES:
        raise ValueError(f"未知分区：{value}")
    return value


CommunitySlug = Annotated[str, AfterValidator(validate_community_key)]
OptionalCommunitySlug = Annotated[str | None, AfterValidator(validate_optional_community_key)]
CommunityOrAllSlug = Annotated[str, AfterValidator(validate_community_or_all)]


def _bad_community_query(value: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("query", "community"), "msg": f"未知分区：{value}", "input": value}]
    )


async def _dep_community(
    community: str = Query(default=DEFAULT_COMMUNITY, description="分区标识"),
    session: Any = Depends(get_session),
) -> str:
    await ensure_communities_synced(session, force=(community not in COMMUNITIES))
    if community not in COMMUNITIES:
        raise _bad_community_query(community)
    return community


async def _dep_community_required(
    community: str = Query(..., description="分区标识"),
    session: Any = Depends(get_session),
) -> str:
    await ensure_communities_synced(session, force=(community not in COMMUNITIES))
    if community not in COMMUNITIES:
        raise _bad_community_query(community)
    return community


async def _dep_community_optional(
    community: str | None = Query(default=None, description="分区标识"),
    session: Any = Depends(get_session),
) -> str | None:
    if community is not None:
        await ensure_communities_synced(session, force=(community not in COMMUNITIES))
        if community not in COMMUNITIES:
            raise _bad_community_query(community)
    return community


async def _dep_community_or_all(
    community: str = Query(default=DEFAULT_COMMUNITY, description="分区标识（all=跨分区聚合）"),
    session: Any = Depends(get_session),
) -> str:
    if community != COMMUNITY_ALL:
        await ensure_communities_synced(session, force=(community not in COMMUNITIES))
        if community not in COMMUNITIES:
            raise _bad_community_query(community)
    return community


COMMUNITY_QUERY_DEP = Depends(_dep_community)
COMMUNITY_REQUIRED_QUERY_DEP = Depends(_dep_community_required)
COMMUNITY_OPTIONAL_QUERY_DEP = Depends(_dep_community_optional)
COMMUNITY_AGGREGATE_QUERY_DEP = Depends(_dep_community_or_all)
COMMUNITY_SYNC_DEP = Depends(_dep_sync_communities)


def official_merch_bilibili_sources() -> dict[str, tuple[str, str]]:
    """UID →（分区、账号名）。"""
    return {
        mid: (meta.key, name)
        for meta in COMMUNITIES.values()
        for mid, name in meta.official_merch_bili_sources
    }


def community_keys() -> tuple[str, ...]:
    return tuple(COMMUNITIES)


def community_pattern() -> str:
    """正则表示（兼容旧测试/工具调用）。"""
    return "^(%s)$" % "|".join(COMMUNITIES)


def community_query_pattern() -> str:
    return "^(%s|%s)$" % ("|".join(COMMUNITIES), COMMUNITY_ALL)


def is_all_community(value: str | None) -> bool:
    return (value or "") == COMMUNITY_ALL


def community_conditions(column, community: str | None, hidden=()) -> list:
    if not is_all_community(community):
        return [column == community]
    return [column.notin_(tuple(hidden))] if hidden else []


def get_community(key: str | None) -> CommunityMeta:
    return COMMUNITIES.get(key or "", COMMUNITIES[DEFAULT_COMMUNITY])


def crosstalk_names(community_key: str) -> tuple[str, ...]:
    own_names = set(get_community(community_key).game_names)
    names: list[str] = [name for name in _CROSSTALK_EXTRAS if name not in own_names]
    for meta in COMMUNITIES.values():
        if meta.key != community_key and meta.domain == "game":
            names.extend(meta.game_names)
    return tuple(names)


def game_name_boilerplate() -> tuple[str, ...]:
    names: list[str] = []
    for meta in COMMUNITIES.values():
        names.extend(meta.game_names)
    return tuple(names)
