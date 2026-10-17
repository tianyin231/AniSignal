"""布尔关键词解析：Mention 风格语法，容错优先。

语法（大小写不敏感）：
- `A AND B` / `A B`      同时包含（相邻词默认 AND）
- `A OR B` / `A, B` / `A | B`   任一包含
- `A NOT B` / `A -B` / `A !B`   包含 A 且不含 B
- `+A -B`                必含 / 排除（Google 风格）
- `(A OR B) AND C`       分组，括号可省略半边
- `"鸣潮 2.0"`           引号短语作为一个整体（引号内 AND/OR/NOT 按字面处理）
- 全角 `（ ） ， ！ ＆ ｜` 视作对应的半角符号

容错原则：用户写错表达式不该让规则失效，只尽量还原意图——
空表达式 / 只有运算符 → None（表示不限关键词，匹配全部）；
多余的右括号、未闭合的左括号、悬空运算符、没有操作数的 NOT 都被安全丢弃。

自写实现，仅借鉴语法约定。
"""

from __future__ import annotations

from dataclasses import dataclass

# 全角 → 半角（中文输入法下最常见的写法）
_FULLWIDTH = str.maketrans({"（": "(", "）": ")", "，": ",", "！": "!", "＆": "&", "｜": "|"})

_OPERATORS = {"AND", "OR", "NOT", "&&", "||", "!"}


@dataclass(frozen=True)
class Term:
    """一个关键词（negated=True 表示"不含"）。"""

    value: str
    negated: bool = False


@dataclass(frozen=True)
class Group:
    """一组子条件的与/或组合（children 至少一个）。"""

    op: str  # "and" | "or"
    children: tuple


Query = Term | Group


def _tokenize(expr: str) -> list[tuple[str, str]]:
    """切成 (kind, value)；kind ∈ text / and / or / not / lparen / rparen。"""
    text = (expr or "").translate(_FULLWIDTH)
    tokens: list[tuple[str, str]] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "\"'“”‘’":
            closing = {"“": "”", "‘": "’", '"': '"', "'": "'"}[char]
            end = text.find(closing, index + 1)
            if end == -1:  # 引号没闭合：剩下全部当作短语
                end = length
            phrase = text[index + 1 : end].strip()
            if phrase:
                tokens.append(("text", phrase))
            index = end + 1
            continue
        if char == "(":
            tokens.append(("lparen", char))
            index += 1
            continue
        if char == ")":
            tokens.append(("rparen", char))
            index += 1
            continue
        if char == ",":  # 逗号等同 OR
            tokens.append(("or", char))
            index += 1
            continue
        if char in "&|!":
            pair = text[index : index + 2]
            if pair in ("&&", "||"):
                tokens.append(("and" if pair == "&&" else "or", pair))
                index += 2
                continue
            # 单个符号：& 视作 AND、| 视作 OR、! 视作 NOT（全角 ＆｜！也走到这里）
            tokens.append(("and" if char == "&" else "or" if char == "|" else "not", char))
            index += 1
            continue
        if char in "+-":
            # +/- 只在前置时是修饰符；夹在词中间（如 wifi-6）仍算普通字符
            following = text[index + 1 : index + 2]
            if following and not following.isspace() and following not in "()\"'“”‘’":
                end = index + 1
                while end < length and not text[end].isspace() and text[end] not in "(),&|!\"'“”‘’":
                    end += 1
                word = text[index + 1 : end]
                if char == "-":
                    tokens.append(("not", char))  # -词 = NOT 词
                # "+词" 在默认 AND 语义下等于词本身，只保留词
                if word:
                    tokens.append(("text", word))
                index = end
                continue
            tokens.append(("not" if char == "-" else "and", char))
            index += 1
            continue
        end = index
        while end < length and not text[end].isspace() and text[end] not in "(),&|!\"'“”‘’":
            end += 1
        word = text[index:end]
        index = end
        upper = word.upper()
        if upper in ("AND", "OR", "NOT"):
            tokens.append((upper.lower(), word))
        else:
            tokens.append(("text", word))
    return tokens


