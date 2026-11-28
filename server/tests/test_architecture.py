"""架构与单一真相源（SSOT）自动化护栏测试。

通过 AST 静态检查与跨层/跨端注册核对，把关键工程约束转化为 CI 可执行的防回归防线：
1. 严格单向分层：禁止 services -> api、parsers -> collectors、models/schemas 反向依赖上层；
2. 循环依赖棘轮：禁止新增任何两节点模块循环依赖（存量历史项显式登记，只减不增）；
3. 平台与分区注册一致性：后端枚举、采集器注册表与前端 lexicon 保持同步；
4. 下沉公共原语（ids / html / clock）行为确定性回归。
"""

from __future__ import annotations

import ast
import collections
from datetime import datetime, timezone
from pathlib import Path
import re

from app.clock import BJ_TZ, feed_time, to_beijing
from app.communities import COMMUNITIES
from app.models.enums import Community, Platform
from app.schemas.ids import git_item_id, rss_id, web_id
from app.services.collect import _COLLECTORS
from app.services.html import sanitize_html

SERVER_APP_DIR = Path(__file__).resolve().parents[1] / "app"
REPO_ROOT = Path(__file__).resolve().parents[2]

# 历史存量两节点循环依赖（棘轮清单：只允许减少，不允许新增；清理任意一对后必须同步移除此处）
KNOWN_LEGACY_CYCLES: frozenset[tuple[str, str]] = frozenset({
    ("app.services.bot", "app.services.bot_policy"),
    ("app.services.collect", "app.services.queue"),
    ("app.services.events", "app.services.fulfillment"),
    ("app.services.extract", "app.services.redeem_codes"),
})


def _module_name(path: Path) -> str:
    rel = path.relative_to(SERVER_APP_DIR).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return "app" + ("." + ".".join(parts) if parts else "")


