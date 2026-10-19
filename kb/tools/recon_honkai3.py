#!/usr/bin/env python3
"""崩坏3 BWIKI 侦察：全量 ns0 标题 → 抽样 wikitext → 顶层模板分布 + 参数并集。

产出写 kb/games/honkai3.yaml 之前必须过的这一步：infobox 白名单、display_templates、
nav_template_prefixes、parse.kinds 的字段名**全部**从这里来，不靠猜。

用法：
    ./.venv/bin/python tools/recon_honkai3.py --sample 400     # 均匀抽 400 篇
    ./.venv/bin/python tools/recon_honkai3.py --titles 琪亚娜 雷电芽衣
    ./.venv/bin/python tools/recon_honkai3.py --top 40 --params 15
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kblib import gamecfg  # noqa: E402
from kblib.wikiapi import MediaWikiClient  # noqa: E402

# 顶层模板：只看不在最外层嵌套里的 {{...}}，用括号配平避开 {{a|{{b}}}} 把 b 也算进来
TEMPLATE_RE = re.compile(r"\{\{")


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def top_level_templates(wikitext: str) -> list[tuple[str, dict[str, str]]]:
    """配平括号找出顶层模板调用，返回 [(模板名, {参数名: 值})]。

    参数名统一小写去空白；位置参数用 "1"/"2"… 命名（和 display_templates 的约定一致）。
    """
    out: list[tuple[str, dict[str, str]]] = []
    i = 0
    n = len(wikitext)
    while i < n:
        if wikitext.startswith("{{", i):
            depth = 0
            j = i
            while j < n:
                if wikitext.startswith("{{", j):
                    depth += 1
                    j += 2
                    continue
                if wikitext.startswith("}}", j):
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                    continue
                j += 1
            body = wikitext[i + 2:max(j - 2, i + 2)]
            out.append(_parse_tpl(body))
            i = j
            continue
        i += 1
    return out


def _split_params(body: str) -> tuple[str, list[tuple[str, str]]]:
    """按 | 切参数，避开 [[链接|文字]] 和 {{模板|参}} 里的竖线。"""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    k = 0
    while k < len(body):
        ch = body[k]
        if ch == "{" and body.startswith("{{", k):
            depth += 1
            cur.append("{{")
            k += 2
            continue
        if ch == "}" and body.startswith("}}", k):
            depth = max(depth - 1, 0)
            cur.append("}}")
            k += 2
            continue
        if ch == "[" and body.startswith("[[", k):
            depth += 1
            cur.append("[[")
            k += 2
            continue
        if ch == "]" and body.startswith("]]", k):
            depth = max(depth - 1, 0)
            cur.append("]]")
            k += 2
            continue
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
            k += 1
            continue
        cur.append(ch)
        k += 1
    parts.append("".join(cur))
    name = parts[0].strip() if parts else ""
    params: list[tuple[str, str]] = []
    pos = 0
    for p in parts[1:]:
        p = p.strip()
        if not p:
            continue
        if "=" in p:
            k_, v = p.split("=", 1)
            k_ = k_.strip().lower()
            # 位置参数也可能带等号（"身高=163cm" 之外的正文），只认左边像标识符的
            if k_ and re.fullmatch(r"[0-9A-Za-z一-鿿_　 .:%#/+-]{1,40}", k_):
                params.append((k_, v.strip()))
                continue
        pos += 1
        params.append((str(pos), p))
    return name, params


def _parse_tpl(body: str) -> tuple[str, dict[str, str]]:
    name, params = _split_params(body)
    return name, dict(params)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--params", type=int, default=12)
    ap.add_argument("--titles", nargs="*", default=None)
    args = ap.parse_args()

    cfg = gamecfg.load_game("honkai3")
    # game= 必须给：不传的话 MediaWikiClient 按默认游戏（azurlane）读 api_url
    client = MediaWikiClient(rps=cfg.rps, game=cfg.name)

    titles = list(client.iter_all_titles(namespace=0))
    print(f"ns0 标题总数: {len(titles)}")

    if args.titles:
        picked = [t for t in args.titles if t in titles]
    else:
        step = max(1, len(titles) // args.sample)
        picked = titles[::step][: args.sample]
    print(f"抽样: {len(picked)} 篇")

    tpl_count: collections.Counter[str] = collections.Counter()
    tpl_params: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    page_tpls: dict[str, collections.Counter[str]] = {}
    sections: collections.Counter[str] = collections.Counter()
    n_pages = 0

    bs = cfg.batch_titles
    for i in range(0, len(picked), bs):
        batch = picked[i:i + bs]
        recs, _ = client.fetch_revisions_safe(batch)
        for r in recs:
            wt = strip_comments(r["wikitext"])
            n_pages += 1
            c: collections.Counter[str] = collections.Counter()
            for name, params in top_level_templates(wt):
                # 解析器函数（#if/#ask/…）和表格不是模板
                if name.startswith("#"):
                    continue
                tpl_count[name] += 1
                c[name] += 1
                for k in params:
                    tpl_params[name][k] += 1
            page_tpls[r["title"]] = c
            for h in re.findall(r"^==+\s*(.+?)\s*==+\s*$", wt, flags=re.M):
                sections[h.strip()] += 1
        print(f"  ...{n_pages}/{len(picked)}", file=sys.stderr)

    print(f"\n实际取到正文: {n_pages} 篇")
    print(f"\n===== 顶层模板分布 top{args.top}（模板名: 出现篇数/总次数）=====")
    for name, cnt in tpl_count.most_common(args.top):
        npages = sum(1 for c in page_tpls.values() if c.get(name))
        print(f"  {name:32s} {npages:5d} 页 / {cnt:6d} 次")

    print(f"\n===== 前 {args.params} 个模板的参数并集（含出现次数）=====")
    for name, _ in tpl_count.most_common(args.params):
        ps = tpl_params[name]
        print(f"\n### {name}  ({tpl_count[name]} 次)")
        print("   " + ", ".join(f"{k or '(位置)'}({v})" for k, v in ps.most_common(40)))

    print(f"\n===== 二级标题分布 top40 =====")
    for h, cnt in sections.most_common(40):
        print(f"  {cnt:5d}  {h}")

    # 落一份机器可读的，供后面写 YAML 时查
    out = Path(cfg.data_dir) / "recon_templates.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(
        {
            "titles_total": len(titles),
            "sampled": n_pages,
            "tpl_count": dict(tpl_count),
            "tpl_params": {k: dict(v) for k, v in tpl_params.items()},
            "sections": dict(sections),
        },
        out.open("w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )
    print(f"\n明细已落 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
