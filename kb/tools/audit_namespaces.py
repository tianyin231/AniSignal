"""v3 覆盖率审计：命名空间清单 + 各命名空间非重定向篇数。

用法：./.venv/bin/python tools/audit_namespaces.py [--out audit_ns.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib.wikiapi import MediaWikiClient  # noqa: E402

OUT = Path(__file__).resolve().parent / "audit_namespaces.json"


def count_ns(client: MediaWikiClient, ns: int) -> int:
    """分页数一遍该命名空间下的非重定向页数（不落标题，只计数）。"""
    n = 0
    cont: dict = {}
    while True:
        params = {
            "action": "query", "list": "allpages", "apnamespace": ns,
            "aplimit": 500, "apfilterredir": "nonredirects", **cont,
        }
        data = client._raw(params)
        n += len(data.get("query", {}).get("allpages", []))
        if "continue" not in data:
            return n
        cont = data["continue"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    client = MediaWikiClient(rps=1.5)

    info = client._raw({"action": "query", "meta": "siteinfo",
                        "siprop": "statistics|namespaces", "format": "json",
                        "formatversion": "2"})
    namespaces = info["query"]["namespaces"]
    stats = info["query"]["statistics"]

    # 只数"可能有内容"的命名空间：content=True 的 + 项目/帮助/概念
    interesting = [ns for ns in namespaces.values()
                   if ns.get("content") or ns["id"] in (4, 5, 12, 13, 108, 102, 274, 828)]
    interesting.sort(key=lambda n: n["id"])

    print(f"wiki statistics: articles={stats.get('articles')} pages={stats.get('pages')}")
    print(f"{'ns':>6} {'canonical':<22} {'name':<14} count")
    rows = []
    for ns in interesting:
        n = count_ns(client, ns["id"])
        rows.append({"id": ns["id"], "name": ns.get("name", ""),
                     "canonical": ns.get("canonical", ""), "nonredirect_pages": n})
        print(f"{ns['id']:>6} {ns.get('canonical',''):<22} {ns.get('name',''):<14} {n}")
        time.sleep(0.2)

    args.out.write_text(json.dumps(
        {"statistics": stats, "namespaces": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
