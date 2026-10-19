#!/usr/bin/env python3
"""图片文件名 → 真实直链（只换 URL，不下载图片）。

wiki 里图片引用只有文件名（`[[文件:X.jpg]]`），bot 要甩原链得先换成
`https://patchwiki.biligame.com/...` 直链。MediaWiki 的文件 URL 带 md5 路径
分区且可能挂在 CDN 上，手拼容易错，直接问 API：`prop=imageinfo&iiprop=url`，
一批 50 个文件名（约 400 次请求），结果缓存到游戏 data_dir 下的 image_urls.json。

缓存可断点续跑：已有的文件名直接跳过，被 kill 再跑同一命令接着换。

用法：
    ./kb/.venv/bin/python tools/fetch_images.py                    # 碧蓝航线全量换
    ./kb/.venv/bin/python tools/fetch_images.py --game arknights   # 明日方舟
    ./kb/.venv/bin/python tools/fetch_images.py --limit 5          # 先试 5 批
    ./kb/.venv/bin/python tools/fetch_images.py --from-db          # 从 kb.db 的 page_images 读清单
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib import gamecfg  # noqa: E402
from kblib.wikiapi import MediaWikiClient  # noqa: E402

log = logging.getLogger("kb.fetch_images")

BATCH = 50          # imageinfo 单批标题数（比正文抓取的 20 大，响应小得多）

FILE_PREFIX = "File:"
# 问过去的是 "File:x"，这个 wiki 会把 File 命名空间本地化成"文件:"再返回
# （normalized 里有 from/to），回贴时必须把前缀剥掉再和裸文件名比对
NS_PREFIX_RE = re.compile(r"^\s*(?:文件|檔案|档案|图像|圖片|图片|Image|File)\s*:\s*", re.I)


def collect_from_raw(raw: Path) -> set[str]:
    """扫原始分片，收集正文里引用过的文件名（和 parse 侧同一套规则）。"""
    from kblib import cleantext

    names: set[str] = set()
    for shard in sorted(raw.glob("*.jsonl.gz")):
        with gzip.open(shard, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                wt = json.loads(line).get("wikitext", "")
                names.update(cleantext.extract_image_files(wt, limit=10**9))
    return names


def collect_from_db(db_path: Path) -> set[str]:
    import sqlite3

    if not db_path.exists():
        return set()
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT file FROM page_images")}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="图片文件名 → 直链（不下载图片）")
    ap.add_argument("--game", default=gamecfg.DEFAULT_GAME,
                    help=f"游戏配置名，默认 {gamecfg.DEFAULT_GAME}")
    ap.add_argument("--source", type=Path, default=None, help="raw 分片目录")
    ap.add_argument("--db", type=Path, default=None,
                    help="从 kb.db 的 page_images 表读清单（默认扫 raw 分片）")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--rps", type=float, default=None, help="默认用游戏配置的 rps")
    ap.add_argument("--limit", type=int, default=0, help="最多发多少个批次（调试）")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    cfg = gamecfg.load_game(args.game)
    source = args.source or cfg.raw_dir
    out = args.out or cfg.image_urls_path

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.db is not None:
        names = collect_from_db(args.db)
        print(f"[{cfg.label}] 从 {args.db} 读到 {len(names)} 个文件名")
    else:
        names = collect_from_raw(source)
        print(f"[{cfg.label}] 从 {source} 扫到 {len(names)} 个文件名")

    cache: dict[str, str] = {}
    if out.exists():
        try:
            cache = {k: v for k, v in json.loads(out.read_text(encoding="utf-8")).items() if v}
        except (json.JSONDecodeError, OSError):
            log.warning("旧缓存读不了，重新换")
    todo = sorted(n for n in names if n not in cache)
    print(f"缓存里已有 {len(cache)} 个，待换 {len(todo)} 个"
          f"（约 {(len(todo) + args.batch - 1) // args.batch} 个批次）")
    if not todo:
        return 0

    client = MediaWikiClient(rps=args.rps, game=cfg.name)
    t0 = time.time()
    done = 0
    for i in range(0, len(todo), args.batch):
        if args.limit and done >= args.limit:
            print(f"达到 --limit {args.limit} 批，停（缓存已存，下次接着跑）")
            break
        batch = todo[i : i + args.batch]
        try:
            q = client.query(
                prop="imageinfo", iiprop="url", iilimit="1",
                titles="|".join(FILE_PREFIX + n for n in batch),
                redirects="0",
            )
        except Exception as e:  # noqa: BLE001 - 单批失败不拖垮整轮，缓存已落盘
            log.warning("批次失败（%s），跳过 %d 个文件名", e, len(batch))
            continue
        # 问的是 File:原名，回来的 normalized/query 标题可能被本地化成"文件:"，
        # 按 from/to 对回来，再剥掉命名空间前缀和裸文件名比对
        norm = {p.get("to"): p.get("from") for p in q.get("normalized", [])}
        for p in q.get("pages", []):
            title = p.get("title", "")
            original = NS_PREFIX_RE.sub("", norm.get(title, title)).strip()
            if original not in names:
                continue
            ii = (p.get("imageinfo") or [{}])[0]
            url = ii.get("url")
            if url:
                cache[original] = url
        done += 1
        if done % 20 == 0:
            out.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i + len(batch)}/{len(todo)}，缓存 {len(cache)} 条"
                  f"（{time.time() - t0:.0f}s，{client.request_count} 次请求）")

    out.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"完成：{len(cache)}/{len(names)} 个文件名换到直链 → {out}"
          f"（{client.request_count} 次请求，{time.time() - t0:.0f}s）")
    missing = len(names) - len(cache)
    if missing:
        print(f"还有 {missing} 个没换到（文件被删/改名/权限），parse 时记 url=NULL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