class _Parser:
    """递归下降：or → and → unary → primary。容错全部落在"跳过看不懂的 token"上。"""

    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> tuple[str, str] | None:
        token = self._peek()
        if token is not None:
            self.pos += 1
        return token

    def parse(self) -> Query | None:
        node = self._parse_or()
        return node

    def _parse_or(self) -> Query | None:
        parts: list[Query] = []
        node = self._parse_and()
        if node is not None:
            parts.append(node)
        while True:
            token = self._peek()
            if token is None or token[0] != "or":
                break
            self._next()
            # 悬空 OR（结尾或连续运算符）：吃掉即可，不当成关键词
            following = self._peek()
            if following is None or following[0] in ("or", "and", "not", "rparen"):
                continue
            node = self._parse_and()
            if node is not None:
                parts.append(node)
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return Group("or", tuple(parts))

    def _parse_and(self) -> Query | None:
        parts: list[Query] = []
        while True:
            token = self._peek()
            if token is None or token[0] in ("or", "rparen"):
                break
            if token[0] == "and":
                self._next()
                # 悬空 AND（结尾或连续运算符）：吃掉即可，不当成关键词
                following = self._peek()
                if following is None or following[0] in ("or", "and", "not", "rparen"):
                    continue
                node = self._parse_unary()
                if node is not None:
                    parts.append(node)
                continue
            before = self.pos
            node = self._parse_unary()  # 前置 NOT 由这里正确处理
            if node is not None:
                parts.append(node)
            elif self.pos == before:
                self._next()  # 保险：任何分支都要前进，避免死循环
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return Group("and", tuple(parts))

    def _parse_unary(self) -> Query | None:
        token = self._peek()
        if token is not None and token[0] == "not":
            self._next()
            node = self._parse_unary()
            if node is None:  # NOT 没有操作数
                return None
            return _negate(node)
        return self._parse_primary()

    def _parse_primary(self) -> Query | None:
        token = self._next()
        if token is None:
            return None
        kind, value = token
        if kind == "lparen":
            node = self._parse_or()
            # 未闭合 / 多余的右括号都安全消化
            closing = self._peek()
            if closing is not None and closing[0] == "rparen":
                self._next()
            return node
        if kind == "text":
            return Term(value)
        # 其它 token（and/or/not/rparen）不该出现在这里，安全跳过
        return None


def _negate(node: Query) -> Query:
    """把否定推到叶子节点，便于直接求值。"""
    if isinstance(node, Term):
        return Term(node.value, not node.negated)
    return Group(node.op, tuple(_negate(child) for child in node.children))


def parse(expr: str | None) -> Query | None:
    """解析布尔表达式；None 表示"不限关键词"。"""
    tokens = _tokenize(expr or "")
    if not tokens:
        return None
    return _Parser(tokens).parse()


def evaluate(node: Query | None, text: str) -> bool:
    """node 为 None 时匹配任意文本（不限关键词）。"""
    if node is None:
        return True
    folded = (text or "").casefold()
    return _eval(node, folded)


def _eval(node: Query, folded: str) -> bool:
    if isinstance(node, Term):
        hit = node.value.casefold() in folded
        return (not hit) if node.negated else hit
    if node.op == "or":
        return any(_eval(child, folded) for child in node.children)
    return all(_eval(child, folded) for child in node.children)


def describe(node: Query | None) -> str:
    """把解析结果还原成可读字符串（管理端展示"这条规则实际在匹配什么"）。"""
    if node is None:
        return "不限关键词"
    return _describe(node)


def _describe(node: Query) -> str:
    if isinstance(node, Term):
        return f"NOT {node.value}" if node.negated else node.value
    joiner = " AND " if node.op == "and" else " OR "
    parts = []
    for child in node.children:
        rendered = _describe(child)
        if isinstance(child, Group) and child.op != node.op:
            rendered = f"({rendered})"
        parts.append(rendered)
    return joiner.join(parts)
