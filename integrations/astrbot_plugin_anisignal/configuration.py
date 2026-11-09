"""AstrBot 设置页的分组结构与 AniSignal 会话配置之间的转换。"""

import copy
import hashlib
import json
import re
from functools import cache
from pathlib import Path


UMO_PATTERN = re.compile(r"^[^:]+:(FriendMessage|GroupMessage):[^:]+$")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# 选项随账号目录变化，校验交给主节点；其余固定选项在本地先拦下。
CATALOG_FIELDS = {"channels", "custom_feeds", "custom_feed_ids", "daily_sources", "updates_sources"}


def cleanup_legacy_configuration(config):
    """退役配置只清理一次；人格和模型不再从站点配置导入。"""
    obsolete = [key for key in config if key == "legacy_cat_behavior" or key.startswith("cat_")]
    for key in obsolete:
        config.pop(key)
    changed = bool(obsolete)
    if str(config.get("base_url", "")).rstrip("/") in {
            "http://anisignal.xyz", "http://www.anisignal.xyz", "http://anisignal.nanamiyako.cn"}:
        config["base_url"] = "https://anisignal.xyz"
        changed = True
    for target in config.get("targets", []):
        notifications = target.get("notifications", {})
        if "hourly_limit" in notifications:
            notifications.pop("hourly_limit")
            changed = True
        interaction = target.get("interaction", {})
        for key in ("ai_prompt", "ai_provider_id"):
            if key in interaction:
                interaction.pop(key)
                changed = True
    return changed


@cache
def plugin_schema():
    return json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8"))


def target_schema():
    return plugin_schema()["targets"]["templates"]["session"]["items"]


def live_schema(config):
    """AstrBot 载入的 schema 带有账号目录选项；测试或旧版本缺失时退回文件。"""
    return getattr(config, "schema", None) or plugin_schema()


def configuration_revision(config):
    """设置视图用它发现别处的并发修改；不纳入密钥，避免把密钥派生值交给页面。"""
    snapshot = {key: config.get(key) for key in ("base_url", "card_font_path", "targets")}
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _coerce_field(value, meta, label, errors, field):
    kind, default = meta.get("type"), copy.deepcopy(meta.get("default"))
    if value is None:
        return default
    if kind == "bool":
        if isinstance(value, bool):
            return value
        errors.append(f"{label}应为开关")
        return default
    if kind == "int":
        try:
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
            number = int(value)
        except (TypeError, ValueError):
            errors.append(f"{label}应为整数")
            return default
        slider = meta.get("slider") or {}
        low, high = slider.get("min", 0), slider.get("max")
        if number < low or (high is not None and number > high):
            errors.append(f"{label}超出范围" + (f" {low}～{high}" if high is not None else f"，不能小于 {low}"))
        return number
    if kind in ("string", "text"):
        if not isinstance(value, str):
            errors.append(f"{label}应为文字")
            return default
        value = value.strip() if kind == "string" else value
        if meta.get("options") and value not in meta["options"]:
            errors.append(f"{label}不是可选值：{value}")
            return default
        return value
    if kind == "list":
        if not isinstance(value, list):
            errors.append(f"{label}应为列表")
            return default
        items = []
        for item in value:
            if field == "custom_feed_ids":
                try:
                    item = int(item)
                except (TypeError, ValueError):
                    errors.append(f"{label}包含无效监控")
                    continue
            elif isinstance(item, str):
                item = item.strip()
                if not item:
                    continue
            else:
                errors.append(f"{label}包含无效项")
                continue
            if item not in items:
                items.append(item)
        options = meta.get("options")
        if options and field not in CATALOG_FIELDS and not field.startswith("channel_"):
            if unknown := [item for item in items if item not in options]:
                errors.append(f"{label}包含未知选项：{'、'.join(map(str, unknown[:3]))}")
                items = [item for item in items if item in options]
        return items
    return copy.deepcopy(value)


