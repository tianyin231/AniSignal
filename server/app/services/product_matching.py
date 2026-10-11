"""商品命中与履约候选的纯规则；候选仍须经证据审核后才能公开。"""

import html
import re
import unicodedata
from collections.abc import Iterable


_SENTENCE_END = re.compile(r"[。！？!?；;\r\n.]+")
_HTML_TAG = re.compile(r"<[^>]+>")
QUALITY_PROBLEM_TERMS = (
    "品控", "质量问题", "质量差", "做工问题", "故障", "瑕疵", "掉漆",
    "失灵", "断连", "坏轴", "漏轴", "连击", "漏光", "歪轴", "键帽松动",
)
_FUTURE_OR_NEGATED = re.compile(
    r"预计|预期|计划|将|即将|准备|等待|承诺|尚未|还未|还没|没有|未能|不能|暂不|不再|什么时候|何时"
    r"|明天|明日|后天|下周|下月|月底|延期|推迟|延迟"
)
_SHIPPING = re.compile(
    r"(?:现已|已经|已|开始|陆续|正在|正式|首批).{0,6}(?:发货|出库|揽收)"
    r"|(?:发货|出库|揽收)了|物流(?:已|已经)?更新"
)
_QUALITY_POSITIVE = re.compile(r"(?:品控|质量).{0,4}(?:不错|很好|很棒|优秀|完美|正常|过关|没问题|没有问题)")
_CURRENT_SHIPPING = re.compile(
    r"(?:现已|已经|已|正在|今天|今日|目前).{0,6}(?:发货|出库|揽收)"
    r"|(?:发货|出库|揽收)了|物流(?:已|已经)?更新"
)


def _plain(value: str | None) -> str:
    return unicodedata.normalize("NFKC", html.unescape(_HTML_TAG.sub("", value or ""))).casefold()


def _compact(value: str) -> str:
    return "".join(
        char for char in value
        if not char.isspace() and not unicodedata.category(char).startswith(("P", "S"))
    )


def matches_product_text(title: str | None, text: str | None, watch_terms: Iterable[str]) -> bool:
    """忽略排版差异匹配完整商品词；标题、正文和不同句子之间不会拼出命中。"""
    terms = {_compact(_plain(term)) for term in watch_terms if term}
    terms.discard("")
    return any(
        term in _compact(sentence)
        for value in (title, text)
        for sentence in _SENTENCE_END.split(_plain(value))
        for term in terms
    )


def _affirmed_occurrence(sentence: str, term: str) -> bool:
    for match in re.finditer(re.escape(term), sentence):
        prefix = sentence[max(0, match.start() - 5):match.start()]
        if not re.search(r"(?:没有|并无|不存在|未发现|没发现|无|未出现).{0,2}$", prefix):
            return True
    return False


def detect_product_signal_types(title: str | None, text: str | None, category: str | None) -> set[str]:
    """调用方须先确认商品命中；按多轴返回候选，不能据此认定已履约。

    发货只能来自当前动作证据，不接受 shipping 类目或预售中的未来日期兜底。
    """
    sentences = [
        part for value in (title, text)
        for part in re.split(r"[。！？!?；;，,\r\n.]+", _plain(value)) if part
    ]
    signals: set[str] = set()
    for signal, terms, categories in (
        ("presale_open", ("预售", "开启预约", "开启预订"), {"presale"}),
        ("balance_open", ("尾款",), {"balance"}),
        ("delayed", ("延期", "推迟", "跳票", "延迟发货"), {"delay"}),
        ("consumer_received", ("开箱", "晒单", "已收到", "收到了", "已经收到", "已到货", "到货了"), {"arrived", "unboxing"}),
        ("aftersales_notice", ("售后公告", "售后说明", "召回", "退换货通知"), set()),
    ):
        if category in categories or any(
            _affirmed_occurrence(sentence, term) for sentence in sentences for term in terms
        ):
            signals.add(signal)
    quality = False
    for sentence in sentences:
        # 正面品控评价与否定问题陈述，不作为质量异常候选。
        checked = _QUALITY_POSITIVE.sub("", sentence)
        if any(_affirmed_occurrence(checked, term) for term in QUALITY_PROBLEM_TERMS):
            quality = True
            break
    if quality or (category == "quality" and not any(
        _QUALITY_POSITIVE.search(sentence) or re.search(r"(?:没有|无|未发现).{0,3}(?:质量问题|瑕疵|故障)", sentence)
        for sentence in sentences
    )):
        signals.add("quality_issue")
    for sentence in sentences:
        for match in _SHIPPING.finditer(sentence):
            prefix = sentence[:match.end()]
            # 预售/日期计划中的“开始发货”仍可能是未来承诺，须有明确当前动作。
            planned_context = "presale_open" in signals or re.search(r"\d{1,2}月|\d{1,2}[/-]\d{1,2}", prefix)
            if not _FUTURE_OR_NEGATED.search(prefix) and (not planned_context or _CURRENT_SHIPPING.search(prefix)):
                signals.add("shipping_started")
    return signals
