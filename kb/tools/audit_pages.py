"""覆盖率审计：扫原始分片，逐页统计解析覆盖率与内容类型分布。

输出三张表：
  1. 内容类型分布（other 拆解，规则在 games/<game>.yaml 的 audit.title_rules）
  2. 解析丢失疑点：wikitext 很长但清洗后没内容的页
  3. 特殊语法覆盖：tabber / gallery / 图片引用 / infobox 家族 / 语音台词

用法：
    ./kb/.venv/bin/python tools/audit_pages.py                  # 碧蓝航线
    ./kb/.venv/bin/python tools/audit_pages.py --game arknights # 明日方舟
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib import cleantext, gamecfg  # noqa: E402

# 需要单独点名的语法特征（正文里出现即计数）
FEATURES = {
    "tabber标签": re.compile(r"<tabber|{{\s*#?\s*tag\s*:\s*tabber|选项卡面板|切换标签|tabber"),
    "gallery图库": re.compile(r"<gallery", re.I),
    "图片引用": re.compile(r"\[\[\s*(?:文件|檔案|档案|File|Image)\s*:", re.I),
    "语音台词标题": re.compile(r"语音|台词|语音台词"),
    "折叠面板": re.compile(r"折叠|展开|collapse", re.I),
    "彩色文字": re.compile(r"<span\s+style|{{\s*颜色", re.I),
    "时间轴": re.compile(r"<timeline|easy-timeline", re.I),
    "数学公式": re.compile(r"<math|{{\s*#?tag\s*:\s*math", re.I),
    "SMW查询": re.compile(r"{{\s*#ask|{{\s*#show|{{\s*#set", re.I),
    "雷达图/图表": re.compile(r"{{\s*图表|{{\s*雷达|{{\s*chart", re.I),
}


def classify(title: str, wikitext: str, title_rules) -> str:
    for name, rx in title_rules:
        if rx.search(title):
            return name
    return "未归类"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=gamecfg.DEFAULT_GAME,
                    help=f"游戏配置名，默认 {gamecfg.DEFAULT_GAME}")
    ap.add_argument("--source", type=Path, default=None)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "audit_pages.json")
    args = ap.parse_args()
    cfg = gamecfg.load_game(args.game)
    source = args.source or cfg.raw_dir
    infobox_names = set(cfg.infobox_templates)
    # 未归类页拆解规则来自 YAML（碧蓝航线那套），没配就只报"未归类"
    title_rules = [(str(r.get("name")), re.compile(str(r.get("regex"))))
                   for r in (cfg.raw.get("audit", {}) or {}).get("title_rules", []) or []]
    kinds = Counter()
    other_break = Counter()
    feat = Counter()
    zero_chunk: list[dict] = []
    infobox_names_seen = Counter()
    all_infobox_like = Counter()
    total = 0
    chunk_by_page: Counter = Counter()

    # 顺带把每页 wikitext 里出现的所有"图鉴/数据"类模板名记下来，找第 10 种
    LIKE_RX = re.compile(r"^\s*{{\s*([^{}|\n]+?)\s*(?:\|[^}]*)?$", re.M)

    for shard in sorted(source.glob("*.jsonl.gz")):
        with gzip.open(shard, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                title = rec["title"]
                wt = rec["wikitext"]
                total += 1

                found = cleantext.top_level_infobox(
                    wt, infobox_names, cfg.legacy_infobox_template_names,
                    tuple(cfg.infobox_template_prefixes))
                if found:
                    kind = (cfg.kind_of(cleantext.infobox_template_name(
                        found[0], tuple(cfg.infobox_template_prefixes))) or ("other",))[0]
                else:
                    kind = next((k for rx, k in cfg.title_patterns if rx.match(title)), "other")
                if kind == "other":
                    other_break[classify(title, wt, title_rules)] += 1
                kinds[kind] += 1

                for k, rx in FEATURES.items():
                    if rx.search(wt):
                        feat[k] += 1

                # infobox 候选：顶层模板里带"图鉴"或字段很多的
                for m in LIKE_RX.finditer(wt[:4000]):
                    n = m.group(1).strip()
                    if "图鉴" in n or "信息" in n or "数据" in n or "档案" in n:
                        all_infobox_like[n] += 1

                if kind == "other" and len(wt) > 3000:
                    body = cleantext.to_plain(found[1] if found else wt)
                    if len(body) < 200:
                        zero_chunk.append({"title": title, "len": len(wt),
                                           "body": len(body)})

    result = {
        "pages": total,
        "kinds": dict(kinds),
        "other_breakdown": dict(other_break.most_common()),
        "features": dict(feat.most_common()),
        "infobox_like_templates": dict(all_infobox_like.most_common(40)),
        "other_long_but_empty": zero_chunk[:60],
        "other_long_but_empty_n": len(zero_chunk),
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"页数 {total}  类型 {dict(kinds)}")
    print("\nother 拆解：")
    for k, v in other_break.most_common():
        print(f"  {k:<14} {v}")
    print("\n语法特征（出现页数）：")
    for k, v in feat.most_common():
        print(f"  {k:<14} {v}")
    print("\ninfobox 候选模板名 top：")
    for k, v in all_infobox_like.most_common(25):
        print(f"  {k:<28} {v}")
    print(f"\nother 里 wikitext>3000 但清洗后<200 字的页：{len(zero_chunk)}")
    for r in zero_chunk[:15]:
        print("   ", r)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
