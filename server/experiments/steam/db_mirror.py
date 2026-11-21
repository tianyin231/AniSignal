"""SQLite 镜像：raw/*.jsonl -> steam.db（幂等，唯一键去重）。"""

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(os.environ.get("STEAM_HOME") or Path(__file__).resolve().parent)
DB = ROOT / "steam.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games(
  community TEXT PRIMARY KEY, name TEXT, app_id INT, developers TEXT, release TEXT);
CREATE TABLE IF NOT EXISTS online(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, community TEXT, app_id INT, online INT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_online ON online(ts, community);
CREATE TABLE IF NOT EXISTS spy(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, community TEXT, app_id INT,
  owners TEXT, average_forever INT, median_forever INT, userscore INT,
  positive INT, negative INT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_spy ON spy(ts, community);
CREATE TABLE IF NOT EXISTS reviews(
  review_id TEXT PRIMARY KEY, community TEXT, ts TEXT, app_id INT, voted_up INT,
  text TEXT, author TEXT, playtime_at_review INT, playtime_forever INT,
  num_games_owned INT, num_reviews INT, votes_up INT, votes_funny INT,
  created INT, updated INT);
CREATE TABLE IF NOT EXISTS rounds(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, round INT, new_reviews INT, errors TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rounds ON rounds(ts);
CREATE TABLE IF NOT EXISTS probes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, detail TEXT, online TEXT, spy TEXT, reviews TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_probes ON probes(ts);
"""


def build():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    raw = ROOT / "raw"
    n = {"online": 0, "spy": 0, "reviews": 0, "rounds": 0, "probes": 0}

    def jl(name):
        p = raw / name
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                yield json.loads(line)
            except ValueError:
                continue

    for r in jl("online.jsonl"):
        cur = con.execute("INSERT OR IGNORE INTO online(ts,community,app_id,online) VALUES (?,?,?,?)",
                          (r["ts"], r["community"], r["app_id"], r["online"]))
        n["online"] += cur.rowcount
    for r in jl("spy.jsonl"):
        cur = con.execute("INSERT OR IGNORE INTO spy(ts,community,app_id,owners,average_forever,median_forever,userscore,positive,negative) VALUES (?,?,?,?,?,?,?,?,?)",
                          (r["ts"], r["community"], r["app_id"], r.get("owners"),
                           r.get("average_forever"), r.get("median_forever"), r.get("userscore"),
                           r.get("positive"), r.get("negative")))
        n["spy"] += cur.rowcount
    for r in jl("reviews.jsonl"):
        cur = con.execute("INSERT OR IGNORE INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (r["review_id"], r["community"], r["ts"], r["app_id"], int(bool(r["voted_up"])),
                           r["text"], r.get("author"), r.get("playtime_at_review"), r.get("playtime_forever"),
                           r.get("num_games_owned"), r.get("num_reviews"), r.get("votes_up"),
                           r.get("votes_funny"), r.get("created"), r.get("updated")))
        n["reviews"] += cur.rowcount
    for r in jl("rounds.jsonl"):
        cur = con.execute("INSERT OR IGNORE INTO rounds(ts,round,new_reviews,errors) VALUES (?,?,?,?)",
                          (r["ts"], r["round"], r["new_reviews"],
                           json.dumps(r.get("errors"), ensure_ascii=False)[:200]))
        n["rounds"] += cur.rowcount
        for community, g in (r.get("games") or {}).items():
            con.execute("INSERT INTO games(community,name,app_id) VALUES (?,?,?) "
                        "ON CONFLICT(community) DO UPDATE SET name=excluded.name, app_id=excluded.app_id",
                        (community, g.get("name"), g.get("app_id")))
    p = ROOT / "stability" / "log.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            vals = [r.get(k) if not isinstance(r.get(k), dict) else r[k].get("status") for k in ("ts", "detail", "online", "spy", "reviews")]
            cur = con.execute("INSERT OR IGNORE INTO probes(ts,detail,online,spy,reviews) VALUES (?,?,?,?,?)", vals)
            n["probes"] += cur.rowcount
    con.commit()
    print(f"mirror: 新增 {n}")
    con.close()


def query(sql):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql)]
    con.close()
    return rows


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "stats":
        for r in query("SELECT community, count(*) n, sum(voted_up) pos, round(avg(playtime_at_review)/60,1) avg_h FROM reviews GROUP BY community"):
            print(f"  评价 {r['community']}: {r['n']} 条 | 好评 {r['pos']} | 评价者均时长 {r['avg_h']}h")
        for r in query("SELECT community, online, ts FROM online ORDER BY id DESC LIMIT 4"):
            print(f"  在线 {r['community']}: {r['online']} @ {r['ts'][:16]}")
        for r in query("SELECT community, median_forever, owners FROM spy ORDER BY id DESC LIMIT 4"):
            print(f"  Spy {r['community']}: 中位 {r['median_forever']}min | owners {r['owners']}")
    elif len(sys.argv) >= 2 and sys.argv[1] == "latest":
        for r in query("SELECT community, voted_up, substr(text,1,60) t, playtime_at_review p FROM reviews ORDER BY created DESC LIMIT 10"):
            hours = (r["p"] or 0) / 60
            print(f"  [{r['community']}] {'推荐' if r['voted_up'] else '不推荐'} {hours:.1f}h: {r['t']}")
    elif len(sys.argv) >= 3 and sys.argv[1] == "sql":
        for r in query(sys.argv[2]):
            print("  " + " | ".join(f"{k}={str(v)[:50]}" for k, v in r.items()))
    else:
        build()


if __name__ == "__main__":
    main()
