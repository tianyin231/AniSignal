#!/usr/bin/env python3
"""阶段 1：抓取。分页拉全量条目 wikitext → data/<game>/raw/（gzip，不下载图片）。

用法：
    ./kb/.venv/bin/python fetch.py --spike 200      # spike：先拉 200 篇验证解析率
    ./kb/.venv/bin/python fetch.py                  # 全量（断点续拉，随时可打断）
    ./kb/.venv/bin/python fetch.py --limit 2000     # 只拉前 2000 篇（调试）
    ./kb/.venv/bin/python fetch.py --game arknights # 换游戏（配置见 games/*.yaml）
    tools/fetch_loop.sh --game arknights            # 反复续跑，直到 manifest 标记抓完

断点续拉按"磁盘上现有分片"自愈，不依赖 manifest：
  * manifest 丢了/写坏了 → 重启后扫分片重建断点，不会从头再拉；
  * 分片里有重复标题（历史遗留/并发写坏）→ 自动去重紧凑重写，不会覆盖旧分片；
  * spike 目录的同 schema 分片会并入正文分片集，不浪费已拉的 200 篇。

被 kill 不丢数据：分片和 manifest 都是先写临时文件再原子替换。
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import random
import shutil
import sys
import time
from pathlib import Path

from kblib import gamecfg
from kblib.wikiapi import MediaWikiClient

log = logging.getLogger("kb.fetch")


def write_shard(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)          # 原子替换：被 kill 也只会留下完整旧文件或完整新文件


def _scan(paths: list[Path], seen: set[str], first: set[tuple[int, int]],
          base_fi: int = 0) -> tuple[int, int, dict[Path, int]]:
    """扫一批分片，登记每个 title 的首次出现位置。

    返回 (有正文的行数, 坏行数, {文件: 行数})。first 里存 (文件序号, 行号)，
    重写阶段只保留这些位置的行，实现去重。
    """
    total = bad = 0
    counts: dict[Path, int] = {}
    for fi, p in enumerate(paths, start=base_fi):
        n = 0
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for li, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                total += 1
                n += 1
                try:
                    t = json.loads(line).get("title")
                except json.JSONDecodeError:
                    bad += 1
                    continue
                if t and t not in seen:
                    seen.add(t)
                    first.add((fi, li))
        counts[p] = n
    return total, bad, counts


def heal_shards(out_dir: Path, prefix: str, shard_size: int,
                extra_dirs: list[Path] | None = None) -> tuple[set[str], list[dict]]:
    """自愈：按磁盘上的分片重建断点状态，返回 (已完成标题, 分片信息列表)。

    分片信息用于重建 manifest 的 shards 段。只有发现重复标题/坏行、或
    extra_dirs 里有新页要并入时才重写文件，否则原样不动。
    """
    main_paths = sorted(out_dir.glob(f"{prefix}-*.jsonl.gz"))
    extra_paths: list[Path] = []
    for d in extra_dirs or []:
        if d.exists():
            extra_paths.extend(sorted(d.glob("*.jsonl.gz")))

    seen: set[str] = set()
    first: set[tuple[int, int]] = set()
    total, bad, counts = _scan(main_paths, seen, first)
    n_extra_new = 0
    if extra_paths:
        n_main_seen = len(seen)
        t2, bad2, _ = _scan(extra_paths, seen, first, base_fi=len(main_paths))
        n_extra_new = len(seen) - n_main_seen
        bad += bad2
    # 只在"正文分片自身有重复/坏行"或"extra 目录真有新页要并入"时才重写；
    # extra 里已有的标题不算重复，免得每轮都白翻工。
    if len(seen) - n_extra_new == total and n_extra_new == 0:
        # 干净状态：不碰文件
        return seen, [{"file": p.name, "pages": counts[p], "bytes": p.stat().st_size}
                      for p in main_paths]

    # 有重复/坏行/要并入：紧凑重写成 0..N-1 连续编号的新分片，再整体换班
    all_paths = main_paths + extra_paths
    tmp_dir = out_dir / ".heal"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    infos: list[dict] = []
    buf: list[dict] = []

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        name = f"{prefix}-{len(infos):04d}.jsonl.gz"
        path = tmp_dir / name
        write_shard(path, buf)
        infos.append({"file": name, "pages": len(buf), "bytes": path.stat().st_size})
        buf = []

    for fi, p in enumerate(all_paths):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for li, line in enumerate(f):
                if (fi, li) not in first:
                    continue
                buf.append(json.loads(line))
                if len(buf) >= shard_size:
                    flush()
    flush()
    for p in main_paths:
        p.unlink()
    for f in sorted(tmp_dir.glob(f"{prefix}-*.jsonl.gz")):
        f.replace(out_dir / f.name)
    tmp_dir.rmdir()
    log.info("分片自愈：正文 %d 行 → %d 篇（丢重复/坏行 %d，并入新篇 %d），重写成 %d 个分片",
             total, len(seen), total - (len(seen) - n_extra_new), n_extra_new, len(infos))
    return seen, infos


def crawl(
    client: MediaWikiClient,
    titles: list[str],
    out_dir: Path,
    shard_size: int,
    prefix: str = "pages",
    progress_every: int = 500,
    max_seconds: float = 0,
    extra_done_dirs: list[Path] | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    done_titles, shard_infos = heal_shards(out_dir, prefix, shard_size, extra_done_dirs)

    manifest: dict = {"prefix": prefix, "shard_size": shard_size, "shards": shard_infos}
    failed: set[str] = set()
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            failed = set(old.get("failed_titles", []))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("旧 manifest 读不了（%s），按分片重建断点", e)

    buf: list[dict] = []
    shard_idx = len(manifest["shards"])
    fetched = 0
    skipped = 0
    t0 = time.time()
    stopped_early = False
    step = client.batch_titles

    def save_manifest(finished: bool = False) -> None:
        manifest["pages"] = sum(s["pages"] for s in manifest["shards"])
        manifest["done"] = len(done_titles)
        manifest["failed"] = len(failed)
        manifest["requests"] = client.request_count
        manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if finished:
            # 完整列表只在收尾时写一次，省得每个批次都序列化几万个标题
            manifest["done_titles"] = sorted(done_titles)
            manifest["failed_titles"] = sorted(failed)
        tmp = manifest_path.parent / (manifest_path.name + ".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(manifest_path)   # 原子替换，kill 不留半截 JSON

    def flush() -> None:
        nonlocal buf, shard_idx
        if not buf:
            return
        path = out_dir / f"{prefix}-{shard_idx:04d}.jsonl.gz"
        write_shard(path, buf)
        manifest["shards"].append(
            {"file": path.name, "pages": len(buf), "bytes": path.stat().st_size}
        )
        buf = []
        shard_idx += 1
        save_manifest()

    for i in range(0, len(titles), step):
        if max_seconds and time.time() - t0 > max_seconds:
            log.info("到点（%.0fs），本轮收工，剩 %d 篇下次继续",
                     max_seconds, len(titles) - i)
            stopped_early = True
            break
        batch = titles[i : i + step]
        todo = [t for t in batch if t not in done_titles and t not in failed]
        if not todo:
            skipped += len(batch)
            continue
        recs, abandoned = client.fetch_revisions_safe(todo)
        failed.update(abandoned)
        done_titles.update(r["title"] for r in recs)
        # 请求成功但没正文的（缺失页/空页）也标记完成，避免每轮重拉
        done_titles.update(t for t in todo if t not in abandoned)
        buf.extend(recs)
        fetched += len(recs)
        save_manifest()          # 每个批次都落盘，方便看进度/断点续跑
        if len(buf) >= shard_size:
            flush()
        if fetched and fetched % progress_every < step:
            rate = fetched / max(time.time() - t0, 1e-6)
            log.info("已抓 %d 篇（%.0f 篇/s，%d 次请求）", fetched, rate, client.request_count)
    flush()

    manifest.update(
        {
            "pages": sum(s["pages"] for s in manifest["shards"]),
            "titles_total": len(titles),
            "titles_skipped": skipped,
            "requests": client.request_count,
            "bytes": sum(s["bytes"] for s in manifest["shards"]),
            "elapsed_sec": round(time.time() - t0, 1),
            "finished_at": None if stopped_early else time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )
    save_manifest(finished=True)
    return manifest


def load_titles(client: MediaWikiClient, cache: Path) -> list[str]:
    """标题列表缓存到本地，断点续跑时不用再拉一遍。"""
    if cache.exists():
        titles = json.loads(cache.read_text(encoding="utf-8"))
        log.info("复用标题缓存 %s（%d 篇）", cache, len(titles))
        return titles
    titles = list(client.iter_all_titles())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(titles, ensure_ascii=False), encoding="utf-8")
    return titles


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取 wiki 全量 wikitext（一游戏一配置）")
    ap.add_argument("--game", default=None,
                    help=f"游戏配置 kb/games/<name>.yaml（默认 {gamecfg.DEFAULT_GAME}；"
                         f"现有：{', '.join(gamecfg.available_games())}）")
    ap.add_argument("--spike", type=int, metavar="N", help="spike 模式：只拉 N 篇（随机抽样）")
    ap.add_argument("--limit", type=int, default=0, help="最多拉多少篇（调试用）")
    ap.add_argument("--rps", type=float, default=None, help="每秒请求数上限（默认用配置值）")
    ap.add_argument("--batch", type=int, default=None, help="单批标题数（默认用配置值）")
    ap.add_argument("--timeout", type=int, default=None, help="单次请求超时秒数（默认用配置值）")
    ap.add_argument("--retries", type=int, default=None, help="重试次数（默认用配置值）")
    ap.add_argument("--shard-size", type=int, default=None,
                    help="每分片页数（默认用配置值）")
    ap.add_argument("--seed", type=int, default=20261018, help="spike 抽样随机种子")
    ap.add_argument("--titles-cache", type=Path, default=None,
                    help="标题列表缓存路径（默认 <game>/raw/titles.json）")
    ap.add_argument("--max-seconds", type=float, default=0,
                    help="本轮最多跑多少秒（到点干净退出，方便反复续跑）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = gamecfg.load_game(args.game)
    client = MediaWikiClient(rps=args.rps, timeout=args.timeout,
                             max_retries=args.retries, batch_titles=args.batch, game=cfg.name)
    log.info("列出全部条目标题…")
    titles = load_titles(client, args.titles_cache or cfg.titles_cache)
    if not titles:
        log.error("没拿到任何标题")
        return 1
    log.info("%s：共 %d 篇条目（%s）", cfg.label, len(titles), cfg.wiki_base)

    if args.spike:
        random.seed(args.seed)
        sample = random.sample(titles, min(args.spike, len(titles)))
        sample.sort()
        out_dir = cfg.spike_dir
        manifest = crawl(client, sample, out_dir,
                         shard_size=args.shard_size or cfg.shard_pages, prefix="spike")
        (out_dir / "sample-titles.json").write_text(
            json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"spike 抓取完成：{manifest['pages']} 篇 → {out_dir}（{manifest['requests']} 次请求）")
    else:
        use = titles[: args.limit] if args.limit else titles
        manifest = crawl(client, use, cfg.raw_dir,
                         shard_size=args.shard_size or cfg.shard_pages,
                         max_seconds=args.max_seconds, extra_done_dirs=[cfg.spike_dir])
        print(f"本轮抓取：{manifest['pages']}/{len(use)} 篇 → {cfg.raw_dir}"
              f"（{manifest['requests']} 次请求，{manifest['elapsed_sec']}s）")
        if manifest["failed"]:
            print(f"放弃 {manifest['failed']} 篇（见 manifest.failed_titles）")
        if manifest["finished_at"] is None:
            print("还没抓完，再跑一遍同一命令即可续跑")

    print(f"数据量：{manifest['bytes'] / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