def normalize_view_target(raw, items, index):
    """把设置视图提交的会话收敛成模板列表条目，字段与默认值以 schema 为准。"""
    errors = []
    raw = raw if isinstance(raw, dict) else {}
    label = f"第 {index + 1} 个会话"
    result = {"__template_key": "session"}
    for field, meta in items.items():
        if meta.get("type") == "object":
            group = raw.get(field) if isinstance(raw.get(field), dict) else {}
            result[field] = {key: _coerce_field(group.get(key), item, f"{label}·{item.get('description', key)}", errors, key)
                             for key, item in meta["items"].items()}
        else:
            result[field] = _coerce_field(raw.get(field), meta, f"{label}·{meta.get('description', field)}", errors, field)
    name = result.get("name", "")
    if name:
        label = f"「{name}」"
    if not name:
        errors.append(f"{label}缺少名称")
    elif len(name) > 100:
        errors.append(f"{label}的名称超过 100 字")
    if not UMO_PATTERN.match(result.get("umo", "")):
        errors.append(f"{label}的会话标识应形如 平台:GroupMessage:群号 或 平台:FriendMessage:用户")
    notifications = result.get("notifications", {})
    for key, title in (("daily_time", "早报时间"), ("calendar_time", "收尾提醒时间")):
        if key in notifications and not TIME_PATTERN.match(notifications[key]):
            errors.append(f"{label}的{title}应为 HH:mm")
    subscription = result.get("subscription", {})
    for key, title in (("keywords", "关注关键词"), ("exclude_keywords", "排除关键词")):
        words = subscription.get(key, [])
        if len(words) > 20:
            errors.append(f"{label}的{title}最多 20 个")
        if any(len(word) > 64 for word in words):
            errors.append(f"{label}的{title}每个不超过 64 字")
    if len(result.get("interaction", {}).get("manager_ids", [])) > 20:
        errors.append(f"{label}的管理用户最多 20 个")
    return result, errors


def normalize_view_configuration(payload, config):
    """校验设置视图的整份提交；密钥留空表示不修改。返回待写入的字段与错误。"""
    schema = live_schema(config)
    items = schema["targets"]["templates"]["session"]["items"]
    payload = payload if isinstance(payload, dict) else {}
    errors, updates = [], {}
    base_url = payload.get("base_url", config.get("base_url", ""))
    if not isinstance(base_url, str) or not re.match(r"^https?://[^\s/]+", base_url.strip()):
        errors.append("服务地址应以 http:// 或 https:// 开头")
    else:
        updates["base_url"] = base_url.strip().rstrip("/")
    tools_key = payload.get("tools_key")
    if payload.get("clear_tools_key"):
        updates["tools_key"] = ""
    elif tools_key is not None:
        if not isinstance(tools_key, str):
            errors.append("用户 Token 应为文字")
        elif tools_key.strip():
            updates["tools_key"] = tools_key.strip()
    font = payload.get("card_font_path", config.get("card_font_path", ""))
    if not isinstance(font, str):
        errors.append("字体路径应为文字")
    else:
        updates["card_font_path"] = font.strip()
    targets = payload.get("targets", [])
    if not isinstance(targets, list):
        errors.append("会话列表格式无效")
        targets = []
    normalized, seen = [], set()
    for index, raw in enumerate(targets):
        target, target_errors = normalize_view_target(raw, items, index)
        errors.extend(target_errors)
        if target.get("umo") in seen:
            errors.append(f"会话标识重复：{target['umo']}")
        seen.add(target.get("umo"))
        normalized.append(target)
    updates["targets"] = normalized
    return updates, errors


def upgrade_subscriptions(config):
    changed = False
    for target in config.get("targets", []):
        subscription = target.setdefault("subscription", {})
        if "scope_mode" not in subscription:
            subscription["scope_mode"] = "channels" if subscription.get("channels") else "legacy"
            subscription.setdefault("inherit_preferences", False)
            subscription.setdefault("inherit_keywords", False)
            for field in target_schema()["subscription"]["items"]:
                if field.startswith("channel_"):
                    community = field.removeprefix("channel_")
                    subscription[field] = [key for key in subscription.get("channels", []) if key.startswith(community + ":")]
            changed = True
    return changed


def quota_summary(identity):
    quota = identity.get("quota")
    if not quota:
        return ""
    return (f"自建监控 {quota['custom_feeds_used']}/{quota['max_feeds']}，采集间隔至少 {quota['min_interval_minutes']} 分钟；"
            f"Bot 接入 {quota['bot_clients_used']}/{quota['max_bot_clients']}，启用会话 {quota['bot_targets_used']}/{quota['max_bot_targets']}；"
            f"今日通知 {quota['notifications_used']}/{quota['daily_notifications']} 次。"
            "通知额度由邮件与 Bot 共用，失败的发送尝试也计入，北京时间零点重置。")


