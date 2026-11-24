"""内容版本修订历史与可视化 Diff 服务（官方公告/动态变更追溯）。

核心职责：
1. 将 HTML/纯文本规范化为可读行文本，屏蔽纯标签格式变化、B 站 CDN 分片轮换与 YouTube 封面 ?v= 参数轮换噪声；
2. 过滤 parser 重算、分类调整、瞬时抓取抖动（A→B→A 丢正文/转发卡）与初次截断/空动态补全，仅保留真实内容演进；
3. 明确区分「源站原文更新（source）」与「站内管理员修订（admin）」；
4. 计算标题与正文的块级 + 行内词粒 Diff，以及图片增删对比；查询侧按列裁剪避免传输冗余的大体积 JSON。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import difflib
from html import unescape
import json
import re
from typing import Any

import bleach
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContentVersion
from app.services.fingerprint import canonical_media
from app.services.urls import safe_media_url

_BLOCK_BREAK_RE = re.compile(
    r"(?i)(?:<\s*br\s*/?\s*>|</\s*(?:p|div|li|tr|h[1-6]|section|article|blockquote|pre)\s*>)"
)
_INLINE_WS_RE = re.compile(r"[ \t\u00a0]+")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
# 行内分词：英文/数字/时间/版本号保持整段，中文按单字切分，确保“11:00→13:00”或“星声×300→星声×600”精确高亮。
_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]+(?:[.:/-][A-Za-z0-9]+)*|[\u4e00-\u9fff]|[^\sA-Za-z0-9\u4e00-\u9fff]|\s+"
)

_MAX_DIFF_CHARS = 50_000
_MAX_INLINE_PAIR_CHARS = 4_000
_MAX_DIFF_LINES = 300
_MIN_INLINE_SIMILARITY = 0.35
_CONTEXT_LINES = 2
_MAX_VERSIONS_PER_CONTENT = 60


@dataclass(frozen=True)
class VersionInput:
    """轻量化版本投影，避免为了算修订摘要把 normalized_json 中重复的大段正文全量拉回内存。"""

    version: int
    parsed_at: datetime
    parser_version: str | None
    title: str | None
    text: str | None
    media: list[Any]
    truncated: bool = False


@dataclass(frozen=True)
class SubstantiveVersion:
    version: int
    parsed_at: datetime
    origin: str  # "source" | "admin"
    title: str
    text: str
    media: tuple[str, ...]
    media_keys: tuple[str, ...]
    truncated: bool

    @property
    def signature(self) -> tuple[str, str, tuple[str, ...]]:
        return (self.title, self.text, self.media_keys)


@dataclass(frozen=True)
class RevisionSummary:
    revision_count: int = 0
    has_source_revision: bool = False
    latest_version: int = 1


def version_origin(parser_version: str | None) -> str:
    """管理员手工修订在 parser_version 中带 +admin 标记，与源站原文变更严格区分。"""
    return "admin" if "+admin" in (parser_version or "") else "source"


def readable_title(raw_title: str | None) -> str:
    text = unescape(raw_title or "")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _INLINE_WS_RE.sub(" ", text.replace("\r", " ").replace("\n", " "))
    return text.strip()


def readable_text(raw_text: str | None) -> str:
    if not raw_text:
        return ""
    text = raw_text
    if "<" in text and ">" in text:
        text = _BLOCK_BREAK_RE.sub("\n", text)
        text = bleach.clean(text, tags=[], strip=True)
    text = unescape(text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        cleaned
        for line in text.split("\n")
        if (cleaned := _INLINE_WS_RE.sub(" ", line).strip())
    ]
    normalized = "\n".join(lines)
    if len(normalized) > _MAX_DIFF_CHARS:
        return normalized[:_MAX_DIFF_CHARS]
    return normalized


def _decode_json_field(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def extract_media_urls(raw_media: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    decoded = _decode_json_field(raw_media)
    items = decoded if isinstance(decoded, list) else []
    urls: list[str] = []
    keys: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        safe = safe_media_url(item)
        if not safe:
            continue
        key = canonical_media(safe)
        if key in seen:
            continue
        seen.add(key)
        urls.append(safe)
        keys.append(key)
    return tuple(urls), tuple(keys)


def extract_media(normalized_json: dict | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return extract_media_urls((normalized_json or {}).get("media"))


def _coerce_input(row: ContentVersion | VersionInput) -> VersionInput:
    if isinstance(row, VersionInput):
        return row
    normalized = row.normalized_json if isinstance(row.normalized_json, dict) else {}
    raw_text = row.text if row.text is not None else str(normalized.get("text") or "")
    extra = normalized.get("extra") if isinstance(normalized.get("extra"), dict) else {}
    raw_media = normalized.get("media") if isinstance(normalized.get("media"), list) else []
    return VersionInput(
        version=row.version,
        parsed_at=row.parsed_at,
        parser_version=row.parser_version,
        title=row.title,
        text=raw_text,
        media=raw_media,
        truncated=bool(extra.get("truncated")),
    )


def _to_substantive(item: VersionInput) -> SubstantiveVersion:
    media, media_keys = extract_media_urls(item.media)
    return SubstantiveVersion(
        version=item.version,
        parsed_at=item.parsed_at,
        origin=version_origin(item.parser_version),
        title=readable_title(item.title),
        text=readable_text(item.text),
        media=media,
        media_keys=media_keys,
        truncated=item.truncated,
    )


def _starts_with_prefix(full_text: str, short_text: str) -> bool:
    prefix = short_text.rstrip("…. ").strip()
    return len(prefix) >= 15 and full_text.startswith(prefix)


def _is_backfill_transition(
    prev: SubstantiveVersion,
    curr: SubstantiveVersion,
    *,
    platform: str | None = None,
) -> bool:
    """识别初次抓取缺正文或截断摘要被补全为全文的过渡，避免误报为“原文修订”。

    覆盖线上实测四类非编辑补全：
    1. B 站空动态初抓无正文 → 后续详情自愈回填；
    2. X 显式 truncated 标记或旧版 parser 截断预览 → 全文补全；
    3. B 站列表 ~250 字简介截断、或转发动态初抓缺失原动态卡片 → 完整正文补全；
    4. RSS 短摘要 → trafilatura 正文补全。
    不作用于官网公告（kuro_cn/game_official/miyoushe/steam），确保滚动更新说明不被误吞。
    """
    if prev.origin != "source" or curr.origin != "source":
        return False
    if not prev.text and bool(curr.text):
        return True
    if prev.truncated and not curr.truncated and (not prev.text or _starts_with_prefix(curr.text, prev.text)):
        return True
    if not prev.text or len(curr.text) <= len(prev.text) + 30:
        return False
    if not _starts_with_prefix(curr.text, prev.text):
        return False
    if platform == "bilibili":
        # 250 字列表截断补全，或转发动态缺失被转发原动态卡片后的补全
        if len(prev.text) <= 270:
            return True
    elif platform == "x":
        if len(prev.text) <= 320:
            return True
    elif platform == "rss":
        if len(prev.text) <= 700 and len(curr.text) >= len(prev.text) * 1.8:
            return True
    return False


def _is_transient_drop(candidate: SubstantiveVersion, anchor: SubstantiveVersion) -> bool:
    """判断中间版本是否只是采集抖动造成的残缺版本（如 B 站转发卡丢失、X 瞬时返回空正文）。

    若后续版本恢复成与 anchor 完全一致的内容，且中间版本没有引入任何新词句或新图片，
    则判定为抓取抖动而非官方“修改后再改回”。
    """
    if candidate.origin != "source" or anchor.origin != "source":
        return False
    if not set(candidate.media_keys).issubset(anchor.media_keys):
        return False
    if not candidate.text and bool(anchor.text):
        return True
    if len(candidate.text) < len(anchor.text):
        lines = [line for line in candidate.text.splitlines() if line]
        if lines and all(line in anchor.text for line in lines):
            return True
    return False


def collapse_substantive_versions(
    rows: Sequence[ContentVersion | VersionInput],
    *,
    platform: str | None = None,
) -> list[SubstantiveVersion]:
    """按版本号升序折叠非实质变更、采集抖动与初次补全，返回真实内容演进序列。"""
    inputs = [_coerce_input(row) for row in rows]
    ordered = sorted(inputs, key=lambda item: item.version)
    if len(ordered) > _MAX_VERSIONS_PER_CONTENT:
        ordered = [ordered[0], *ordered[-(_MAX_VERSIONS_PER_CONTENT - 1):]]

    kept: list[SubstantiveVersion] = []
    last_raw_key: tuple[str, str, tuple[str, ...]] | None = None

    for item in ordered:
        # 原始串与图片键完全相同时直接短路，避免对纯分类重算版本反复执行 bleach HTML 清洗。
        _, media_keys = extract_media_urls(item.media)
        raw_key = (item.title or "", item.text or "", media_keys)
        if last_raw_key is not None and raw_key == last_raw_key:
            continue
        last_raw_key = raw_key

        curr = _to_substantive(item)
        if not kept:
            kept.append(curr)
            continue
        prev = kept[-1]
        if curr.signature == prev.signature:
            continue
        if len(kept) == 1 and _is_backfill_transition(prev, curr, platform=platform):
            kept[0] = curr
            continue

        # 折叠 A → B(残缺抖动) → A 环路；若 B 引入了新内容（如 11:00→13:00→11:00）则完整保留。
        folded_flap = False
        for idx in range(len(kept) - 2, -1, -1):
            if kept[idx].signature == curr.signature and all(
                _is_transient_drop(mid, kept[idx]) for mid in kept[idx + 1 :]
            ):
                del kept[idx + 1 :]
                folded_flap = True
                break
        if folded_flap:
            continue

        kept.append(curr)
    return kept


def summarize_versions(
    rows: Sequence[ContentVersion | VersionInput],
    *,
    platform: str | None = None,
) -> RevisionSummary:
    if not rows:
        return RevisionSummary()
    latest_version = max(item.version for item in rows)
    kept = collapse_substantive_versions(rows, platform=platform)
    if len(kept) <= 1:
        return RevisionSummary(revision_count=0, has_source_revision=False, latest_version=latest_version)
    revisions = kept[1:]
    return RevisionSummary(
        revision_count=len(revisions),
        has_source_revision=any(item.origin == "source" for item in revisions),
        latest_version=latest_version,
    )


def _merge_segments(raw_segments: list[tuple[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    for op, text in raw_segments:
        if not text:
            continue
        if merged and merged[-1]["op"] == op:
            merged[-1]["text"] += text
        else:
            merged.append({"op": op, "text": text})
    return merged


def inline_diff_segments(old_text: str, new_text: str) -> list[dict[str, str]]:
    """计算单行文本（如标题）的内联合并 Diff 片段（equal/delete/insert）。"""
    old_tokens = _TOKEN_RE.findall(old_text)
    new_tokens = _TOKEN_RE.findall(new_text)
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    raw: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            raw.append(("equal", "".join(old_tokens[i1:i2])))
        elif tag == "delete":
            raw.append(("delete", "".join(old_tokens[i1:i2])))
        elif tag == "insert":
            raw.append(("insert", "".join(new_tokens[j1:j2])))
        elif tag == "replace":
            raw.append(("delete", "".join(old_tokens[i1:i2])))
            raw.append(("insert", "".join(new_tokens[j1:j2])))
    return _merge_segments(raw)


def _pair_line_segments(
    old_line: str, new_line: str
) -> tuple[list[dict[str, str]] | None, list[dict[str, str]] | None]:
    """为改写后的相邻旧/新行分别生成行内高亮片段；差异过大时放弃行内标注保持整行清晰。"""
    if not old_line or not new_line or len(old_line) + len(new_line) > _MAX_INLINE_PAIR_CHARS:
        return None, None
    old_tokens = _TOKEN_RE.findall(old_line)
    new_tokens = _TOKEN_RE.findall(new_line)
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    if matcher.ratio() < _MIN_INLINE_SIMILARITY:
        return None, None
    old_raw: list[tuple[str, str]] = []
    new_raw: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            seg = "".join(old_tokens[i1:i2])
            old_raw.append(("equal", seg))
            new_raw.append(("equal", seg))
        elif tag == "delete":
            old_raw.append(("delete", "".join(old_tokens[i1:i2])))
        elif tag == "insert":
            new_raw.append(("insert", "".join(new_tokens[j1:j2])))
        elif tag == "replace":
            old_raw.append(("delete", "".join(old_tokens[i1:i2])))
            new_raw.append(("insert", "".join(new_tokens[j1:j2])))
    return _merge_segments(old_raw), _merge_segments(new_raw)


def compute_version_diff(old: SubstantiveVersion, new: SubstantiveVersion) -> dict:
    """计算两个实质版本间的完整结构化对比结果。"""
    title_changed = old.title != new.title
    title_segments = inline_diff_segments(old.title, new.title) if title_changed else []

    old_lines = old.text.splitlines() if old.text else []
    new_lines = new.text.splitlines() if new.text else []
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)

    hunks: list[dict] = []
    added_lines = 0
    removed_lines = 0
    emitted_lines = 0
    truncated = False

    for group in matcher.get_grouped_opcodes(n=_CONTEXT_LINES):
        hunk_lines: list[dict] = []
        old_start = group[0][1] + 1
        new_start = group[0][3] + 1
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for offset in range(i2 - i1):
                    if emitted_lines >= _MAX_DIFF_LINES:
                        truncated = True
                        break
                    hunk_lines.append({
                        "op": "equal",
                        "old_no": i1 + offset + 1,
                        "new_no": j1 + offset + 1,
                        "text": old_lines[i1 + offset],
                        "segments": None,
                    })
                    emitted_lines += 1
            elif tag == "delete":
                removed_lines += i2 - i1
                for idx in range(i1, i2):
                    if emitted_lines >= _MAX_DIFF_LINES:
                        truncated = True
                        break
                    hunk_lines.append({
                        "op": "delete",
                        "old_no": idx + 1,
                        "new_no": None,
                        "text": old_lines[idx],
                        "segments": None,
                    })
                    emitted_lines += 1
            elif tag == "insert":
                added_lines += j2 - j1
                for idx in range(j1, j2):
                    if emitted_lines >= _MAX_DIFF_LINES:
                        truncated = True
                        break
                    hunk_lines.append({
                        "op": "insert",
                        "old_no": None,
                        "new_no": idx + 1,
                        "text": new_lines[idx],
                        "segments": None,
                    })
                    emitted_lines += 1
            elif tag == "replace":
                old_block = old_lines[i1:i2]
                new_block = new_lines[j1:j2]
                removed_lines += len(old_block)
                added_lines += len(new_block)
                paired = min(len(old_block), len(new_block)) if max(len(old_block), len(new_block)) <= 12 else 0
                old_segs_list: list[list[dict[str, str]] | None] = [None] * len(old_block)
                new_segs_list: list[list[dict[str, str]] | None] = [None] * len(new_block)
                for pair_idx in range(paired):
                    old_segs, new_segs = _pair_line_segments(old_block[pair_idx], new_block[pair_idx])
                    old_segs_list[pair_idx] = old_segs
                    new_segs_list[pair_idx] = new_segs
                for offset, line in enumerate(old_block):
                    if emitted_lines >= _MAX_DIFF_LINES:
                        truncated = True
                        break
                    hunk_lines.append({
                        "op": "delete",
                        "old_no": i1 + offset + 1,
                        "new_no": None,
                        "text": line,
                        "segments": old_segs_list[offset],
                    })
                    emitted_lines += 1
                for offset, line in enumerate(new_block):
                    if emitted_lines >= _MAX_DIFF_LINES:
                        truncated = True
                        break
                    hunk_lines.append({
                        "op": "insert",
                        "old_no": None,
                        "new_no": j1 + offset + 1,
                        "text": line,
                        "segments": new_segs_list[offset],
                    })
                    emitted_lines += 1
            if truncated:
                break
        if hunk_lines:
            hunks.append({
                "old_start": old_start,
                "new_start": new_start,
                "lines": hunk_lines,
            })
        if truncated:
            break

    old_media_map = dict(zip(old.media_keys, old.media))
    new_media_map = dict(zip(new.media_keys, new.media))
    media_added = [new_media_map[k] for k in new.media_keys if k not in old_media_map]
    media_removed = [old_media_map[k] for k in old.media_keys if k not in new_media_map]

    return {
        "from_version": old.version,
        "to_version": new.version,
        "from_parsed_at": old.parsed_at,
        "to_parsed_at": new.parsed_at,
        "to_origin": new.origin,
        "title_changed": title_changed,
        "title_segments": title_segments,
        "hunks": hunks,
        "media_added": media_added,
        "media_removed": media_removed,
        "stats": {
            "added_lines": added_lines,
            "removed_lines": removed_lines,
            "title_changed": title_changed,
            "media_added": len(media_added),
            "media_removed": len(media_removed),
        },
        "truncated": truncated,
    }


def build_version_history(
    content_id: int,
    rows: Sequence[ContentVersion | VersionInput],
    *,
    platform: str | None = None,
    from_version: int | None = None,
    to_version: int | None = None,
) -> dict:
    """构造单条内容的实质版本列表与指定区间 Diff；若版本号无效抛出 ValueError。"""
    latest_version = max((item.version for item in rows), default=0)
    kept = collapse_substantive_versions(rows, platform=platform)
    versions_out = [
        {
            "version": item.version,
            "parsed_at": item.parsed_at,
            "origin": item.origin,
            "title": item.title,
            "text_length": len(item.text),
            "media_count": len(item.media),
        }
        for item in kept
    ]
    revision_count = max(0, len(kept) - 1)
    has_source_revision = any(item.origin == "source" for item in kept[1:])

    diff_out = None
    if len(kept) >= 2:
        by_ver = {item.version: item for item in kept}
        if to_version is None:
            target = kept[-1]
        elif to_version in by_ver:
            target = by_ver[to_version]
        else:
            raise ValueError(f"version {to_version} not found")

        if from_version is None:
            target_idx = kept.index(target)
            base = kept[target_idx - 1] if target_idx > 0 else kept[0]
        elif from_version in by_ver:
            base = by_ver[from_version]
        else:
            raise ValueError(f"version {from_version} not found")

        if base.version == target.version:
            raise ValueError("from_version and to_version must differ")
        if base.version > target.version:
            base, target = target, base
        diff_out = compute_version_diff(base, target)

    return {
        "content_id": content_id,
        "latest_version": latest_version,
        "revision_count": revision_count,
        "has_source_revision": has_source_revision,
        "versions": versions_out,
        "diff": diff_out,
    }


async def load_version_inputs(
    session: AsyncSession, content_ids: Sequence[int]
) -> dict[int, list[VersionInput]]:
    """仅投影计算 Diff 所需的列，通过 JSON 路径跳过 normalized_json 里重复存储的正文。"""
    unique_ids = list(dict.fromkeys(content_ids))
    if not unique_ids:
        return {}
    rows = await session.execute(
        select(
            ContentVersion.raw_content_id,
            ContentVersion.version,
            ContentVersion.parsed_at,
            ContentVersion.parser_version,
            ContentVersion.title,
            ContentVersion.text,
            ContentVersion.normalized_json["media"].label("media_json"),
            ContentVersion.normalized_json["extra"]["truncated"].label("truncated_json"),
        )
        .where(ContentVersion.raw_content_id.in_(unique_ids))
        .order_by(ContentVersion.raw_content_id.asc(), ContentVersion.version.asc())
    )
    grouped: dict[int, list[VersionInput]] = defaultdict(list)
    for raw_id, version, parsed_at, parser_version, title, text, media_json, truncated_json in rows.all():
        decoded_media = _decode_json_field(media_json)
        decoded_trunc = _decode_json_field(truncated_json)
        grouped[raw_id].append(
            VersionInput(
                version=version,
                parsed_at=parsed_at,
                parser_version=parser_version,
                title=title,
                text=text,
                media=decoded_media if isinstance(decoded_media, list) else [],
                truncated=bool(decoded_trunc) if not isinstance(decoded_trunc, str) else decoded_trunc.lower() == "true",
            )
        )
    return grouped


async def load_revision_summaries(
    session: AsyncSession,
    content_ids: Sequence[int],
    *,
    platforms: Mapping[int, str] | None = None,
) -> dict[int, RevisionSummary]:
    """批量加载多版本内容的实质修订摘要（调用方仅传入 latest_version > 1 的 ID 即可零开销）。"""
    grouped = await load_version_inputs(session, content_ids)
    platform_map = platforms or {}
    return {
        cid: summarize_versions(vlist, platform=platform_map.get(cid))
        for cid, vlist in grouped.items()
    }
