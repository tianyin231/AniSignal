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
    ),
}

DEFAULT_COMMUNITY = "wuwa"

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
    names: list[str] = list(_CROSSTALK_EXTRAS)
    for meta in COMMUNITIES.values():
        if meta.key != community_key:
            names.extend(meta.game_names)
    return tuple(names)


def game_name_boilerplate() -> tuple[str, ...]:
    """全部分区游戏名的书写形式（含变体），供标题去噪等"清洗游戏名"场景使用。"""
    names: list[str] = []
    for meta in COMMUNITIES.values():
        names.extend(meta.game_names)
    return tuple(names)