def _collect_import_graph() -> dict[str, set[str]]:
    """扫描 server/app 全部模块依赖图（含函数内延迟导入）。"""
    edges: dict[str, set[str]] = collections.defaultdict(set)
    for py_file in sorted(SERVER_APP_DIR.rglob("*.py")):
        src = py_file.read_text(encoding="utf-8")
        mod = _module_name(py_file)
        tree = ast.parse(src, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app.") and alias.name != mod:
                        edges[mod].add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("app.") and node.module != mod:
                    edges[mod].add(node.module)
    return edges


def _layer(mod: str) -> str:
    parts = mod.split(".")
    return parts[1] if len(parts) > 1 else "root"


def test_strict_layer_boundaries() -> None:
    """严禁跨层反向导入：保证 api -> services -> collectors/parsers -> models/core schemas/clock 单向依赖。"""
    edges = _collect_import_graph()
    forbidden_rules: dict[str, set[str]] = {
        "services": {"api"},
        "models": {"api", "services", "collectors", "parsers"},
        "parsers": {"api", "collectors"},
        "collectors": {"api"},
    }
    pure_leaf_schemas = {"app.schemas.content", "app.schemas.ids"}
    violations: list[str] = []
    for src_mod, targets in sorted(edges.items()):
        src_layer = _layer(src_mod)
        if src_mod == "app.clock" and targets:
            violations.append(f"app.clock must stay a pure leaf without app imports: {sorted(targets)}")
            continue
        if src_mod in pure_leaf_schemas:
            disallowed = {"api", "services", "collectors", "parsers"}
        elif src_layer == "schemas":
            # app.schemas.api 属于接口入参校验层，可调用各领域 normalizer，但禁止反依赖路由模块
            disallowed = {"api"}
        else:
            disallowed = forbidden_rules.get(src_layer, set())
        if not disallowed:
            continue
        for dst_mod in sorted(targets):
            if _layer(dst_mod) in disallowed:
                violations.append(f"{src_mod} -> {dst_mod}")
    assert not violations, "检测到跨层反向依赖：\n" + "\n".join(violations)


def test_two_node_import_cycles_ratchet() -> None:
    """循环依赖棘轮：禁止新增两节点循环依赖，历史项消除后需及时从白名单移除。"""
    edges = _collect_import_graph()
    actual_cycles: set[tuple[str, str]] = set()
    for src_mod, targets in edges.items():
        for dst_mod in targets:
            if src_mod in edges.get(dst_mod, ()):
                pair = tuple(sorted((src_mod, dst_mod)))
                actual_cycles.add((pair[0], pair[1]))

    unexpected = sorted(actual_cycles - KNOWN_LEGACY_CYCLES)
    assert not unexpected, f"检测到新增模块循环依赖：{unexpected}"

    resolved = sorted(KNOWN_LEGACY_CYCLES - actual_cycles)
    assert not resolved, f"以下历史循环依赖已消除，请从 KNOWN_LEGACY_CYCLES 移除：{resolved}"


def test_platform_registration_single_source_of_truth() -> None:
    """后端 Platform 枚举、采集器注册表与前端 lexicon.ts 必须保持同步。"""
    enum_platforms = {p.value for p in Platform}
    collector_platforms = set(_COLLECTORS.keys())
    assert enum_platforms == collector_platforms, (
        f"Platform 枚举与 _COLLECTORS 不一致: "
        f"only_enum={sorted(enum_platforms - collector_platforms)}, "
        f"only_collectors={sorted(collector_platforms - enum_platforms)}"
    )

    lexicon_path = REPO_ROOT / "web" / "app" / "utils" / "lexicon.ts"
    if lexicon_path.exists():
        text = lexicon_path.read_text(encoding="utf-8")
        meta_match = re.search(r"export const PLATFORM_META:[^=]+=\s*\{(.*?)\n\}", text, re.S)
        assert meta_match is not None, "未能解析 lexicon.ts 中的 PLATFORM_META"
        lexicon_keys = set(re.findall(r"^\s*([a-z0-9_]+):\s*\{", meta_match.group(1), re.M))
        missing_in_web = sorted(enum_platforms - lexicon_keys)
        assert not missing_in_web, f"以下平台缺少前端 lexicon.ts PLATFORM_META 定义：{missing_in_web}"

        order_match = re.search(r"const ADMIN_PLATFORM_ORDER:[^=]+=\s*\[(.*?)\]", text, re.S)
        assert order_match is not None, "未能解析 lexicon.ts 中的 ADMIN_PLATFORM_ORDER"
        admin_order_keys = set(re.findall(r"'([a-z0-9_]+)'", order_match.group(1)))
        assert admin_order_keys == enum_platforms, (
            f"前端 ADMIN_PLATFORM_ORDER 与后端 Platform 枚举不一致: "
            f"only_backend={sorted(enum_platforms - admin_order_keys)}, "
            f"only_frontend={sorted(admin_order_keys - enum_platforms)}"
        )


def test_community_registration_single_source_of_truth() -> None:
    """内置分区枚举、communities 注册表与前端兜底列表保持一致。"""
    enum_keys = {c.value for c in Community}
    assert enum_keys == set(COMMUNITIES.keys())

    composable_path = REPO_ROOT / "web" / "app" / "composables" / "useGameSection.ts"
    if composable_path.exists():
        text = composable_path.read_text(encoding="utf-8")
        block = re.search(r"export const GAME_COMMUNITIES:[^=]+=\s*\[(.*?)\n\]", text, re.S)
        assert block is not None, "未能解析 useGameSection.ts 中的 GAME_COMMUNITIES"
        web_keys = set(re.findall(r"key:\s*'([a-z0-9_]+)'", block.group(1)))
        assert web_keys == enum_keys, (
            f"前端 GAME_COMMUNITIES 与后端 Community 枚举不一致: "
            f"only_backend={sorted(enum_keys - web_keys)}, "
            f"only_frontend={sorted(web_keys - enum_keys)}"
        )


def test_shared_primitives_contracts() -> None:
    """验证下沉后的公共原语（ids / html / clock）保持确定性契约。"""
    assert len(rss_id("https://example.com/feed.xml")) == 48
    assert len(web_id("https://example.com/changelog")) == 48
    assert git_item_id("Acme/Demo", "release", "v1.0.0") == "acme/demo:release:v1.0.0"
    long_id = git_item_id("very-long-org-name/very-long-repository-name", "release", "v2026.11.28-alpha.1234567890")
    assert len(long_id) <= 64 and long_id.startswith("release:")

    assert sanitize_html("plain text") is None
    cleaned = sanitize_html('<p>hello</p><script>alert(1)</script><img src="https://img.example/a.png">')
    assert cleaned is not None
    assert "<script" not in cleaned
    assert 'referrerpolicy="no-referrer" loading="lazy"' in cleaned

    parsed_iso = feed_time("2026-11-28T06:00:00Z")
    assert parsed_iso == datetime(2026, 11, 28, 6, 0, tzinfo=timezone.utc)
    bj = to_beijing(datetime(2026, 11, 28, 6, 0))
    assert bj.tzinfo == BJ_TZ and bj.hour == 14
