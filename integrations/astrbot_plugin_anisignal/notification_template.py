"""把已保存的通知快照渲染成文字和图片组件，不执行模板中的代码。"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


TOKEN = re.compile(r"\{([a-z_]+)\}")
FIELDS = {"title", "summary", "content", "original", "time", "source", "url", "images", "image_urls", "items", "index", "schedule", "details_hint"}
ITEM_TEMPLATE = "{index}. {title}\n{summary}\n发布时间：{time}\n来源：{source}\n{url}\n{images}"
KINDS = {"daily": "每日早报", "updates": "重要动态", "critical": "关键动态", "calendar": "活动提醒", "reminder": "定时提醒", "manual": "情报通知"}


@dataclass(frozen=True)
class NotificationImage:
    url: str


@dataclass(frozen=True)
class NotificationMessage:
    parts: tuple[str | NotificationImage, ...]
    customized: bool = False

    @property
    def text(self):
        """回执与会话历史保留图片引用；实际发送使用图片组件。"""
        return "".join(part if isinstance(part, str) else f"[图片] {part.url}" for part in self.parts).strip()


def _published(value):
    if not value:
        return ""
    try:
        date = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return ""
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


def _unique(values):
    return list(dict.fromkeys(value for value in values if value))


def details_hint(brief, enabled=False):
    """交互提示属于插件呈现；默认关闭，完整短清单不提示展开。"""
    items = (brief or {}).get("items") or []
    if not enabled or not items:
        return ""
    if brief.get("mode") == "calendar":
        omitted = brief.get("truncated", False)
    else:
        omitted = any(len(item.get("sources", [])) > 1 or any(
            source.get("text", "").strip() and source["text"].strip() not in
            (item.get("title", ""), item.get("summary", ""), item.get("share_text", ""))
            for source in item.get("sources", [])) for item in items)
    if not omitted:
        return ""
    number = min(2, len(items))
    return f"查看详情：/展开 {number}" + ("（数字可换）" if len(items) > 1 else "")


def _schedule(entries):
    lines = []
    for entry in entries:
        action = "维护开始" if entry.get("kind") == "maintenance" else "截止"
        lines.append(f"{entry.get('title', '')}\n{action}：{_published(entry.get('stage_at'))}"
                     + ("\n" + entry["url"] if entry.get("url") else ""))
    return "\n\n".join(lines)


def _item_fields(item, index, images):
    sources = item.get("sources") or [item]
    content = "\n\n".join(_unique(source.get("text", "") for source in sources))
    own_images = _unique(source.get("image") for source in [item, *sources])
    return {
        "index": str(index), "title": item.get("title", ""),
        "summary": item.get("summary") or item.get("share_text") or "",
        "content": content, "original": content,
        "time": " / ".join(_unique(_published(source.get("published_at")) for source in sources)),
        "source": " / ".join(_unique(source.get("source") or source.get("platform", "") for source in sources)),
        "url": "\n".join(_unique(source.get("url", "") for source in sources)),
        "images": [image for image in own_images if image in images],
        "image_urls": "\n".join(image for image in own_images if image in images),
        "schedule": _schedule(item.get("calendar_entries", [])),
    }


def render_notification(row, options, intro=""):
    template = options.get("notification_template", "{intro}\n\n{body}")
    customized = "{body}" not in template or any(match[1] in FIELDS for match in TOKEN.finditer(template))
    images = _unique(row.get("images") or [])[:max(1, min(9, int(row.get("max_images", 1))))]
    brief = row.get("brief") or {}
    hint = details_hint(brief, options.get("show_details_hint", False))
    # 卡片的提示直接绘在卡内；文字模式只在显式开启后追加。
    if not customized and hint and not (brief.get("mode") == "daily" and options.get("notification_format", "card") == "card"):
        template += "\n\n{details_hint}"
    items = brief.get("items") or [{"title": brief.get("title") or KINDS.get(row.get("kind"), "情报通知"), "text": row["body"]}]
    fields = [_item_fields(item, index, images) for index, item in enumerate(items, 1)]
    if len(fields) == 1 and not fields[0]["images"]:
        fields[0]["images"] = images
        fields[0]["image_urls"] = "\n".join(images)
    values = {"intro": intro, "body": row["body"], "images": images, "image_urls": "\n".join(images), "details_hint": hint}
    for name in FIELDS - {"items", "images", "image_urls", "details_hint"}:
        values[name] = "\n\n".join((f"{index}. " if len(fields) > 1 and name != "index" else "") + item[name]
                                    for index, item in enumerate(fields, 1) if item[name])
    parts, emitted_images = [], set()

    def append(value):
        if not value:
            return
        if isinstance(value, str) and parts and isinstance(parts[-1], str):
            parts[-1] += value
        else:
            parts.append(value)

    def render(text, data, allow_items=False):
        position = 0
        for match in TOKEN.finditer(text):
            append(text[position:match.start()])
            name = match[1]
            if name == "images":
                for url in data.get("images", []):
                    if url not in emitted_images:
                        append(NotificationImage(url))
                        emitted_images.add(url)
            elif name == "items" and allow_items:
                for index, item in enumerate(fields):
                    if index:
                        append("\n\n")
                    render(options.get("notification_item_template", ITEM_TEMPLATE), item)
            else:
                # 只扫描模板一次，原文中的 {images} 等字样不会被当成指令。
                append(data.get(name, match[0]))
            position = match.end()
        append(text[position:])

    render(template, values, allow_items=True)
    if not any(isinstance(part, NotificationImage) or part.strip() for part in parts):
        parts = [row["body"]]
    return NotificationMessage(tuple(parts), customized)
