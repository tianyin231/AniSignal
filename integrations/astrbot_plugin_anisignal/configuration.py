"""AstrBot 设置页的分组结构与 AniSignal 会话配置之间的转换。"""

import copy
import json
from functools import cache
from pathlib import Path


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
