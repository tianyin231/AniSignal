"""按版本、商品与信号独立核验 AI 履约结果，不访问数据库或外部服务。"""

import hashlib
from html import unescape
import json
import math
import re
from collections.abc import Iterable

from app.services.product_matching import detect_product_signal_types, quality_claim_conflicts


SIGNAL_TYPES = frozenset({
    "presale_open", "balance_open", "delayed", "shipping_started",
    "aftersales_notice", "consumer_received", "quality_issue",
})
_VERDICTS = {"supported", "unsupported", "uncertain"}
_ACQUISITIONS = {"self_purchase", "seeded", "unknown"}
_ACTORS = {"official", "user", "seeded", "media", "merchant", "unknown"}
_KINDS = {"self_purchase", "pr_sample", "forward", "none"}
_AXIS_BASIS = {
    "presale_open": r"预售|预订|预约|pre.?order",
    "balance_open": r"尾款|补款|balance payment|final payment",
    "delayed": r"延[期迟后]|推迟|跳票|delay|postpone",
    "shipping_started": r"发货|出库|揽收|物流|shipp|dispatch",
    "aftersales_notice": r"售后|召回|维修|退换|保修|退款|after.?sales|recall|refund|repair",
    "consumer_received": r"到货|收到|收货|拿到|到手|开箱|拆箱|实物|实拍|到了|received|arrived|unbox|in hand",
    "quality_issue": r"质量|品控|做工|故障|瑕疵|问题|差|坏|无法|没[有]?反应|失灵|断连|漏光|连击|掉漆|不亮|不灵|断触|变形|开裂|咔擦声|异响|阻力|defect|broken|quality|fault|fail|not working",
}


def _has_axis_basis(signal_type: str, quote: str) -> bool:
    if not re.search(_AXIS_BASIS.get(signal_type, r"(?!)"), quote, re.I):
        return False
    if signal_type == "consumer_received" and re.search(r"期待|准备|等待|希望|将要|即将|计划|预计|早日|什么时候|waiting|expect|will unbox", quote, re.I):
        return bool(re.search(
            r"(?:已|已经|终于|今天|刚刚|刚).{0,4}(?:到货|收到|签收|到手)|到货了|收到货|到手了|拿到了|have received|has arrived",
            quote, re.I,
        ))
    return True


def _confidence(value) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and 0 <= value <= 100 else None


