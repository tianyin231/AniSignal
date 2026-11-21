"""运维日报：探针 + 轮次 + 在线曲线 + Spy 快照 + 评价样本。"""

import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("STEAM_HOME") or Path(__file__).resolve().parent)


def read_jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def sec(t):
    print(f"\n{'=' * 18} {t} {'=' * 18}")


def main():
    sec("端点可用率（探针）")
    probes = read_jsonl(ROOT / "stability" / "log.jsonl")
    for surf in ("detail", "online", "spy", "reviews"):
        obs = [p.get(surf) for p in probes if isinstance(p.get(surf), dict)]
        if obs:
            ok = sum(1 for o in obs if o.get("status") == "ok")
            lat = [o.get("ms", 0) for o in obs]
            print(f"  {surf:<9} {ok}/{len(obs)}  延迟avg {sum(lat)//len(lat) if lat else 0}ms")

    sec("采集轮次")
    rounds = read_jsonl(ROOT / "raw" / "rounds.jsonl")
    if rounds:
        total = sum(r.get("new_reviews", 0) for r in rounds)
        errs = sum(len(r.get("errors") or []) for r in rounds)
        print(f"  轮次 {len(rounds)} | 累计新评价 {total} | 错误 {errs}")
        last = rounds[-1]
        for community, g in (last.get("games") or {}).items():
            spy = g.get("spy") or {}
            print(f"  {community}: 在线 {g.get('online')} | 中位时长 {spy.get('median_forever')}min | "
                  f"均时长 {spy.get('average_forever')}min | owners {spy.get('owners')} | 新评价 {g.get('new_reviews')}")

    sec("在线人数曲线（最近 8 个点）")
    online = read_jsonl(ROOT / "raw" / "online.jsonl")[-8:]
    for o in online:
        print(f"  {o['ts'][:16]} {o['community']}: {o['online']}")

    sec("最新评价样本")
    for r in read_jsonl(ROOT / "raw" / "reviews.jsonl")[-5:]:
        tag = "推荐" if r.get("voted_up") else "不推荐"
        pt = (r.get("playtime_at_review") or 0) / 60
        print(f"  [{r['community']}] {tag} {pt:.0f}h 拥有{r.get('num_games_owned')}款: {(r.get('text') or '')[:50]}")


if __name__ == "__main__":
    main()
