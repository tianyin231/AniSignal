"""AstrBot 设置页的分组结构与 AniSignal 会话配置之间的转换。"""

import copy
import json
from functools import cache
from pathlib import Path


def cleanup_legacy_configuration(config):
    """升级时仅移除已退役的猫系设置，连接凭据与会话订阅保留。"""
    obsolete = [key for key in config if key == "legacy_cat_behavior" or key.startswith("cat_")]
    for key in obsolete:
        config.pop(key)
    return bool(obsolete)


@cache
def target_schema():
    schema = json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8"))
    return schema["targets"]["templates"]["session"]["items"]


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
    return result


def apply_subscription(targets, umo, response):
    """聊天修改成功后保存回插件设置，下次同步或重启不会把它覆盖。"""
    for target in targets:
        if target.get("umo") != umo:
            continue
        for field in ("communities", "keywords"):
            target["subscription"][field] = copy.deepcopy(response[field])
        for field in ("daily_enabled", "daily_time", "updates_enabled"):
            target["notifications"][field] = response[field]
        break
