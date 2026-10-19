#!/usr/bin/env python3
"""阶段 2：解析。wikitext → 结构化 SQLite + 语义 chunk JSONL。

两条路并行：

  * 结构化路：infobox 模板 → SQLite。字段语义特异的游戏（碧蓝航线的舰娘/装备/
    关卡图鉴）走 `kblib/extractors/azurlane.py` 的专用抽取器；字段即结构的游戏
    （明日方舟 CharinfoV2/敌人信息/道具信息）走通用实体路——列名→字段名映射全在
    games/*.yaml 的 `parse.kinds` 里，零代码。
  * 文本路：正文去噪 → 分节 → 分段（一段讲一件事）→ chunks.jsonl

用法：
    ./kb/.venv/bin/python parse.py --source data/raw/spike --db data/spike_kb.db \\
        --chunks data/spike_chunks.jsonl          # spike 验证抽取率
    ./kb/.venv/bin/python parse.py                 # 全量（默认游戏 azurlane）
    ./kb/.venv/bin/python parse.py --game arknights
"""

from __future__ import annotations

import argparse
import gzip
import importlib
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

from kblib import cleantext, gamecfg
from kblib.extractors import (
    CORE_SCHEMA,
    GENERIC_SCHEMA,
    ExtractCtx,
    Row,
    child_table_ddl,
    generic_entity,
)

log = logging.getLogger("kb.parse")

# ---------------------------------------------------------------- chunk 参数默认值
# 具体数值来自游戏配置（games/*.yaml 的 chunk: 段）；这里的常量只在不传 cfg 的
# 老调用点生效
HEADING_RE = re.compile(r"^(={1,6})\s*(.+?)\s*\1$")
SENT_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])")

NOISE_PARAGRAPH_RES = [
    re.compile(r"^最后编辑"),
    re.compile(r"^本页面最后"),
    re.compile(r"^本文引用"),
    re.compile(r"^参考资料"),
    re.compile(r"^版权"),
    re.compile(r"^\s*[\d\s\|\-:：,，.。]*$"),
]


# ---------------------------------------------------------------- 数据库
def open_db(path: Path, cfg: gamecfg.GameConfig | None = None) -> sqlite3.Connection:
    """建库：骨架表 + 游戏专用表（extractor_module）+ 通用实体表 + 配置声明的子表。"""
    cfg = cfg or gamecfg.load_game(None)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    # parse 是全量重建：先清掉旧表，保证可重复跑
    old = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in old:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    ddl = CORE_SCHEMA
    mod = load_extractor_module(cfg)
    if mod is not None:
        ddl += getattr(mod, "SCHEMA", "")
    # 有 kind 走通用实体路（没挂专用抽取器模块，或模块里没这个 kind）才建通用表
    generic_kinds = {k for k in cfg.kinds if mod is None or k not in getattr(mod, "EXTRACTORS", {})}
    if generic_kinds:
        ddl += GENERIC_SCHEMA + child_table_ddl(cfg)
    conn.executescript(ddl)
    return conn


def load_extractor_module(cfg: gamecfg.GameConfig):
    """按配置挂专用抽取器模块；没配（新游戏）就返回 None，全走通用实体路。"""
    name = cfg.extractor_module
    if not name:
        return None
    try:
        return importlib.import_module(name)
    except ImportError as e:  # pragma: no cover - 配置写错才到这
        raise RuntimeError(f"游戏 {cfg.name} 的 extractor_module {name} 导入失败：{e}") from e


def extractors_of(cfg: gamecfg.GameConfig) -> dict:
    """kind → (抽取函数, strict)。strict=True 要求 infobox 字段数达标。"""
    mod = load_extractor_module(cfg)
    if mod is None:
        return {}
    return dict(getattr(mod, "EXTRACTORS", {}))


# ---------------------------------------------------------------- 分节 / 分段
def iter_sections(text: str):
    """按标题切成 (标题路径, 正文) 段。"""
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush():
        if buf:
            body = "\n".join(buf)
            buf.clear()
            return body
        return None

    for line in text.split("\n"):
        m = HEADING_RE.match(line.strip())
        if m:
            body = flush()
            if body is not None:
                yield [t for _, t in stack], body
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    body = flush()
    if body is not None:
        yield [t for _, t in stack], body


def split_long(text: str, limit: int) -> list[str]:
    """超长段落按句子边界再切。"""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for sent in SENT_SPLIT_RE.split(text):
        if not sent:
            continue
        if len(cur) + len(sent) > limit and cur:
            out.append(cur.strip())
            cur = sent
        else:
            cur += sent
    if cur.strip():
        out.append(cur.strip())
    return out


