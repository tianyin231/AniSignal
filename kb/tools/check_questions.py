"""题库自检：把 50 题逐条跑一遍探针/检索，打印实际值，人工核对后再定稿。

用法：./.venv/bin/python tools/check_questions.py --game genshin
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
    ap.add_argument("--questions", type=Path, default=None)
    args = ap.parse_args()
    cfg = gamecfg.load_game(args.game)
    qpath = args.questions or cfg.questions_path
    rows = [json.loads(l) for l in qpath.read_text(encoding="utf-8").splitlines() if l.strip()]
    conn = sqlite3.connect(cfg.kb_db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    bad = 0
    for r in rows:
        if r["kind"] == "table":
            fn = ev.PROBES.get(r["probe"]) or ev.GENERIC_PROBES[r["probe"]]
            try:
                if r["probe"] in ev.GENERIC_PROBES:
                    got = fn(cur, cfg, **r["args"])
                else:
                    got = fn(cur, **r["args"])
            except Exception as e:  # noqa: BLE001
                got = f"<ERR {e}>"
            ok = str(got) == str(r["expect"]) if got is not None else False
            if not ok:
                bad += 1
            print(f"{r['id']} {'OK ' if ok else 'BAD'} {r['q']}")
            print(f"      probe={r['probe']} {r['args']}")
            print(f"      expect={r['expect']!r}")
            print(f"      got   ={got!r}")
        else:
            print(f"{r['id']} QA  {r['q']}  kw={r['expect_keywords']} src={r['source_title']}")
    print(f"\n查表题 {sum(1 for r in rows if r['kind']=='table')} 条中不符 {bad} 条")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
