#!/usr/bin/env python3
"""开发小工具：直接从 data/<game>/raw/*.jsonl.gz 里按标题查原始 wikitext。

用来独立核对 eval 题库的标准答案（不经过 parser，避免自证）。
    ./kb/.venv/bin/python tools/raw_lookup.py 光辉 企业
    ./kb/.venv/bin/python tools/raw_lookup.py --game arknights --grep "职业" 阿米娅
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 直接跑本文件时能找到 kblib

from kblib import gamecfg


def iter_raw(dirs):
    files: list[Path] = []
    for d in dirs:
        files.extend(sorted(d.glob("*.jsonl.gz")))
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("titles", nargs="*")
    ap.add_argument("--grep", default="", help="只输出匹配该正则的行")
    ap.add_argument("--spike", action="store_true", help="连 spike 目录一起搜")
    ap.add_argument("--max-chars", type=int, default=1200)
    ap.add_argument("--game", default=gamecfg.DEFAULT_GAME,
                    help=f"游戏配置名，默认 {gamecfg.DEFAULT_GAME}")
    args = ap.parse_args()
    cfg = gamecfg.load_game(args.game)

    dirs = [cfg.raw_dir] + ([cfg.spike_dir] if args.spike else [])
    want = set(args.titles)
    found = 0
    for rec in iter_raw(dirs):
        if want and rec["title"] not in want:
            continue
        found += 1
        print("=" * 70)
        print(f"# {rec['title']}  (pageid={rec.get('page_id')}, {len(rec['wikitext'])} chars)")
        text = rec["wikitext"]
        if args.grep:
            for line in text.split("\n"):
                if re.search(args.grep, line):
                    print(line[:400])
        else:
            print(text[: args.max_chars])
    if want and found < len(want):
        missing = want - {t for t in want}
        print(f"（注意：还有 {len(want) - found} 个标题没抓到）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
