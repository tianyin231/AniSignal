"""`parse.strip_parser_functions` 开关的回归测试（KB-PROBE 报告 §4.3）。

原神是六款里唯一靠这个开关兜着的：19,918 页中 1,055 页（5.3%，六款最高）带
mwparserfromhell 0.7.2 配对不上的风险构造。开关在**解析之前**用正则把
`{{#…}}` 整块删掉，解析器根本看不到那些结构，所以解析树差异 0 页、全库
18,843/18,843 字段一致。

但这是「配置绕开」不是「代码修好」：开关一关，那 1,055 页会同时暴露在
KB-PROBE 报告 §2 的两种形状下——

  ① `{{#fn: … [[链接]] …}}`：链接的 `]]` 被当成模板右括号，外层模板提前闭合，
     **第一个 `|` 之后的参数整段消失**，不报错。
  ② `{{#fn: …<tag>…|note}}`：HTML 标签落在模板名段（第一个 `|` 之前），
     名字解析直接崩，整个模板节点消失。

本文件钉住这个开关：谁把 YAML 里的 true 改成 false、或把 gamecfg 的默认值翻
过去，这里先红。每条行为断言都配一条**反事实**（开关关掉会怎样）——没有反事
实，「开关生效」这种断言恒真，测试等于没写（KB-PROBE 踩过的假阴性坑）。

两条样例都取自原神真实页面（`哈贾娜德` / `兹白`），只裁掉无关段落，
风险构造本身一个字符没改。
"""

from __future__ import annotations

import pytest

from kblib import cleantext, gamecfg

# --------------------------------------------------------------- 真实样例
# 形状①：`{{#info:…[[「和旋舞剧」]]…}}` 落在 `称号` 的参数值里。页面
# `哈贾娜德`（须弥 NPC），全页 25 个字段，开关关掉只剩 2 个。
SHAPE1_WIKITEXT = """{{NPC
|名称=哈贾娜德
|称号=新晋学者{{#info:5.6版本[[「和旋舞剧」]]&月之二版本[[「参究故墟·稳健知行」]]活动期间}}
|性别=女
|职业=素论派学者
|所属国家=须弥
|中文CV=未知
|是否完善=否
}}"""

# 形状②：`<center>` 落在 `{{#info:` 的名字段（第一个 `|` 之前）。页面
# `兹白`，开关关掉同样只剩 2 个字段，且 `称号` 的值被截断在标签处。
SHAPE2_WIKITEXT = """{{角色
|名称=兹白
|称号={{#info:<center><b>琅风·兹白</b><br>月出恒兮，有女琚兮</center>}}
|性别=女
|职业=琅风
|所属国家=璃月
|中文CV=未知
|是否完善=否
}}"""

# 开关关掉时漏进正文的 DSL 长相（`|参数=值` 原样躺在正文里）。
# 断言它**不出现**，比断言字段数更直接：字段数对了也可能是碰巧。
DSL_LEAK = "|性别=女"


def _parse_like_pipeline(cfg: gamecfg.GameConfig, wikitext: str,
                         *, strip: bool) -> tuple[dict, str]:
    """照 parse.py 的每页路径跑一遍，返回 (infobox 字段, 正文纯文本)。

    与 parse.py 的差异只有一处：`strip` 由调用方给，而不是取 cfg 的值——
    反事实断言要的就是「同一份配置、只翻这一个旋钮」。
    """
    with cleantext.wiki_style(cfg.display_templates,
                              cfg.strip_template_name_comments,
                              strip):
        wc = cleantext.parse_wikitext(wikitext)
        found = cleantext.find_top_level_infobox(
            wc, set(cfg.infobox_templates), cfg.legacy_infobox_template_names,
            cfg.infobox_template_prefixes, descend_tags=cfg.descend_html_tags)
        fields = cleantext.infobox_fields(found) if found else {}
        body = cleantext.to_plain(wikitext)
    return fields, body


@pytest.fixture(scope="module")
def genshin() -> gamecfg.GameConfig:
    return gamecfg.load_game("genshin")


# ------------------------------------------------------- 配置本身（第一道闸）
def test_genshin_yaml_keeps_switch_on(genshin):
    """YAML 里这个 true 是原神 1,055 页的唯一护栏，翻成 false 立刻红。"""
    assert genshin.strip_parser_functions is True


def test_switch_defaults_off_for_other_games():
    """默认值必须保持关：碧蓝/方舟的 wikitext 里没有拆不动的解析函数，
    别为它们改一行行为（cleantext._STRIP_PARSER_FNS 的注释同此）。"""
    for name in ("azurlane", "arknights"):
        cfg = gamecfg.load_game(name)
        assert cfg.strip_parser_functions is False, (
            f"{name} 不该开 strip_parser_functions——它的输出会变")


# --------------------------------------------------------------- 形状①
def test_shape1_all_infobox_fields_survive(genshin):
    """开关开着：7 个字段一个不少，`称号` 里不残留 `#info` 的 DSL。"""
    fields, body = _parse_like_pipeline(genshin, SHAPE1_WIKITEXT, strip=True)

    assert set(fields) == {"名称", "称号", "性别", "职业", "所属国家",
                           "中文CV", "是否完善"}
    assert fields["称号"] == "新晋学者"
    assert DSL_LEAK not in body


def test_shape1_switch_off_loses_fields(genshin):
    """反事实：同一个旋钮关掉，7 → 2 个字段，DSL 漏进 `称号` 的值里。

    这条是上面那条的牙——没有它，「字段没丢」可能只是因为解析器本来就没崩。
    """
    fields, body = _parse_like_pipeline(genshin, SHAPE1_WIKITEXT, strip=False)

    assert len(fields) == 2
    assert "性别" not in fields and "职业" not in fields
    assert "#info" in (fields.get("称号") or "")
    assert DSL_LEAK in body


# --------------------------------------------------------------- 形状②
def test_shape2_html_tag_in_name_segment(genshin):
    """开关开着：6 个字段齐全，`称号` 整个值都是解析函数 → 剥干净成空、被丢掉。

    字段少一个不是漏抽：`称号` 的值**全部**是 `{{#info:…}}`，删干净就是空串，
    `infobox_fields` 不收空值。反事实那条里这个键是在的（带着 DSL），两条对着
    看才知道少的是「该少的那一个」。
    """
    fields, body = _parse_like_pipeline(genshin, SHAPE2_WIKITEXT, strip=True)

    assert set(fields) == {"名称", "性别", "职业", "所属国家",
                           "中文CV", "是否完善"}
    assert "称号" not in fields
    assert DSL_LEAK not in body


def test_shape2_switch_off_loses_fields(genshin):
    """反事实：标签落在名字段，模板节点整个消失，6 → 2 个字段。"""
    fields, body = _parse_like_pipeline(genshin, SHAPE2_WIKITEXT, strip=False)

    assert len(fields) == 2
    assert "性别" not in fields and "职业" not in fields
    assert "#info" in (fields.get("称号") or "")
    assert DSL_LEAK in body
