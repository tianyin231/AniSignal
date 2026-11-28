"""官方公告实质修订（抓暗改）的统一检测与文案渲染，供邮件、Webhook、Bot 推送与查询共用。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from app.clock import format_beijing
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.communities import get_community
from app.config import get_settings
from app.models import ContentVersion, RawContent, Source
from app.services.content_diff import (
    collapse_substantive_versions,
    compute_version_diff,
    load_version_inputs,
)
from app.services.email_templates import document
from app.services.notifications import _plain, _safe_url


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip("，、：； ") + "…"


def diff_change_summary(diff: dict, *, max_lines: int = 4) -> tuple[str, list[str]]:
    """把结构化 Diff 提炼为单行变更标签 + 最多若干条关键增删摘录。"""
    stats = diff.get("stats") or {}
    parts: list[str] = []
    if stats.get("title_changed"):
        parts.append("标题变更")
    added = int(stats.get("added_lines") or 0)
    removed = int(stats.get("removed_lines") or 0)
    if added or removed:
        if added and removed:
            parts.append(f"正文 +{added}/-{removed} 行")
        elif added:
            parts.append(f"正文新增 {added} 行")
        else:
            parts.append(f"正文删减 {removed} 行")
    m_add = int(stats.get("media_added") or 0)
    m_rem = int(stats.get("media_removed") or 0)
    if m_add or m_rem:
        if m_add and m_rem:
            parts.append(f"图片 +{m_add}/-{m_rem}")
        elif m_add:
            parts.append(f"新增 {m_add} 张图")
        else:
            parts.append(f"移除 {m_rem} 张图")
    label = " · ".join(parts) or "原文修订"

    snippets: list[str] = []
    if stats.get("title_changed") and diff.get("title_segments"):
        old_t = "".join(seg["text"] for seg in diff["title_segments"] if seg["op"] != "insert")
        new_t = "".join(seg["text"] for seg in diff["title_segments"] if seg["op"] != "delete")
        if old_t and new_t:
            snippets.append(f"原标题：{_clip(old_t, 70)}")
            snippets.append(f"新标题：{_clip(new_t, 70)}")

    for hunk in diff.get("hunks") or []:
        for line in hunk.get("lines") or []:
            op = line.get("op")
            if op not in {"delete", "insert"}:
                continue
            text = _clip(line.get("text") or "", 80)
            if not text:
                continue
            prefix = "旧" if op == "delete" else "新"
            snippets.append(f"{prefix}：{text}")
            if len(snippets) >= max_lines:
                break
        if len(snippets) >= max_lines:
            break

    return label, snippets[:max_lines]


def _matches_exclude(item: dict, exclude_keywords: Sequence[str] | None) -> bool:
    if not exclude_keywords:
        return False
    haystack = " ".join([
        item.get("title") or "",
        item.get("source") or "",
        " ".join(item.get("change_lines") or []),
    ]).casefold()
    return any(word.casefold() in haystack for word in exclude_keywords if word)


async def load_official_revision_events(
    session: AsyncSession,
    *,
    after_version_id: int | None = None,
    since_parsed_at: datetime | None = None,
    communities: Iterable[str] | None = None,
    exclude_keywords: Sequence[str] | None = None,
    limit: int = 100,
) -> tuple[list[dict], int]:
    """扫描新增的官方源站实质修订（version > 1），复用降噪 Diff 引擎过滤风控/反爬/重算。

    返回 (revision_events, max_scanned_version_id)。
    """
    conditions = [
        ContentVersion.version > 1,
        RawContent.hidden.is_(False),
        Source.identity == "official",
    ]
    if after_version_id is not None:
        conditions.append(ContentVersion.id > after_version_id)
    if since_parsed_at is not None:
        conditions.append(ContentVersion.parsed_at > since_parsed_at)
    community_list = list(communities) if communities is not None else None
    if community_list is not None:
        if not community_list:
            return [], after_version_id or 0
        conditions.append(RawContent.community.in_(community_list))

    order_col = ContentVersion.id.asc() if after_version_id is not None else ContentVersion.parsed_at.desc()
    stmt = (
        select(ContentVersion.id, ContentVersion.version, ContentVersion.parsed_at, RawContent, Source.name)
        .join(RawContent, RawContent.id == ContentVersion.raw_content_id)
        .join(Source, Source.id == RawContent.source_id)
        .where(*conditions)
        .order_by(order_col, ContentVersion.id.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).all())
    if not rows:
        return [], after_version_id or 0

    max_scanned_id = max(row[0] for row in rows)
    # 同一条内容在同批出现多个新版本时，只按该批中最新的一版比对，避免连发重复提醒。
    latest_by_content: dict[int, tuple[int, int, datetime, RawContent, str | None]] = {}
    for ver_id, ver_no, parsed_at, raw, source_name in rows:
        prev = latest_by_content.get(raw.id)
        if prev is None or ver_no > prev[1]:
            latest_by_content[raw.id] = (ver_id, ver_no, parsed_at, raw, source_name)

    grouped = await load_version_inputs(session, list(latest_by_content))
    base = get_settings().public_base_url.rstrip("/")
    events: list[dict] = []

    for content_id, (ver_id, ver_no, _parsed_at, raw, source_name) in latest_by_content.items():
        vlist = grouped.get(content_id) or []
        kept = collapse_substantive_versions(vlist, platform=raw.platform)
        if len(kept) < 2:
            continue
        target_ver = kept[-1]
        # 只有当前新入库的版本本身就是有效源站修订才推送，避免风控退化版或历史旧版被误推。
        if target_ver.origin != "source" or target_ver.version != ver_no:
            continue
        base_ver = kept[-2]
        diff = compute_version_diff(base_ver, target_ver)
        stats = diff.get("stats") or {}
        if not (
            stats.get("title_changed")
            or stats.get("added_lines")
            or stats.get("removed_lines")
            or stats.get("media_added")
            or stats.get("media_removed")
        ):
            continue
        change_label, change_lines = diff_change_summary(diff)
        meta = get_community(raw.community)
        item = {
            "content_id": raw.id,
            "id": raw.id,
            "version_id": ver_id,
            "from_version": base_ver.version,
            "to_version": target_ver.version,
            "parsed_at": target_ver.parsed_at,
            "published_at": raw.published_at,
            "community": raw.community,
            "community_label": meta.label,
            "platform": raw.platform,
            "content_type": raw.content_type,
            "source": source_name or raw.platform,
            "identity": "official",
            "title": target_ver.title or raw.external_id,
            "text": target_ver.text,
            "url": _safe_url(raw.url),
            "detail_url": f"{base}/contents/{raw.id}#revisions",
            "change_label": change_label,
            "change_lines": change_lines,
            "stats": stats,
            "media_added": diff.get("media_added") or [],
            "media_removed": diff.get("media_removed") or [],
            "image": (diff.get("media_added") or list(target_ver.media) or [None])[0],
        }
        if _matches_exclude(item, exclude_keywords):
            continue
        events.append(item)

    if after_version_id is not None:
        events.sort(key=lambda item: item["version_id"])
    else:
        events.sort(key=lambda item: item["parsed_at"], reverse=True)
    return events, max_scanned_id


def revision_webhook_payload(item: dict) -> dict:
    """构造供 Webhook（Bark/飞书/钉钉/企微/Discord/ntfy/通用 JSON）使用的修订负载。"""
    summary_lines = [f"版本 v{item['from_version']}→v{item['to_version']}（{item['change_label']}）"]
    summary_lines.extend(item.get("change_lines") or [])
    summary = "\n".join(summary_lines)
    return {
        "type": "content_revision",
        "event_id": item["content_id"],
        "content_id": item["content_id"],
        "from_version": item["from_version"],
        "to_version": item["to_version"],
        "change_label": item["change_label"],
        "change_lines": item.get("change_lines") or [],
        "title": f"官方公告修订 · {item['title']}",
        "summary": summary,
        "note": item["change_label"],
        "community": item["community"],
        "community_label": item["community_label"],
        "category": "official_revision",
        "keywords": [],
        "url": item["detail_url"],
        "sources": [
            {
                "id": item["content_id"],
                "title": item["title"],
                "source": item["source"],
                "url": item.get("url"),
            }
        ],
    }


def render_revision_email(item: dict) -> tuple[str, str, str]:
    """渲染单条官方公告修订即时提醒邮件的 (subject, text_body, html_body)。"""
    title = _plain(item["title"])
    source = _plain(item["source"])
    community_label = _plain(item["community_label"])
    change_label = _plain(item["change_label"])
    parsed_bj = format_beijing(item["parsed_at"])
    detail_url = item["detail_url"]
    origin_url = item.get("url")
    subject = f"AniSignal 官方修订 · [{community_label}] {title}"[:255]

    text_lines = [
        f"[{community_label}] {title}",
        f"来源：{source} · 修订时间：{parsed_bj}（北京时间）",
        f"变更概览：v{item['from_version']}→v{item['to_version']}（{change_label}）",
    ]
    if item.get("change_lines"):
        text_lines.append("")
        text_lines.extend(f"• {line}" for line in item["change_lines"])
    text_lines.extend(["", f"查看完整对比：{detail_url}"])
    if origin_url:
        text_lines.append(f"查看源站原文：{origin_url}")
    text_lines.append("可在账号中心关闭官方公告修订提醒或调整分区偏好。")

    snippet_html = ""
    if item.get("change_lines"):
        snippet_html = "<ul>" + "".join(
            f"<li>{escape(line)}</li>" for line in item["change_lines"]
        ) + "</ul>"
    origin_link = (
        f' · <a href="{escape(origin_url, quote=True)}">查看原文</a>'
        if origin_url
        else ""
    )
    html = document(
        title,
        f"<p><strong>{escape(community_label)}</strong> · {escape(source)} · 修订于 {escape(parsed_bj)}（北京时间）</p>"
        f"<p>变更概览：v{item['from_version']}→v{item['to_version']}（{escape(change_label)}）</p>"
        f"{snippet_html}"
        f'<p><a href="{escape(detail_url, quote=True)}">查看完整版本对比</a>{origin_link}</p>'
        "<p>可在账号中心关闭官方公告修订提醒或调整分区偏好。</p>",
        eyebrow="官方修订",
    )
    return subject, "\n".join(text_lines), html


def format_bot_revision_message(item: dict) -> str:
    """单条官方公告修订的 Bot 推送短讯文案。"""
    parsed_bj = format_beijing(item["parsed_at"], "%m/%d %H:%M")
    lines = [
        f"[{item['community_label']}] 官方公告修订 · {_clip(item['title'], 60)}",
        f"v{item['from_version']}→v{item['to_version']} · {item['change_label']}",
    ]
    for snippet in (item.get("change_lines") or [])[:3]:
        lines.append(snippet)
    lines.append(f"来源：{item['source']} · 修订于 {parsed_bj}")
    lines.append(item["detail_url"])
    return "\n".join(lines)


def bot_revision_brief(items: list[dict]) -> dict:
    """为 Bot 修订推送/查询构造结构化 brief，兼容模板与上下文索引。"""
    return {
        "mode": "revisions",
        "title": "官方公告修订",
        "items": [
            {
                "id": item["content_id"],
                "content_ids": [item["content_id"]],
                "title": item["title"],
                "summary": f"v{item['from_version']}→v{item['to_version']} · {item['change_label']}"
                + (f"；{' / '.join(item['change_lines'][:2])}" if item.get("change_lines") else ""),
                "community": item["community"],
                "platform": item["platform"],
                "identity": "official",
                "source": item["source"],
                "url": item["detail_url"],
                "published_at": item["published_at"].isoformat() if item.get("published_at") else None,
                "revised_at": item["parsed_at"].isoformat(),
                "image": item.get("image"),
                "sources": [
                    {
                        "id": item["content_id"],
                        "content_ids": [item["content_id"]],
                        "title": item["title"],
                        "source": item["source"],
                        "community": item["community"],
                        "platform": item["platform"],
                        "identity": "official",
                        "url": item["detail_url"],
                        "text": "\n".join(item.get("change_lines") or []) or item["change_label"],
                        "published_at": item["published_at"].isoformat() if item.get("published_at") else None,
                        "image": item.get("image"),
                    }
                ],
            }
            for item in items
        ],
    }
