"""题库草稿回查：把候选问题逐条打到探针上，打印实得值，人工核对后再定稿。

为什么单独一个脚本而不直接改 questions.jsonl：`tools/check_questions.py` 是
"已定稿的题逐条自检"，而这里是"还没有标准答案、要现查一个出来"——顺序相反。
标准答案必须来自实得值（再抽几条回查原始 wikitext），不能让 parser 自证。

用法：./.venv/bin/python tools/probe_explore.py --game genshin probe.tsv
      tsv 每行：probe<TAB>title<TAB>json_args
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib import gamecfg  # noqa: E402
import eval as ev  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=None)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("tsv", type=Path)
    args = ap.parse_args()
    cfg = gamecfg.load_game(args.game)
    conn = sqlite3.connect(args.db or cfg.kb_db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for line in args.tsv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        probe, title, raw = (line.split("\t") + ["{}"])[:3]
        fn = ev.GENERIC_PROBES.get(probe) or ev.PROBES[probe]
        try:
            got = (fn(cur, cfg, title=title, **json.loads(raw))
                   if probe in ev.GENERIC_PROBES
                   else fn(cur, title=title, **json.loads(raw)))
        except Exception as e:  # noqa: BLE001
            got = f"<ERR {e}>"
        got = (got[:120] + "…") if isinstance(got, str) and len(got) > 120 else got
        print(f"{probe:14s} {title:16s} {raw:44s} → {got!r}")


if __name__ == "__main__":
    sys.exit(main())