class Chunker:
    """一个游戏的切 chunk 口径（min/target/max + 噪音规则都来自配置）。"""

    def __init__(self, cfg: gamecfg.GameConfig) -> None:
        self.cfg = cfg

    def is_noise_heading(self, path: list[str]) -> bool:
        return any(any(k in h for k in self.cfg.noise_section_keywords) for h in path)

    def is_noise_paragraph(self, p: str) -> bool:
        if len(p) < self.cfg.chunk_min:
            return True
        return any(r.search(p) for r in NOISE_PARAGRAPH_RES)

    def pack_paragraphs(self, paras: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """(heading, 段落) 列表 → 合并到 target、超长按句再切、丢短尾。"""
        cfg = self.cfg
        chunks: list[tuple[str, str]] = []
        cur_head, cur = "", ""
        for heading, para in paras:
            if heading != cur_head and cur:
                chunks.append((cur_head, cur.strip()))
                cur = ""
            cur_head = heading
            for piece in split_long(para, cfg.chunk_max):
                if cur and len(cur) + len(piece) + 1 > cfg.chunk_target:
                    chunks.append((cur_head, cur.strip()))
                    cur = ""
                cur = (cur + "\n" + piece).strip() if cur else piece
        if cur.strip():
            chunks.append((cur_head, cur.strip()))
        return [(h, c) for h, c in chunks if len(c) >= cfg.chunk_min]

    def chunk_body(self, text: str) -> list[tuple[str, str]]:
        """正文纯文本 → [(标题路径, chunk 文本)]。"""
        paras: list[tuple[str, str]] = []
        for path, body in iter_sections(text):
            if self.is_noise_heading(path):
                continue
            heading = " > ".join(path)
            body = cleantext.tables_to_lines(body)
            for para in re.split(r"\n\s*\n", body):
                # MediaWiki 语义：单个换行等于空格；表格行已由 tables_to_lines 压成一行
                para = re.sub(r"\s*\n\s*", " ", para)
                para = re.sub(r"[ \t]{2,}", " ", para).strip()
                if not para or self.is_noise_paragraph(para):
                    continue
                paras.append((heading, para))
        return self.pack_paragraphs(paras)

    def pack_lines(self, lines: list[str], heading: str) -> list[tuple[str, str]]:
        """一批独立成义的行（图注/台词/模板参数）→ chunk，口径同 pack_paragraphs。"""
        return self.pack_paragraphs([(heading, line) for line in lines if line and line.strip()])


# ---------------------------------------------------------------- 主流程
def read_shards(source: Path):
    """按修改时间顺序读 source 下的 *.jsonl.gz。"""
    files = sorted(source.glob("*.jsonl.gz"))
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def load_image_urls(path: Path) -> dict[str, str]:
    """文件名 → 直链缓存（tools/fetch_images.py 批量 imageinfo 换来的）。"""
    if not path.exists():
        return {}
    try:
        return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if v}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("图片 URL 缓存读不了（%s），本次不记 images", e)
        return {}


def insert_rows(conn: sqlite3.Connection, rows: list[Row]) -> None:
    for r in rows:
        cols = list(r.values)
        # 列名一律加双引号：子表列名来自 YAML，可能有 trigger 这种 SQL 关键字
        conn.execute(
            f'INSERT OR REPLACE INTO {r.table}({", ".join(chr(34) + c + chr(34) for c in cols)}) '
            f"VALUES({', '.join('?' * len(cols))})",
            [r.values[c] for c in cols],
        )


