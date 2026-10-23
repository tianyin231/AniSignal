from copy import deepcopy

import pytest

from app.services.fulfillment_automation import (
    SIGNAL_TYPES, decide_signal_automation, has_current_fulfillment_review, parse_fulfillment_review,
)


def make_review(*, product_ids=(1,), content_version=2, updates=None):
    result = {
        "schema_version": 2, "content_version": content_version,
        "kind": "none", "confidence": 30, "reason": "逐轴分析",
        "signals": [{"product_id": pid, "signal_type": signal_type, "verdict": "uncertain", "confidence": 0,
                     "quote": "", "reason": "原文未涉及", "acquisition_type": "unknown"}
                    for pid in product_ids for signal_type in sorted(SIGNAL_TYPES)],
    }
    for key, fields in (updates or {}).items():
        next(item for item in result["signals"] if (item["product_id"], item["signal_type"]) == key).update(fields)
    return result


def decide(review, *, signal_type="consumer_received", product_id=1, title="爱弥斯键盘开箱", text="", actor_type="unknown", content_version=2):
    return decide_signal_automation(signal_type=signal_type, product_id=product_id, actor_type=actor_type,
        title=title, text=text, review_fulfillment=review, content_version=content_version)


def supported(quote, *, signal_type="consumer_received", acquisition_type="unknown", **kwargs):
    return make_review(updates={(1, signal_type): {
        "verdict": "supported", "confidence": 92, "quote": quote, "reason": "原文支持",
        "acquisition_type": acquisition_type, **kwargs,
    }})


def test_complete_uncertain_review_is_done_and_supports_product_subset():
    review = make_review(product_ids=(1, 2))
    assert has_current_fulfillment_review(review, content_version=2, product_ids=[1])
    assert has_current_fulfillment_review(review, content_version=2)
    assert not has_current_fulfillment_review(review, content_version=3)
    assert not has_current_fulfillment_review(review, content_version=2, product_ids=[1, 3])
    assert not has_current_fulfillment_review({**review, "signals": []}, content_version=2)


@pytest.mark.parametrize("mutation", [
    lambda value: value["signals"].pop(),
    lambda value: value["signals"].append(deepcopy(value["signals"][0])),
    lambda value: value["signals"][0].update(product_id=2),
    lambda value: value["signals"][0].update(confidence=101),
    lambda value: value["signals"][0].update(confidence=float("nan")),
    lambda value: value["signals"][0].update(confidence=True),
    lambda value: value["signals"][0].update(verdict="maybe"),
    lambda value: value["signals"][0].update(acquisition_type="unboxing"),
    lambda value: value["signals"][0].pop("quote"),
    lambda value: value["signals"][0].update(verdict="supported", quote=""),
    lambda value: value.update(schema_version=1),
])
def test_malformed_or_incomplete_analysis_stays_retryable(mutation):
    value = make_review()
    mutation(value)
    assert parse_fulfillment_review(value, product_ids=[1]) is None


def test_parse_whitelists_fields_and_does_not_trust_model_version():
    value = make_review()
    value.update(content_version=999, verified=True)
    value["signals"][0]["locked"] = True
    parsed = parse_fulfillment_review(value, product_ids=[1])
    assert "content_version" not in parsed and "verified" not in parsed
    assert "locked" not in parsed["signals"][0]


def test_physical_receipt_can_be_verified_without_guessing_purchase():
    result = decide(supported("爱弥斯键盘开箱", acquisition_type="self_purchase"))
    assert result["review_status"] == "verified"
    assert result["acquisition_type"] == "unknown"
    assert result["actor_type"] == "unknown"
    assert result["confidence"] == .92 and result["method"] == "ai"


def test_regular_sale_time_does_not_establish_a_presale_without_presale_context():
    quote = "开售时间：10月22日12:00"
    result = decide(supported(quote, signal_type="presale_open"), signal_type="presale_open",
                    title="交通卡现货发售", text=quote, actor_type="official")
    assert result["review_status"] == "candidate"


@pytest.mark.parametrize("actor,expected_actor,expected_acquisition", [
    ("unknown", "user", "self_purchase"), ("user", "user", "self_purchase"),
    ("seeded", "seeded", "seeded"), ("official", "official", "unknown"),
    ("merchant", "merchant", "unknown"), ("media", "media", "unknown"),
])
def test_source_identity_and_explicit_purchase_boundaries(actor, expected_actor, expected_acquisition):
    quote = "我自购的爱弥斯键盘已收到，今天开箱"
    result = decide(supported(quote, acquisition_type="self_purchase"), text=quote, actor_type=actor)
    assert result["review_status"] == "verified"
    assert result["actor_type"] == expected_actor and result["acquisition_type"] == expected_acquisition


def test_samples_and_negated_purchase_cannot_turn_into_self_purchase():
    quote = "不是自购，这是官方寄送的样品开箱"
    sample = decide(supported(quote, acquisition_type="seeded"), text=quote)
    assert sample["actor_type"] == "seeded" and sample["acquisition_type"] == "seeded"
    guessed = decide(supported(quote, acquisition_type="self_purchase"), text=quote)
    assert guessed["acquisition_type"] == "unknown"


