"""游戏分区注册表：分区标识、展示名、AI 提示词语料、官方账号配置的唯一权威。

新增分区的完整步骤：
1. 在 ``COMMUNITIES`` 加一条 ``CommunityMeta``，并在 ``models/enums.py`` 的 ``Community``
   加同名成员（测试会断言两边一致，防止漂移）。
2. 官方 B 站 UID：config.py 加 ``<game>_bili_mid`` 配置项，注册表里把 ``bili_mid_setting``
   指向它（不加配置也能跑，走 ``official_bili_mid_default`` 兜底）。
3. 需要历史内容归入新分区时，仿照 0024/0025 写迁移回填并播种采集目标。

校验正则、审核/摘要提示词、标题去噪、防串扰词表、B 站官方身份判定均从本表派生，
不要在业务代码里写 if community == "xxx" 分支。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommunityMeta:
    key: str
    label: str  # 展示名：前端分区 tabs、徽章、site-config
    game_name: str  # 提示词用全称（含出品方，如"库洛游戏《鸣潮》"）
    bare_name: str  # 裸游戏名：标题去噪、防串扰
    game_note: str  # 摘要提示词里防写成其他游戏的备注句
    other_games_examples: str  # 审核提示词"其他游戏"示例（含括号）
    color: str  # 分区语义色键：前端据此映射 Tailwind 类（blue/cyan/amber/...）
    bili_mid_setting: str | None  # config 中官方 B 站 UID 属性名
    official_bili_mid_default: str | None  # 配置缺失时的兜底 UID
    extra_names: tuple[str, ...] = ()  # 游戏名的常见变体（如"崩坏：星穹铁道"带冒号/繁体）
    domain: str = "game"  # news 分区采用资讯筛选，不沿用游戏词典与审核规则
    source_aliases: tuple[str, ...] = ()  # 仅用于跨区采集分流，英文缩写需按词边界匹配
    source_exclusions: tuple[str, ...] = ()  # 含同系列名称的独立游戏，不能借简称入本分区
    # 已核验认证的周边专号；共享品牌账号只采集明确属于本游戏的内容。
    official_merch_bili_sources: tuple[tuple[str, str], ...] = ()

    @property
    def game_names(self) -> tuple[str, ...]:
        """《X》与 X 两种书写形式 + 额外变体（标题去噪 / 防串扰都要匹配）。"""
        return (f"《{self.bare_name}》", self.bare_name, *self.extra_names)


COMMUNITIES: dict[str, CommunityMeta] = {
    "wuwa": CommunityMeta(
        key="wuwa",
        label="鸣潮",
        game_name="库洛游戏《鸣潮》",
        bare_name="鸣潮",
        game_note="不要把《鸣潮》写成其它游戏；",
        other_games_examples="（如崩坏系列、原神、碧蓝航线、星穹铁道、明日方舟等）",
        color="blue",
        bili_mid_setting="wuthering_waves_bili_mid",
        official_bili_mid_default="1955897084",
        source_aliases=("Wuthering Waves", "WuWa", "鳴潮"),
        official_merch_bili_sources=(("3493082584582776", "库洛游戏周边"),),
    ),
    "blhx": CommunityMeta(
        key="blhx",
        label="碧蓝航线",
        game_name="《碧蓝航线》",
        bare_name="碧蓝航线",
        game_note="不要把《碧蓝航线》写成其它游戏；",
        other_games_examples="（如鸣潮、崩坏系列、原神、星穹铁道、明日方舟等）",
        color="cyan",
        bili_mid_setting="azur_lane_bili_mid",
        official_bili_mid_default="233114659",
        source_aliases=("Azur Lane", "碧藍航線"),
    ),
    "hsr": CommunityMeta(
        key="hsr",
        label="崩坏星穹铁道",
        game_name="《崩坏：星穹铁道》",
        bare_name="崩坏星穹铁道",
        game_note="不要把《崩坏：星穹铁道》写成其它游戏；",
        other_games_examples="（如鸣潮、碧蓝航线、明日方舟、原神等）",
        color="violet",
        bili_mid_setting="star_rail_bili_mid",
        official_bili_mid_default="1340190821",
        extra_names=("崩坏：星穹铁道", "崩壞星穹鐵道", "崩壞：星穹鐵道", "星穹铁道", "星穹鐵道"),
        source_aliases=("Honkai Star Rail", "Honkai: Star Rail", "Star Rail", "HSR"),
        official_merch_bili_sources=(("508103429", "帕姆的收藏夹"),),
    ),
    "arknights": CommunityMeta(
        key="arknights",
        label="明日方舟",
        game_name="《明日方舟》",
        bare_name="明日方舟",
        game_note="不要把《明日方舟》写成其它游戏；",
        other_games_examples="（如鸣潮、碧蓝航线、星穹铁道、原神等）",
        color="orange",
        bili_mid_setting="arknights_bili_mid",
        official_bili_mid_default="161775300",
        source_aliases=("Arknights", "明日方舟"),
        source_exclusions=("Endfield", "终末地", "終末地"),
        official_merch_bili_sources=(("3546864272017883", "朝陇山播报站"),),
    ),
    "genshin": CommunityMeta(
        key="genshin", label="原神", game_name="米哈游《原神》", bare_name="原神",
        game_note="不要把《原神》写成星穹铁道或其他游戏；",
        other_games_examples="（如鸣潮、星穹铁道、碧蓝航线、明日方舟、终末地等）",
        color="cyan", bili_mid_setting="genshin_bili_mid", official_bili_mid_default="401742377",
        source_aliases=("Genshin Impact", "Genshin", "原神"),
    ),
    "endfield": CommunityMeta(
        key="endfield", label="终末地", game_name="鹰角网络《明日方舟：终末地》", bare_name="终末地",
        game_note="《明日方舟：终末地》与《明日方舟》本体独立，不混入本体更新；",
        other_games_examples="（如明日方舟本体、原神、鸣潮、星穹铁道等）",
        color="lime", bili_mid_setting="endfield_bili_mid", official_bili_mid_default="1265652806",
        extra_names=("明日方舟：终末地", "明日方舟:终末地", "明日方舟终末地", "終末地"),
        source_aliases=("Arknights Endfield", "Arknights: Endfield", "Endfield", "终末地", "終末地"),
    ),
    "zzz": CommunityMeta(
        key="zzz", label="绝区零", game_name="米哈游《绝区零》", bare_name="绝区零",
        game_note="不要把《绝区零》写成原神、崩坏系列或其他游戏；",
        other_games_examples="（如原神、星穹铁道、因缘精灵、鸣潮等）",
        color="orange", bili_mid_setting="zenless_bili_mid", official_bili_mid_default="1636034895",
        extra_names=("絕區零",), source_aliases=("Zenless Zone Zero", "ZZZ"),
    ),
    "petitplanet": CommunityMeta(
        key="petitplanet", label="星布谷地", game_name="米哈游《星布谷地》", bare_name="星布谷地",
        game_note="《星布谷地》是星球生活模拟游戏，不要混入星露谷物语或其他游戏；",
        other_games_examples="（如星露谷物语、动物森友会、原神、因缘精灵等）",
        color="teal", bili_mid_setting="petit_planet_bili_mid", official_bili_mid_default="3546622923377024",
        source_aliases=("Petit Planet",),
    ),
    "hna": CommunityMeta(
        key="hna", label="崩坏：因缘精灵", game_name="米哈游《崩坏：因缘精灵》", bare_name="崩坏：因缘精灵",
        game_note="《崩坏：因缘精灵》与崩坏3、星穹铁道独立，不混入其他崩坏系列游戏更新；",
        other_games_examples="（如崩坏3、星穹铁道、星布谷地、原神等）",
        color="violet", bili_mid_setting="nexus_anima_bili_mid", official_bili_mid_default="3546886017387331",
        extra_names=("崩坏因缘精灵", "崩坏:因缘精灵", "因缘精灵", "崩壞：因緣精靈", "因緣精靈"),
        source_aliases=("Honkai Nexus Anima", "Honkai: Nexus Anima", "Nexus Anima", "HNA"),
    ),
    "roco": CommunityMeta(
        key="roco", label="洛克王国：世界", game_name="腾讯魔方工作室《洛克王国：世界》", bare_name="洛克王国：世界",
        game_note="只关注《洛克王国：世界》，不要混入《洛克王国》页游的活动与攻略；",
        other_games_examples="（如洛克王国页游、因缘精灵、原神、鸣潮等）",
        color="sky", bili_mid_setting="roco_bili_mid", official_bili_mid_default="626796832",
        extra_names=("洛克王国世界", "洛克王国:世界", "洛克王国 世界", "洛克王國：世界"),
        source_aliases=("Roco Kingdom",),
    ),
    "promilia": CommunityMeta(
        key="promilia", label="蓝色星原：旅谣", game_name="蛮啾网络《蓝色星原：旅谣》", bare_name="蓝色星原：旅谣",
        game_note="《蓝色星原：旅谣》与《碧蓝航线》独立，不混入碧蓝航线的更新；",
        other_games_examples="（如碧蓝航线、鸣潮、原神、异环等）",
        color="cyan", bili_mid_setting="azur_promilia_bili_mid", official_bili_mid_default="3546569016085336",
        extra_names=("蓝色星原旅谣", "蓝色星原:旅谣", "蓝色星原", "藍色星原：旅謠", "藍色星原"),
        source_aliases=("Azur Promilia",),
    ),
    "nte": CommunityMeta(
        key="nte", label="异环", game_name="Hotta Studio《异环》", bare_name="异环",
        game_note="《异环》是超自然都市开放世界游戏，不要混入幻塔或无限大等游戏；",
        other_games_examples="（如幻塔、无限大、绝区零、鸣潮等）",
        color="yellow", bili_mid_setting="neverness_bili_mid", official_bili_mid_default="3546636978489848",
        extra_names=("異環",), source_aliases=("Neverness to Everness", "NTE"),
    ),
    "ai": CommunityMeta(
        key="ai", label="AI", game_name="人工智能", bare_name="AI",
        game_note="区分已发布、测试中与传闻；不把转述当作事实确认；",
        other_games_examples="", color="teal", bili_mid_setting=None,
        official_bili_mid_default=None, domain="news",
    ),
}

DEFAULT_COMMUNITY = "wuwa"


def official_merch_bilibili_sources() -> dict[str, tuple[str, str]]:
    """UID →（分区、账号名）；2026-10-28 已逐个核对 B站企业认证。"""
    return {mid: (meta.key, name) for meta in COMMUNITIES.values()
            for mid, name in meta.official_merch_bili_sources}

# 摘要防串扰的固定扩展名单：与现有分区无关、出现即视为串台的同类游戏
_CROSSTALK_EXTRAS = (
    "战双帕弥什",
    "战双",
    "原神",
    "崩坏：星穹铁道",
    "崩坏星穹铁道",
    "绝区零",
)


def community_keys() -> tuple[str, ...]:
    return tuple(COMMUNITIES)


def community_pattern() -> str:
    """FastAPI/Pydantic 校验正则，从注册表生成（非法值 → 422）。"""
    return "^(%s)$" % "|".join(COMMUNITIES)


# 聚合查询专用：community=all 表示跨分区（仅限公开读端点，写路径不接受）
COMMUNITY_ALL = "all"


def community_query_pattern() -> str:
    """公开列表端点的分区查询正则：各分区 + all（跨分区聚合）。"""
    return "^(%s|%s)$" % ("|".join(COMMUNITIES), COMMUNITY_ALL)


def is_all_community(value: str | None) -> bool:
    return (value or "") == COMMUNITY_ALL


def get_community(key: str | None) -> CommunityMeta:
    """按 key 取分区元数据；未知/缺省回落默认分区。"""
    return COMMUNITIES.get(key or "", COMMUNITIES[DEFAULT_COMMUNITY])


def crosstalk_names(community_key: str) -> tuple[str, ...]:
    """摘要防串扰词表：其他分区的游戏名 + 固定扩展名单。

    本分区游戏名不进名单——联动内容摘要里合法出现时不应被回退。
    """
    own_names = set(get_community(community_key).game_names)
    names: list[str] = [name for name in _CROSSTALK_EXTRAS if name not in own_names]
    for meta in COMMUNITIES.values():
        if meta.key != community_key and meta.domain == "game":
            names.extend(meta.game_names)
    return tuple(names)


def game_name_boilerplate() -> tuple[str, ...]:
    """全部分区游戏名的书写形式（含变体），供标题去噪等"清洗游戏名"场景使用。"""
    names: list[str] = []
    for meta in COMMUNITIES.values():
        if meta.domain == "game":
            names.extend(meta.game_names)
    return tuple(names)