def run(source: Path, db_path: Path, chunks_path: Path, report: bool = True,
        game: str | None = None) -> dict:
    cfg = gamecfg.load_game(game)
    conn = open_db(db_path, cfg)
    conn.execute("PRAGMA journal_mode=WAL")
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    chunker = Chunker(cfg)
    extractors = extractors_of(cfg)

    image_urls = load_image_urls(cfg.image_urls_path)
    infobox_names = set(cfg.infobox_templates)
    n_pages = n_ok = 0
    tpl_counter: dict[str, int] = {}
    kind_counter: dict[str, int] = {}
    extra_counter: dict[str, int] = {}
    n_chunks = n_extra = n_images = 0
    failures: list[str] = []

    with open(chunks_path, "w", encoding="utf-8") as cf, \
            cleantext.wiki_style(cfg.display_templates,
                                 cfg.strip_template_name_comments):
        # chunk_id 分配：prose/card 走老计数器（azurlane v2 已入向量库的 id 一个都
        # 不能变，否则增量 upsert 会退化成全量重建）；配置里 new_kinds 声明的 kind
        # 走 x 前缀的另一个计数器，两边互不干扰
        def emit(text: str, heading: str, kind: str, title: str, url: str,
                 images: list[str]) -> None:
            nonlocal n_extra
            rec = {
                "chunk_id": f"{cfg.chunk_id_prefix}:x{n_extra:07d}",
                "text": text,
                "source_title": title,
                "source_url": url,
                "heading": heading,
                "kind": kind,
            }
            if images:
                rec["images"] = images
            cf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_extra += 1
            extra_counter[kind] = extra_counter.get(kind, 0) + 1

        def emit_pack(lines: list[str], heading: str, kind: str, title: str,
                      url: str, images: list[str]) -> None:
            for _h, chunk in chunker.pack_lines(lines, heading):
                emit(chunk, heading, kind, title, url, images)

        for rec in read_shards(source):
            title = rec["title"]
            wikitext = rec["wikitext"]
            n_pages += 1
            wc = cleantext.parse_wikitext(wikitext)
            found = cleantext.find_top_level_infobox(
                wc, infobox_names, cfg.legacy_infobox_template_names,
                cfg.infobox_template_prefixes)
            kind, category, fields = "other", None, {}
            body_wikitext = wikitext

            if found:
                tpl = found
                body_wikitext = wikitext.replace(str(tpl), "", 1)
                # 塞壬图鉴/仲裁机关 这类家族子页归一成家族名再查表
                tpl_name = cleantext.infobox_template_name(tpl, cfg.infobox_template_prefixes)
                hit = cfg.kind_of(tpl_name)
                if hit:
                    kind, category = hit
                fields = cleantext.infobox_fields(tpl)
                tpl_counter[tpl_name] = tpl_counter.get(tpl_name, 0) + 1
            else:
                for rx, k in cfg.title_patterns:
                    if rx.match(title):
                        kind = k
                        break

            infobox_ok = 1 if len(fields) >= cfg.min_infobox_fields else 0
            if infobox_ok:
                n_ok += 1
            kind_counter[kind] = kind_counter.get(kind, 0) + 1

            # ---- 结构化落库 ----
            # strict 的 kind（azurlane 老三族）要求字段数达标：它们的 card 走老
            # chunk_id 计数器，门槛一改 id 就错位；宽松 kind（v3 新家族 + 各游戏的
            # 通用实体路）有字段就收，card 走 x 前缀计数器，随便降。
            spec = extractors.get(kind)
            strict = bool(spec[1]) if spec else False
            row_ok = infobox_ok if strict else (1 if fields else 0)
            card_text = None
            if found and row_ok:
                ctx = ExtractCtx(
                    title=title, page_id=rec.get("page_id"), kind=kind, category=category,
                    fields=fields, cfg=cfg, wikitext=wikitext, body_wikitext=body_wikitext,
                    top_level_templates=list(wc.filter_templates(recursive=False)) if wc else [],
                )
                if spec:
                    result = spec[0](ctx)
                elif kind in cfg.kinds:
                    result = generic_entity(ctx)
                else:
                    result = None
                if result is not None:
                    insert_rows(conn, result.rows)
                    card_text = result.card_text

            conn.execute(
                "INSERT OR REPLACE INTO pages(page_id,title,kind,url,wikitext_len,infobox_template,"
                "infobox_fields,infobox_ok) VALUES(?,?,?,?,?,?,?,?)",
                (rec.get("page_id"), title, kind, cfg.page_url(title), len(wikitext),
                 tpl_name if found else None, len(fields), infobox_ok),
            )

            # ---- 图片 URL（只记链接不下载）----
            url = cfg.page_url(title)
            images: list[str] = []
            for sort, fname in enumerate(cleantext.extract_image_files(
                    wikitext, cfg.max_images_per_page)):
                u = image_urls.get(fname)
                conn.execute("INSERT OR REPLACE INTO page_images VALUES(?,?,?,?)",
                             (title, sort, fname, u))
                if u:
                    images.append(u)
            n_images += len(images)

            # ---- 文本路：正文 → chunk ----
            body_text = cleantext.to_plain(body_wikitext)
            for heading, chunk in chunker.chunk_body(body_text):
                cf.write(json.dumps({
                    "chunk_id": f"{cfg.chunk_id_prefix}:{n_chunks:07d}",
                    "text": chunk,
                    "source_title": title,
                    "source_url": url,
                    "heading": heading,
                    "kind": "prose",
                    **({"images": images} if images else {}),
                }, ensure_ascii=False) + "\n")
                n_chunks += 1
            if card_text:
                # 配置里声明走 x 前缀计数器的 kind（azurlane 的 v3 新家族），
                # 不挤占老 id；其余 kind 的 card 走老计数器
                if kind in cfg.new_kinds:
                    emit(card_text, "卡片摘要", "card", title, url, images)
                else:
                    cf.write(json.dumps({
                        "chunk_id": f"{cfg.chunk_id_prefix}:{n_chunks:07d}",
                        "text": card_text,
                        "source_title": title,
                        "source_url": url,
                        "heading": "卡片摘要",
                        "kind": "card",
                        **({"images": images} if images else {}),
                    }, ensure_ascii=False) + "\n")
                    n_chunks += 1

            # ---- 补捞：正文路径丢过/只取了首参的内容，另切一类 chunk ----
            emit_pack(cleantext.gallery_lines(wikitext), "图库", "gallery", title, url, images)
            emit_pack(cleantext.hidden_lines(wikitext), "隐藏正文", "hidden", title, url, images)
            emit_pack(cleantext.template_dump(body_wikitext, exclude=str(found) if found else None,
                                             nav_prefixes=cfg.nav_template_prefixes),
                      "模板数据", "template", title, url, images)
            if fields:
                voice = [f"{scene}：{line}" for scene, line in
                         cleantext.voice_lines(fields, cfg.voice_field_suffix)]
                emit_pack(voice, "语音台词", "voice", title, url, images)
                skip = cfg.card_field_keys.get(kind, frozenset())
                long_fields = [ln for ln in cleantext.long_field_lines(fields, cfg.fields_chunk_min)
                               if ln.split(":", 1)[0] not in skip]
                emit_pack(long_fields, "图鉴字段", "fields", title, url, images)

            if found and not infobox_ok and not row_ok:
                failures.append(title)

    # ---- 重定向别名（内容在目标页里，别名本身也要能查到）----
    n_aliases = 0
    if cfg.redirects_path.exists():
        with gzip.open(cfg.redirects_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("alias") and d.get("target"):
                    conn.execute("INSERT OR REPLACE INTO aliases VALUES(?,?)",
                                 (d["alias"], d["target"]))
                    n_aliases += 1

    for k, v in (("pages", n_pages), ("infobox_ok", n_ok), ("chunks", n_chunks),
                 ("extra_chunks", n_extra), ("aliases", n_aliases),
                 ("page_images", n_images),
                 ("infobox_rate", round(n_ok / max(n_pages, 1), 4))):
        conn.execute("INSERT OR REPLACE INTO parse_stats VALUES(?,?)", (k, str(v)))
    conn.commit()
    conn.close()

    result = {
        "pages": n_pages,
        "infobox_pages": sum(tpl_counter.values()),
        "infobox_ok": n_ok,
        "infobox_rate": round(n_ok / max(sum(tpl_counter.values()), 1), 4),
        "overall_rate": round(n_ok / max(n_pages, 1), 4),
        "templates": dict(sorted(tpl_counter.items(), key=lambda x: -x[1])),
        "kinds": kind_counter,
        "chunks": n_chunks,
        "extra_chunks": n_extra,
        "extra_by_kind": dict(sorted(extra_counter.items(), key=lambda x: -x[1])),
        "page_images": n_images,
        "aliases": n_aliases,
        "weak_infobox": failures[:50],
    }
    if report:
        print(f"游戏          : {cfg.label}（{cfg.name}）")
        print(f"页面数        : {n_pages}")
        print(f"带 infobox 页 : {result['infobox_pages']}")
        print(f"抽取成功      : {n_ok}")
        print(f"结构化抽取率  : {result['infobox_rate']:.1%}（占带 infobox 页）")
        print(f"全页占比      : {result['overall_rate']:.1%}")
        print("模板分布      :")
        for k, v in result["templates"].items():
            print(f"    {k:<16} {v}")
        print(f"页面类型      : {kind_counter}")
        print(f"chunk 数      : {n_chunks}（prose/card，id 与 v2 对齐）")
        print(f"补捞 chunk   : {n_extra}  {result['extra_by_kind']}")
        print(f"图片 URL     : {n_images}（{len(image_urls)} 个文件名已换直链）")
        print(f"重定向别名    : {n_aliases}")
        if failures:
            print(f"抽取不完整示例 : {failures[:10]}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="解析 wikitext → SQLite + chunk JSONL")
    ap.add_argument("--game", default=None,
                    help=f"游戏配置 kb/games/<name>.yaml（默认 {gamecfg.DEFAULT_GAME}；"
                         f"现有：{', '.join(gamecfg.available_games())}）")
    ap.add_argument("--source", type=Path, default=None, help="raw 分片目录")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--chunks", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = gamecfg.load_game(args.game)
    source = args.source or cfg.raw_dir
    if not source.exists():
        print(f"找不到 {source}，先跑 fetch.py --game {cfg.name}", file=sys.stderr)
        return 1
    run(source, args.db or cfg.kb_db, args.chunks or cfg.chunks_path, game=cfg.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