def test_balance_payment_can_establish_purchase_without_certifying_other_axes():
    quote = "补款永远慢一步这就是会员购，终于到货开箱"
    review = supported(quote, acquisition_type="self_purchase")
    receipt = decide(review, text=quote)
    assert receipt["review_status"] == "verified" and receipt["acquisition_type"] == "self_purchase"
    quality = decide(review, signal_type="quality_issue", text=quote)
    assert quality["review_status"] == "candidate" and quality["acquisition_type"] == "unknown"


@pytest.mark.parametrize("review,reason", [
    (None, "等待自动分析"),
    ({"kind": "self_purchase", "confidence": 99}, "等待自动分析"),
    (supported("爱弥斯键盘开箱", confidence=84), "低置信度"),
    (supported("模型臆造的原文"), "引文无法追溯"),
])
def test_incomplete_low_confidence_and_untraceable_reviews_stay_candidates(review, reason):
    result = decide(review)
    assert result["review_status"] == "candidate" and reason in result["reason"]
    if review:
        assert decide(review, content_version=3)["review_status"] == "candidate"


def test_quote_normalizes_markup_but_does_not_join_title_and_body():
    quote = "爱弥斯键盘 开箱 & 实拍"
    review = supported(quote)
    assert decide(review, title="<b>爱弥斯键盘</b>\n开箱 &amp; 实拍")["review_status"] == "verified"
    assert decide(review, title="爱弥斯键盘", text="开箱 & 实拍")["review_status"] == "candidate"


@pytest.mark.parametrize("signal_type,title,quote", [
    ("consumer_received", "转发小李的爱弥斯键盘开箱", "转发小李的爱弥斯键盘开箱"),
    ("consumer_received", "用Amg65改了一个爱弥斯主题键盘", "更换键帽，diy出自己的爱弥斯键盘"),
    ("shipping_started", "爱弥斯键盘预售", "预计下月开始发货"),
    ("quality_issue", "爱弥斯键盘体验", "没有质量问题，不断连"),
    ("quality_issue", "爱弥斯键盘体验", "键盘没有异响"),
    ("quality_issue", "爱弥斯键盘体验", "按压没有明显阻力"),
    ("quality_issue", "爱弥斯键盘体验", "键盘没有咔擦声"),
])
def test_explicit_repost_diy_future_and_negation_conflict_with_claim(signal_type, title, quote):
    result = decide(supported(quote, signal_type=signal_type), signal_type=signal_type, title=title, text=quote)
    assert result["review_status"] == "conflict"


def test_product_comparison_never_certifies_another_products_quality():
    title = "鸣潮爱弥斯键盘品鉴和其他联名键盘对比"
    text = "爱弥斯和优诺键盘都是比较值的。达妮娅键盘别买，超贵质量还一般。"
    review = make_review(product_ids=(1, 2), updates={(2, "quality_issue"): {
        "verdict": "supported", "confidence": 96, "quote": "达妮娅键盘别买，超贵质量还一般", "reason": "只针对达妮娅",
    }})
    assert decide(review, product_id=1, signal_type="quality_issue", title=title, text=text)["review_status"] == "candidate"
    assert decide(review, product_id=2, signal_type="quality_issue", title=title, text=text)["review_status"] == "verified"


@pytest.mark.parametrize("quote", ["测试键盘的空格按下去完全没有反应", "没有断连，但是空格无法输入", "这么大的咔擦声，明显的阻力", "按键有异响", "没有异响，但空格完全没有反应", "无阻力，但是背光不亮"])
def test_actual_failure_is_not_mistaken_for_negating_a_quality_issue(quote):
    result = decide(supported(quote, signal_type="quality_issue"), signal_type="quality_issue", text=quote)
    assert result["review_status"] == "verified"


def test_only_official_actor_can_verify_official_progress():
    quote = "爱弥斯键盘首批已发货"
    review = supported(quote, signal_type="shipping_started")
    assert decide(review, signal_type="shipping_started", text=quote, actor_type="official")["review_status"] == "verified"
    result = decide(review, signal_type="shipping_started", text=quote, actor_type="user")
    assert result["review_status"] == "candidate" and "社区转述" in result["reason"]


@pytest.mark.parametrize("signal_type,quote", [
    ("consumer_received", "爱弥斯键盘已经下单付款，期待早日开箱"),
    ("quality_issue", "爱弥斯键盘开箱展示"),
    ("shipping_started", "爱弥斯键盘现已开启预售"),
    ("balance_open", "爱弥斯键盘已经发货"),
    ("aftersales_notice", "爱弥斯键盘很好看"),
])
def test_high_confidence_literal_quote_still_requires_axis_fact(signal_type, quote):
    result = decide(supported(quote, signal_type=signal_type), signal_type=signal_type, text=quote, actor_type="official")
    assert result["review_status"] == "candidate" and "直接依据" in result["reason"]


def test_explicit_unsupported_rejects_and_contradictory_axes_conflict():
    quote = "爱弥斯键盘尚未到货"
    review = supported(quote, verdict="unsupported")
    assert decide(review, text=quote)["review_status"] == "rejected"
    review["signals"].append({**next(item for item in review["signals"] if item["signal_type"] == "consumer_received"), "verdict": "supported"})
    assert decide(review, text=quote)["review_status"] == "conflict"


def test_automation_signature_is_stable_and_changes_with_version():
    review = supported("爱弥斯键盘开箱")
    first = decide(review)
    assert first == decide(deepcopy(review))
    assert first["signature"] != decide(review, content_version=3)["signature"]
