"""一游戏一配置：pipeline 里所有"这个 wiki 长什么样"的旋钮都集中到 kb/games/*.yaml。

为什么抽出来：v1–v3 只炼碧蓝航线，wiki 地址、infobox 白名单、chunk 规则、
qdrant collection 名全写死在 kblib/config.py 和各脚本里。炼第二款游戏（明日方舟
PRTS wiki）时这些要整套换掉，但 fetch/parse/embed/eval 的代码一行都不该动——
换游戏 = 换一份 YAML。

约定：
  * `kb/games/<name>.yaml` 一份配置；脚本统一 `--game <name>`，默认 azurlane
    （老默认，行为与 v1–v3 完全一致）。
  * 落盘位置在 YAML 里显式写 `data_dir`。azurlane 沿用 v1 起的 `data/`
    （`blhx_kb` collection、`index_meta.json`、bm25 索引、eval 报告都在那儿，
    搬家等于作废 v2/v3 的向量库）；新游戏各自一个目录，互不污染。
  * 结构化路两套：`extractors/` 下的专用抽取器（azurlane 的舰娘/装备/关卡…），
    和 YAML 里 `kinds:` 声明的通用实体路（列名→字段名映射，新游戏零代码）。
    kind 同时出现在两边时专用抽取器优先。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import DATA_DIR, GAMES_DIR

# 默认游戏：不给 --game 时的行为与 v1–v3 一致
DEFAULT_GAME = os.environ.get("KB_GAME", "azurlane")


def _as_tuple_str(v: Any) -> tuple[str, ...]:
    if not v:
        return ()
    if isinstance(v, str):
        return (v,)
    return tuple(str(x) for x in v)


def _as_pairs(v: Any) -> dict[str, tuple[str, str]]:
    """{模板名: {kind: x, category: y}} → {模板名: (x, y)}（保序）。"""
    out: dict[str, tuple[str, str]] = {}
    for k, spec in (v or {}).items():
        if isinstance(spec, dict):
            out[str(k)] = (str(spec.get("kind", "other")), str(spec.get("category") or ""))
        else:  # 简写：模板名: kind（category 同 kind）
            out[str(k)] = (str(spec), str(spec))
    return out


def _as_frozensets(v: Any) -> dict[str, frozenset[str]]:
    return {str(k): frozenset(_as_tuple_str(x)) for k, x in (v or {}).items()}


def _as_display(v: Any) -> dict[str, tuple[str, ...]]:
    """展示型模板渲染表：{模板名: [参数名...]}。

    值给列表/元组 = 按顺序渲染这些参数；给标量 = 只渲染这一个；给 null = 渲染成空
    （图标类模板，`{{mdi|alpha-x-box-outline}}` 这种机器名一个字符都不该出现）。
    """
    out: dict[str, tuple[str, ...]] = {}
    for k, spec in (v or {}).items():
        if isinstance(spec, (list, tuple)):
            params = tuple(str(x) for x in spec)
        elif spec is None:
            params = ()
        else:
            params = (str(spec),)
        out[str(k)] = params
    return out


@dataclass(frozen=True)
class GameConfig:
    """一款游戏的全套旋钮。字段名和老的 kblib.config 常量一一对应，方便对照。"""

    name: str
    label: str
    wiki_base: str
    api_url: str
    user_agent: str
    data_dir: Path
    eval_dir: Path
    chunk_id_prefix: str
    collection: str
    namespaces: tuple[int, ...]

    # ---- 抓取礼貌参数 ----
    rps: float
    batch_titles: int
    timeout: int
    max_retries: int
    backoff_cap: int
    shard_pages: int

    # ---- chunk 规则 ----
    chunk_min: int
    chunk_target: int
    chunk_max: int
    voice_field_suffix: str
    fields_chunk_min: int
    max_images_per_page: int

    # ---- 解析 ----
    infobox_templates: dict[str, tuple[str, str]]
    infobox_template_prefixes: dict[str, tuple[str, str]]
    legacy_infobox_template_names: frozenset[str]
    title_patterns: tuple[tuple[re.Pattern[str], str], ...]
    new_kinds: frozenset[str]
    card_field_keys: dict[str, frozenset[str]]
    nav_template_prefixes: tuple[str, ...]
    noise_section_keywords: tuple[str, ...]
    min_infobox_fields: int
    # 展示型模板渲染表：模板名 → 要渲染的参数名（位置参数写 "1"/"2"…，空元组=渲染成空）
    display_templates: dict[str, tuple[str, ...]]
    # 模板名里嵌 HTML 注释时是否剥掉再比对（PRTS 的 CharinfoV2 就嵌了一行）
    strip_template_name_comments: bool
    # 专用抽取器模块（kblib.extractors.<x>）；不配 = 全走通用实体路
    extractor_module: str | None
    # 通用实体路：kind → {card_fields, attr_fields, child_templates}
    kinds: dict[str, dict]
    # 整份 YAML 原样透传：pipeline 之外的旋钮（如 audit_pages 的标题分类规则）
    # 走这里，不必为每个开发小工具在 GameConfig 上加字段
    raw: dict

    # ---- 派生路径（都在 data_dir 下，azurlane 的 data_dir 就是 kb/data）----
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def spike_dir(self) -> Path:
        return self.data_dir / "raw" / "spike"

    @property
    def titles_cache(self) -> Path:
        return self.raw_dir / "titles.json"

    @property
    def kb_db(self) -> Path:
        return self.data_dir / "kb.db"

    @property
    def chunks_path(self) -> Path:
        return self.data_dir / "chunks.jsonl"

    @property
    def redirects_path(self) -> Path:
        return self.data_dir / "redirects.jsonl.gz"

    @property
    def image_urls_path(self) -> Path:
        return self.data_dir / "image_urls.json"

    @property
    def bm25_dir(self) -> Path:
        return self.data_dir / "bm25"

    @property
    def index_meta_path(self) -> Path:
        return self.data_dir / "index_meta.json"

    @property
    def questions_path(self) -> Path:
        return self.eval_dir / "questions.jsonl"

    def page_url(self, title: str) -> str:
        from urllib.parse import quote

        return f"{self.wiki_base}/{quote(str(title).replace(' ', '_'))}"

    # ---- 结构化路辅助 ----
    def template_kinds(self) -> dict[str, tuple[str, str]]:
        """infobox 模板名 → (kind, category)。前缀家族合并进来（子页名归一后查表）。"""
        return {**self.infobox_templates, **self.infobox_template_prefixes}

    def kind_of(self, tpl_name: str) -> tuple[str, str] | None:
        return self.template_kinds().get(tpl_name)


_CACHE: dict[str, GameConfig] = {}


def available_games() -> list[str]:
    if not GAMES_DIR.exists():
        return []
    return sorted(p.stem for p in GAMES_DIR.glob("*.yaml"))


def load_game(name: str | None = None) -> GameConfig:
    """按名字读 kb/games/<name>.yaml；不给名字用默认游戏（azurlane）。"""
    name = (name or DEFAULT_GAME).strip()
    if name in _CACHE:
        return _CACHE[name]
    path = GAMES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"没有游戏配置 {path}（现有：{', '.join(available_games()) or '无'}）"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fetch = raw.get("fetch", {}) or {}
    chunk = raw.get("chunk", {}) or {}
    parse = raw.get("parse", {}) or {}

    data_dir = Path(str(raw.get("data_dir", f"data/{name}")))
    if not data_dir.is_absolute():
        data_dir = DATA_DIR.parent / data_dir
    eval_dir = Path(str(raw.get("eval_dir", f"eval/{name}")))
    if not eval_dir.is_absolute():
        eval_dir = DATA_DIR.parent / eval_dir

    cfg = GameConfig(
        name=str(raw.get("name", name)),
        label=str(raw.get("label", name)),
        wiki_base=str(raw["wiki_base"]).rstrip("/"),
        api_url=str(raw.get("api_url") or f"{str(raw['wiki_base']).rstrip('/')}/api.php"),
        user_agent=str(raw.get("user_agent")
                       or "anisignal-kb/0.1 (offline knowledge-base build; polite 1.5 req/s)"),
        data_dir=data_dir,
        eval_dir=eval_dir,
        chunk_id_prefix=str(raw.get("chunk_id_prefix", name)),
        collection=str(raw.get("collection", f"{name}_kb")),
        namespaces=tuple(int(n) for n in (raw.get("namespaces") or [0])),
        # 抓取
        rps=float(fetch.get("rps", 1.5)),
        batch_titles=int(fetch.get("batch_titles", 20)),
        timeout=int(fetch.get("timeout", 180)),
        max_retries=int(fetch.get("max_retries", 6)),
        backoff_cap=int(fetch.get("backoff_cap", 60)),
        shard_pages=int(fetch.get("shard_pages", 500)),
        # chunk
        chunk_min=int(chunk.get("min", 60)),
        chunk_target=int(chunk.get("target", 420)),
        chunk_max=int(chunk.get("max", 900)),
        voice_field_suffix=str(chunk.get("voice_field_suffix", "台词")),
        fields_chunk_min=int(chunk.get("fields_chunk_min", 60)),
        max_images_per_page=int(chunk.get("max_images_per_page", 16)),
        # 解析
        infobox_templates=_as_pairs(parse.get("infobox_templates")),
        infobox_template_prefixes=_as_pairs(parse.get("infobox_template_prefixes")),
        legacy_infobox_template_names=frozenset(
            _as_tuple_str(parse.get("legacy_infobox_template_names"))),
        title_patterns=tuple(
            (re.compile(str(p["regex"])), str(p["kind"]))
            for p in (parse.get("title_patterns") or []) if p.get("regex")
        ),
        new_kinds=frozenset(_as_tuple_str(parse.get("new_kinds"))),
        card_field_keys=_as_frozensets(parse.get("card_field_keys")),
        nav_template_prefixes=_as_tuple_str(parse.get("nav_template_prefixes")),
        noise_section_keywords=_as_tuple_str(parse.get("noise_section_keywords")),
        min_infobox_fields=int(parse.get("min_infobox_fields", 5)),
        display_templates=_as_display(parse.get("display_templates")),
        strip_template_name_comments=bool(parse.get("strip_template_name_comments", False)),
        extractor_module=(str(parse["extractor_module"]) if parse.get("extractor_module") else None),
        kinds={str(k): (v or {}) for k, v in (parse.get("kinds") or {}).items()},
        raw=raw,
    )
    _CACHE[name] = cfg
    return cfg


def get_game(name: str | None = None) -> GameConfig:
    return load_game(name)
