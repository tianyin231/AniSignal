#!/usr/bin/env python3
"""重定向别名 → 目标页（主命名空间全部重定向页）。

fetch.py 只拉非重定向页（apfilterredir=nonredirects），所以别名本身从没进过库：
查"企业"搜不到"企业号"。重定向页只有一行 `#REDIRECT [[x]]`，拉回来几乎不花钱，
落到 data/redirects.jsonl.gz，parse.py 读进 `aliases` 表。

实现要点（都踩过坑）：
  * 分页的 `continue` 在 API 响应的**顶层**，不在 `query` 里——用
    MediaWikiClient.iter_all_titles(filter_redir="redirects")，别自己抠；
  * 目标解析用 API 自带的 redirects=1 解析（链式 A→B→C 一次到底），不要拉正文
    正则抠 `#REDIRECT`：本wiki 上传回的重定向页正文会被解析成目标页内容，
    正则一条都匹配不上。

用法：
    ./kb/.venv/bin/python tools/fetch_redirects.py
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib.config import REDIRECTS_PATH  # noqa: E402
from kblib.wikiapi import MediaWikiClient  # noqa: E402

log = logging.getLogger("kb.fetch_redirects")


def main() -> int:
    ap = argparse.ArgumentParser(description="拉主命名空间重定向页 → aliases 表数据")
    ap.add_argument("--namespace", type=int, default=0)
    ap.add_argument("--rps", type=float, default=1.5)
    ap.add_argument("--out", type=Path, default=REDIRECTS_PATH)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    client = MediaWikiClient(rps=args.rps)

    titles = list(client.iter_all_titles(args.namespace, filter_redir="redirects"))
    print(f"命名空间 {args.namespace} 下重定向页 {len(titles)} 个")

    t0 = time.time()
    pairs = client.resolve_redirects(titles)
    unresolved = len(titles) - len(pairs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
        for alias, target in sorted(pairs.items()):
            f.write(json.dumps({"alias": alias, "target": target},
                               ensure_ascii=False) + "\n")
    tmp.replace(args.out)
    print(f"完成：{len(pairs)}/{len(titles)} 个别名解析出目标 → {args.out}"
          f"（{client.request_count} 次请求，{time.time() - t0:.0f}s）")
    if unresolved:
        log.warning("%d 个别名没解析出目标（断链/指向自己），不进 aliases 表", unresolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
