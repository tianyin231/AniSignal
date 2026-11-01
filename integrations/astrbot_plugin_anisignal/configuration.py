"""AstrBot 设置页的分组结构与 AniSignal 会话配置之间的转换。"""

import copy
import json
from functools import cache
from pathlib import Path


def cleanup_legacy_configuration(config):
    """退役配置只清理一次；人格和模型不再从站点配置导入。"""
    obsolete = [key for key in config if key == "legacy_cat_behavior" or key.startswith("cat_")]
    for key in obsolete:
        config.pop(key)
    changed = bool(obsolete)
    for target in config.get("targets", []):
        interaction = target.get("interaction", {})
        for key in ("ai_prompt", "ai_provider_id"):
            if key in interaction:
                interaction.pop(key)
                changed = True
    return changed


@cache
def target_schema():
    schema = json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8"))
    return schema["targets"]["templates"]["session"]["items"]


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


def update_catalog(config, catalog, identity):
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
    items["subscription"]["items"]["custom_feeds"].update(options=list(presets), labels=list(presets.values()))
    sources = {**options, **{f"preset:{key}": name for key, name in catalog.get("custom_presets", {}).items()},
               **{f"feed:{key}": value for key, value in feed_options.items()}}
    for field in ("daily_sources", "updates_sources"):
        items["notifications"]["items"][field].update(options=list(sources), labels=[label(value) for value in sources.values()])
    owner = identity.get("user")
    schema["tools_key"]["hint"] = (f"已连接 {owner['display_name']}（#{owner['id']}）。个人监控选项已按该账号权限加载。" if owner else
                                   "在 AniSignal 账号中心创建用户 Token，填入并保存后重新打开设置，加载账号可用来源。")


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
