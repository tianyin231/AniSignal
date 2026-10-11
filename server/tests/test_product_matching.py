import pytest
from sqlalchemy import func, select

from app.models import ClassificationRule
from app.schemas.content import NormalizedContent
from app.services.classify import classify_content, load_rules, seed_defaults
from app.services.product_matching import detect_product_signal_types, matches_product_text


@pytest.mark.parametrize("title,text,terms,expected", [
    ("爱弥斯 联名－机械键盘 品控拉完了", None, ["爱弥斯联名机械键盘"], True),
    ("<em>爱弥斯</em>键盘", None, ["爱弥斯键盘"], True),
    ("ＣＯＤＥＸ－Ｋ１ 开箱", None, ["codex k1"], True),
    (None, "爱弥斯（联名）键盘开箱", ["爱弥斯联名键盘"], True),
    ("爱弥斯。键盘怎么样", None, ["爱弥斯键盘"], False),
    ("爱弥斯", "键盘开箱", ["爱弥斯键盘"], False),
    (None, "爱弥斯\n键盘", ["爱弥斯键盘"], False),
    ("爱弥斯人物传记", "绝区零键盘开箱", ["爱弥斯键盘"], False),
    ("爱弥斯磁轴键盘DIY", None, ["爱弥斯联名机械键盘", "爱弥斯键盘"], False),
    ("任意内容", None, ["", " ! "], False),
])
def test_product_text_matches_complete_terms_without_joining_sentences(title, text, terms, expected):
    assert matches_product_text(title, text, terms) is expected


def test_unboxing_and_quality_create_independent_candidates():
    assert detect_product_signal_types("爱弥斯键盘到货开箱，品控拉完了", None, "unboxing") == {
        "consumer_received", "quality_issue",
    }
    assert detect_product_signal_types("爱弥斯键盘品控拉完了", None, None) == {"quality_issue"}


@pytest.mark.parametrize("title,text,category", [
    ("爱弥斯键盘开启预售", "预计12月开始发货", "presale"),
    ("爱弥斯键盘发货计划", "计划下月陆续发货", "shipping"),
    ("爱弥斯键盘物流消息", "目前尚未开始发货", "shipping"),
    ("爱弥斯键盘", "什么时候开始发货？", "shipping"),
    ("爱弥斯键盘延期", "还没有开始发货", "delay"),
    ("爱弥斯键盘预售中", "12月开始发货", "presale"),
    ("爱弥斯键盘", "新批次将开始发货", "shipping"),
    ("爱弥斯键盘发货时间", "12月开始发货", "shipping"),
])
def test_future_or_negated_shipping_is_not_current_progress(title, text, category):
    assert "shipping_started" not in detect_product_signal_types(title, text, category)


def test_actual_shipping_and_presale_can_coexist():
    assert detect_product_signal_types("爱弥斯键盘第二批预售", "首批现已开始发货。新批次预计下月发货。", "presale") == {
        "presale_open", "shipping_started",
    }


@pytest.mark.parametrize("title", ["爱弥斯键盘品控不错", "爱弥斯键盘没有瑕疵", "爱弥斯键盘没有质量问题"])
def test_positive_quality_is_not_an_issue(title):
    assert "quality_issue" not in detect_product_signal_types(title, None, "quality")


async def test_new_quality_rules_and_transactional_seed(session):
    count = await seed_defaults(session, commit=False)
    assert count > 0
    rules = await load_rules(session)
    content = NormalizedContent(platform="bilibili", content_type="video", external_id="q", title="爱弥斯键盘品控拉完了")
    assert classify_content(content, rules) == "quality"
    assert await seed_defaults(session, commit=False) == 0
    await session.rollback()
    assert await session.scalar(select(func.count(ClassificationRule.id))) == 0


def test_ai_product_context_uses_same_text_match_and_not_search_keywords():
    from app.services.curation import _match_product_context

    products = [{"id": 1, "name": "爱弥斯键盘", "terms": ["爱弥斯键盘"], "community": "wuwa"}]
    assert _match_product_context(products, "wuwa", "爱弥斯－键盘开箱", "", []) == {
        "products": [{"id": 1, "name": "爱弥斯键盘", "matched_terms": ["爱弥斯键盘"]}],
    }
    assert _match_product_context(products, "wuwa", "爱弥斯人物传记", "", ["爱弥斯键盘"]) is None
    assert _match_product_context(products, "wuwa", "爱弥斯", "键盘", []) is None
    assert _match_product_context(products, "hsr", "爱弥斯键盘", "", []) is None