def _signal_item(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    product_id = value.get("product_id")
    confidence = _confidence(value.get("confidence"))
    if (
        isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0
        or value.get("signal_type") not in SIGNAL_TYPES
        or value.get("verdict") not in _VERDICTS
        or value.get("acquisition_type") not in _ACQUISITIONS
        or confidence is None
        or not isinstance(value.get("quote"), str)
        or (value.get("verdict") in {"supported", "unsupported"} and not value.get("quote", "").strip())
        or len(value.get("quote", "")) > 800
        or not isinstance(value.get("reason"), str)
        or not value["reason"].strip()
    ):
        return None
    return {
        "product_id": product_id,
        "signal_type": value["signal_type"],
        "verdict": value["verdict"],
        "confidence": confidence,
        "quote": value["quote"].strip()[:800],
        "reason": value["reason"].strip()[:240],
        "acquisition_type": value["acquisition_type"],
    }


def parse_fulfillment_review(value, *, product_ids: Iterable[int]) -> dict | None:
    """只接收完整的逐商品七轴结构；空数组、漏轴、重复轴均留给后台重试。"""
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        return None
    expected = set(product_ids)
    if not expected or not isinstance(value.get("signals"), list):
        return None
    confidence = _confidence(value.get("confidence"))
    if value.get("kind") not in _KINDS or confidence is None or not isinstance(value.get("reason"), str):
        return None
    signals = []
    keys = set()
    for raw_item in value["signals"]:
        item = _signal_item(raw_item)
        if item is None or item["product_id"] not in expected:
            return None
        key = (item["product_id"], item["signal_type"])
        if key in keys:
            return None
        keys.add(key)
        signals.append(item)
    if keys != {(product_id, signal_type) for product_id in expected for signal_type in SIGNAL_TYPES}:
        return None
    return {
        "schema_version": 2,
        "kind": value["kind"],
        "confidence": confidence,
        "reason": value["reason"].strip()[:240],
        "signals": signals,
    }


def has_current_fulfillment_review(
    review_fulfillment, *, content_version: int, product_ids: Iterable[int] | None = None,
) -> bool:
    """供审核去重和后台补偿共用；合法 uncertain 已完成分析，但仍可自动跟踪。"""
    if (not isinstance(review_fulfillment, dict) or type(review_fulfillment.get("content_version")) is not int
        or review_fulfillment.get("content_version") != content_version):
        return False
    signals = review_fulfillment.get("signals")
    if not isinstance(signals, list):
        return False
    actual_ids = {item["product_id"] for item in signals if isinstance(item, dict) and isinstance(item.get("product_id"), int)}
    if product_ids is not None and not set(product_ids) <= actual_ids:
        return False
    return parse_fulfillment_review(review_fulfillment, product_ids=actual_ids) is not None


def _quote_plain(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", value or ""))).strip()


def is_grounded_quote(quote: str, *, title: str, text: str) -> bool:
    plain = _quote_plain(quote)
    return bool(plain and (plain in _quote_plain(title) or plain in _quote_plain(text)))


def _explicit_acquisition(quote: str, acquisition: str) -> str:
    patterns = {
        "self_purchase": r"自购|自费(?:买|购)|自己(?:花钱)?(?:买|购)|我(?:已|已经|刚|自己)?(?:买|购|下单|付款)|补款|付尾款|补尾款|支付尾款",
        "seeded": r"送测|样品|官方寄送|品牌提供|免费提供|提供体验",
    }
    if acquisition not in patterns:
        return "unknown"
    for match in re.finditer(patterns[acquisition], quote):
        prefix = quote[max(0, match.start() - 6):match.start()]
        if not re.search(r"(?:不是|并非|没有|并不|非|未|不).{0,3}$", prefix):
            return acquisition
    return "unknown"


def decide_signal_automation(
    *, signal_type: str, product_id: int, actor_type: str, title: str, text: str,
    review_fulfillment: dict | None, content_version: int,
) -> dict:
    """只认证本商品、本轴和本版本；未知购买方式不妨碍确认实物展示。"""
    actor = actor_type if actor_type in _ACTORS else "unknown"
    result = {
        "review_status": "candidate", "acquisition_type": "seeded" if actor == "seeded" else "unknown",
        "actor_type": actor, "confidence": 0.0, "reason": "等待自动分析", "method": "rule", "quote": "",
    }
    matching = []
    if (
        isinstance(review_fulfillment, dict) and review_fulfillment.get("schema_version") == 2
        and type(review_fulfillment.get("content_version")) is int
        and review_fulfillment.get("content_version") == content_version
        and isinstance(review_fulfillment.get("signals"), list)
    ):
        matching = [item for value in review_fulfillment["signals"] if (item := _signal_item(value)) is not None
                    and item["product_id"] == product_id and item["signal_type"] == signal_type]
    if matching:
        result.update(method="ai", reason="依据不足，继续自动跟踪")
        for item in matching:
            item["quote"] = _quote_plain(item["quote"])
        highest = max(matching, key=lambda item: item["confidence"])
        result.update(confidence=highest["confidence"] / 100)
        grounded = [item for item in matching if item["confidence"] >= 85 and item["quote"]
                    and is_grounded_quote(item["quote"], title=title, text=text)]
        if not grounded:
            result["reason"] = (f"低置信度（{highest['confidence']}%），继续自动跟踪" if highest["confidence"] < 85
                                else "引文无法追溯，继续自动跟踪")
        verdicts = {item["verdict"] for item in grounded}
        if {"supported", "unsupported"} <= verdicts:
            result.update(review_status="conflict", reason="同一信号的分析结果矛盾，继续自动跟踪")
        elif grounded:
            item = max(grounded, key=lambda value: value["confidence"])
            result.update(confidence=item["confidence"] / 100, reason=item["reason"], quote=item["quote"])
            if item["verdict"] == "unsupported":
                result["review_status"] = "rejected"
            elif item["verdict"] == "uncertain":
                result["reason"] = f"信息不足，继续自动跟踪：{item['reason']}"
            elif item["verdict"] == "supported":
                quote = item["quote"]
                conflict = False
                if signal_type == "consumer_received":
                    conflict = bool(re.search(r"转发|转载|搬运|二剪|尚未收到|还没收到|没有收到|未到货", quote))
                    # 其他型号改出的主题键盘，不等于目标联名商品实物。
                    conflict = conflict or bool(re.search(r"用.{1,30}(?:改|做).{0,16}主题键盘", _quote_plain(title)))
                elif signal_type == "shipping_started" and re.search(r"预计|计划|将|尚未|还未|还没|没有|明天|下月", quote):
                    conflict = "shipping_started" not in detect_product_signal_types(quote, "", None)
                elif signal_type == "quality_issue":
                    conflict = quality_claim_conflicts(quote)
                if conflict:
                    result.update(review_status="conflict", reason="原文与支持判定存在冲突，继续自动跟踪")
                elif not _has_axis_basis(signal_type, quote):
                    result.update(reason="原文缺少该信号的直接依据，继续自动跟踪")
                else:
                    acquisition = _explicit_acquisition(quote, item["acquisition_type"])
                    if actor == "seeded":
                        acquisition = "seeded"
                    elif acquisition == "self_purchase" and actor in {"official", "merchant", "media"}:
                        acquisition = "unknown"
                    result.update(review_status="verified", acquisition_type=acquisition)
                    if acquisition == "seeded" and actor not in {"official", "merchant"}:
                        result["actor_type"] = "seeded"
                    elif acquisition == "self_purchase" and actor == "unknown":
                        result["actor_type"] = "user"
                    if signal_type not in {"consumer_received", "quality_issue"} and actor != "official":
                        result.update(review_status="candidate", reason="社区转述官方进展，继续自动跟踪")
    result["signature"] = hashlib.sha256(json.dumps({
        "product_id": product_id, "signal_type": signal_type, "content_version": content_version,
        "title": title, "text": text, "decision": result,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
    return result
