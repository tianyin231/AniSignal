"""分区注册表（app/communities.py）单测：完整性、校验派生、提示词与防串扰词表。

新增分区时这些用例保证注册表、枚举、提示词、标题去噪四处始终一致。
"""

import re

from app.communities import (
    COMMUNITIES,
    DEFAULT_COMMUNITY,
    community_keys,
    community_pattern,
    crosstalk_names,
    game_name_boilerplate,
    get_community,
)
from app.models.enums import Community
from app.services import ai as ai_service
from app.services.curation import _review_system_prompt
from app.services.events import _TITLE_BOILERPLATE


def test_registry_matches_enum():
    assert community_keys() == tuple(c.value for c in Community)
    assert DEFAULT_COMMUNITY == Community.WUWA.value
    for meta in COMMUNITIES.values():
        assert meta.label and meta.bare_name and meta.color
        assert meta.game_name
        assert meta.game_note
        # 前 two 项必须是《X》与裸名；变体（extra_names）跟在后面
        assert meta.game_names[:2] == (f"《{meta.bare_name}》", meta.bare_name)


def test_community_pattern_derived_from_registry():
    pattern = community_pattern()
    assert re.fullmatch(pattern, DEFAULT_COMMUNITY)
    for key in community_keys():
        assert re.fullmatch(pattern, key)
    assert re.fullmatch(pattern, "other") is None


def test_review_prompt_templated_per_community():
    for key, meta in COMMUNITIES.items():
        prompt = _review_system_prompt(key)
        if meta.domain == "news":
            assert "newsworthy" in prompt and "不是视频字幕" in prompt
            assert "不做没有证据的真实性背书" in prompt
            continue
        assert f"《{meta.bare_name}》" in prompt
        # 其他分区的游戏名（任意变体）应出现在"其他游戏"示例中
        for other_key, other in COMMUNITIES.items():
            if other_key != key and other.domain == "game":
                assert any(name in prompt for name in other.game_names)


def test_crosstalk_wordlist_excludes_own_game():
    wuwa_words = crosstalk_names("wuwa")
    blhx_words = crosstalk_names("blhx")
    assert "碧蓝航线" in wuwa_words
    assert "鸣潮" not in wuwa_words
    assert "鸣潮" in blhx_words
    assert "碧蓝航线" not in blhx_words


def test_summary_supported_uses_registry():
    # blhx 分区：碧蓝航线是本分区游戏名，摘要允许出现
    assert ai_service._summary_supported("碧蓝航线新活动开启", "碧蓝航线新活动", "blhx")
    # wuwa 分区：证据未提碧蓝航线 → 拒绝；证据提了 → 放行（联动内容）
    assert not ai_service._summary_supported("碧蓝航线新活动开启", "无关证据文本", "wuwa")
    assert ai_service._summary_supported("碧蓝航线新活动开启", "证据提到碧蓝航线联动", "wuwa")


def test_title_boilerplate_covers_all_communities():
    for name in game_name_boilerplate():
        assert name in _TITLE_BOILERPLATE


def test_get_community_fallback():
    assert get_community(None).key == DEFAULT_COMMUNITY
    assert get_community("unknown").key == DEFAULT_COMMUNITY
    assert get_community("blhx").key == "blhx"
