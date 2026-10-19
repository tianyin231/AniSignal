"""诊断：每张子表声明的模板，实际在页面哪里出现（顶层 / infobox 参数值里 / 根本没有）。

用法：./.venv/bin/python tools/diag_child_templates.py --game genshin
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib import cleantext, gamecfg  # noqa: E402


def walk(node: Any, out: list, depth: int) -> None:  # noqa: F821
    for child in getattr(node, "nodes", None) or ():
        if type(child).__name__ == "Template":
            out.append((child, depth))
            walk(child, depth + 1)
        else:
            walk(child, depth)
            contents = getattr(child, "contents", None)
            if contents is not None and contents is not child:
                walk(contents, depth)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=None)
    args = ap.parse_args()
    cfg = gamecfg.load_game(args.game)
    # 声明 → (kind, spec)
    decl: dict[str, list[tuple[str, dict]]] = {}
    for kind, spec in cfg.kinds.items():
        for st in spec.get("child_templates", []) or []:
            names = st.get("template") or []
            if isinstance(names, str):
                names = [names]
            for n in names:
                decl.setdefault(str(n), []).append((kind, st))

    # 名字 → [顶层页数, 嵌在 infobox 参数值里的页数, 出现在正文里的页数]
    stat: dict[str, Counter] = {n: Counter() for n in decl}
    infobox_names = set(cfg.template_kinds())
    for f in sorted((cfg.raw_dir).glob("pages-*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                d = json.loads(line)
                wt = d["wikitext"]
                wc = cleantext.parse_wikitext(wt)
                if not wc:
                    continue
                found = cleantext.find_top_level_infobox(
                    wc, cfg.template_kinds(), cfg.legacy_infobox_template_names,
                    cfg.infobox_template_prefixes, descend_tags=cfg.descend_html_tags)
                top = {cleantext.template_name(t)
                       for t in cleantext.iter_templates(wc, cfg.descend_html_tags)}
                nested_in_ib: set[str] = set()
                if found:
                    for p in found.params:
                        if str(p.name).strip().isdigit():
                            continue
                        sub = cleantext.parse_wikitext(str(p.value))
                        if not sub:
                            continue
                        for t in sub.filter_templates(recursive=True):
                            nested_in_ib.add(cleantext.template_name(t))
                for n in decl:
                    if n in top:
                        stat[n]["top"] += 1
                    elif n in nested_in_ib:
                        stat[n]["in_infobox_param"] += 1
                    elif n in wt:
                        stat[n]["body_only"] += 1
    print(f"{'模板':<20} {'顶层':>7} {'infobox参数内':>14} {'仅在正文':>9}  声明 kind")
    for n, c in sorted(stat.items(), key=lambda x: -sum(x[1].values())):
        kinds = ",".join(sorted({k for k, _ in decl[n]}))
        print(f"{n:<20} {c['top']:>7} {c['in_infobox_param']:>14} {c['body_only']:>9}  {kinds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