def update_catalog(config, catalog, identity, *, synced_at="", sync_error=""):
    """AstrBot 直接从 config.schema 读取面板，鉴权后刷新本人可用选项。"""
    schema = getattr(config, "schema", None)
    if not schema:
        return
    items = schema["targets"]["templates"]["session"]["items"]
    options = catalog.get("channels", {})
    def label(value):
        return value.replace("_", " · ").replace(" · YB", " · YouTube").replace(" · B", " · B站")
    for field, metadata in items["subscription"]["items"].items():
        if field.startswith("channel_"):
            community = field.removeprefix("channel_")
            group = {key: label(value).split(" · ", 1)[-1] for key, value in options.items() if key.startswith(community + ":")}
            metadata.update(options=list(group), labels=list(group.values()))
    feeds = catalog.get("custom_feeds", [])
    feed_options = {row["id"]: f"{row['name']} · {'我的监控' if row['owned'] else '公共监控'}" for row in feeds}
    items["subscription"]["items"]["custom_feed_ids"].update(options=list(feed_options), labels=list(feed_options.values()))
    presets = catalog.get("custom_presets", {})
    # 现有站点预设已并入对应游戏分区的官方来源；没有预设时隐藏这个旧字段，免得被当成缺少权限。
    items["subscription"]["items"]["custom_feeds"].update(options=list(presets), labels=list(presets.values()),
                                                         invisible=not presets)
    sources = {**options, **{f"preset:{key}": name for key, name in catalog.get("custom_presets", {}).items()},
               **{f"feed:{key}": value for key, value in feed_options.items()}}
    for field in ("daily_sources", "updates_sources"):
        items["notifications"]["items"][field].update(options=list(sources), labels=[label(value) for value in sources.values()])
    owner = identity.get("user")
    schema["tools_key"]["hint"] = (f"已连接 {owner['display_name']}（#{owner['id']}）。个人监控选项已按该账号权限加载。" if owner else
                                   "在 AniSignal 账号中心创建用户 Token，填入并保存后重新打开设置，加载账号可用来源。")
    quota = identity.get("quota")
    schema["tools_key"]["description"] = "AniSignal 用户 Token" + (
        f" · 今日通知 {quota['notifications_used']}/{quota['daily_notifications']}" if quota else "")
    if summary := quota_summary(identity):
        schema["tools_key"]["hint"] += "\n" + summary
    if sync_error:
        state = "同步失败"
        message = f"未确认本次配置同步成功：{sync_error}。请修正后保存，再重新打开设置查看状态。"
    elif identity:
        state = "已同步"
        message = f"上次同步成功：{synced_at}（北京时间）。" if synced_at else "已连接主节点。"
        message += "保存配置后即发起同步，后台每 30 秒校验一次。"
    else:
        state, message = "待连接", "填入用户 Token 并保存后，自动加载额度并同步会话配置。"
    schema["targets"]["description"] = f"会话与通知定制 · {state}"
    schema["targets"]["hint"] = message + ("\n" + quota_summary(identity) if quota else "")


def to_plugin_target(target):
    result = {"__template_key": "session"}
    for field, metadata in target_schema().items():
        if metadata["type"] == "object":
            result[field] = {key: copy.deepcopy(target.get(key, item["default"]))
                             for key, item in metadata["items"].items()}
        else:
            result[field] = copy.deepcopy(target.get(field, metadata["default"]))
    if result["subscription"]["communities"] is None:
        community = target.get("community", "all")
        result["subscription"]["communities"] = (
            list(target_schema()["subscription"]["items"]["communities"]["options"])
            if community == "all" else [community]
        )
    if "scope_mode" not in target:
        result["subscription"].pop("scope_mode", None)
        result["subscription"].pop("inherit_preferences", None)
        result["subscription"].pop("inherit_keywords", None)
    # 从旧站点配置导入也维持旧范围，不使用新会话的默认勾选模式。
    for field in target_schema()["subscription"]["items"]:
        if field.startswith("channel_"):
            community = field.removeprefix("channel_")
            result["subscription"][field] = [key for key in target.get("channels", []) if key.startswith(community + ":")]
    upgrade_subscriptions({"targets": [result]})
    return result


def to_server_target(target):
    result = {}
    for field, metadata in target_schema().items():
        if field == "presentation":
            continue  # 只影响 AstrBot 的呈现，不传给服务端调度配置。
        if metadata["type"] == "object":
            result.update({key: copy.deepcopy(target.get(field, {}).get(key, item["default"]))
                           for key, item in metadata["items"].items()})
        else:
            result[field] = copy.deepcopy(target.get(field, metadata["default"]))
    result["delivery_mode"] = "astrbot"
    subscription = target.get("subscription", {})
    groups = [field for field in subscription if field.startswith("channel_")]
    if groups:
        result["channels"] = list(dict.fromkeys(key for field in groups for key in subscription[field]))
    if result.get("scope_mode") in {"legacy", "custom"}:
        result["channels"] = []
    for field in list(result):
        if field.startswith("channel_"):
            result.pop(field)
    return result


def apply_subscription(targets, umo, response):
    """聊天修改成功后保存回插件设置，下次同步或重启不会把它覆盖。"""
    for target in targets:
        if target.get("umo") != umo:
            continue
        for field in ("communities", "keywords", "exclude_keywords", "channels", "scope_mode"):
            if field in response:
                target["subscription"][field] = copy.deepcopy(response[field])
        if "channels" in response:
            for field in target_schema()["subscription"]["items"]:
                if field.startswith("channel_"):
                    target["subscription"][field] = [key for key in response["channels"] if key.startswith(field.removeprefix("channel_") + ":")]
        for field in ("daily_enabled", "daily_time", "updates_enabled"):
            target["notifications"][field] = response[field]
        break
