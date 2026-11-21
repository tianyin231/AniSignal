"""Steam 采集程序（纯匿名，零凭据零账号）。

信号集（2026-11-19 实测全部匿名可用）：
  游戏详情    store.steampowered.com/api/appdetails?appids=
  实时在线    api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/（无 key）
  聚合游玩    steamspy.com/api.php?request=appdetails（owners 估算/平均/中位时长）
  中文评价    store.steampowered.com/ajaxappreviews/{appid}?json=1&appids={appid}&start_offset=
             （appid 必须在路径+参数双写；含评价者 playtime/拥有游戏数/评测数）

刻意不使用 steamcommunity.com 的任何端点（档案/游戏库）：匿名已被登录墙/429 惩罚盒
双重封锁，且与采集目标无关。个人档案统计见 README「已知限制」。

限流纪律：reviews API 150 请求/5 分钟（约 30/min）；SteamSpy 请求间隔 ≥4s；
本程序默认每轮 4 游戏 × 6 请求，30 分钟一轮，请求间 2.5s。
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(os.environ.get("STEAM_HOME") or Path(__file__).resolve().parent)
RAW = ROOT / "raw"
RAW.mkdir(exist_ok=True)
STATE = ROOT / "steam_state.json"

BASE_STORE = "https://store.steampowered.com"
BASE_API = "https://api.steampowered.com"
BASE_SPY = "https://steamspy.com/api.php"

ROUND_INTERVAL = float(os.environ.get("STEAM_INTERVAL", "1800"))
SPACING = float(os.environ.get("STEAM_SPACING", "2.5"))
REVIEW_PAGES = int(os.environ.get("STEAM_REVIEW_PAGES", "3"))   # 每轮每游戏翻几页评价
REVIEW_PAGE_SIZE = 100                                        # API 上限
SKIP_SPY = os.environ.get("STEAM_SKIP_SPY", "").lower() in {"1", "true", "yes"}

# AniSignal 社区 -> (显示名, Steam appid)（2026-11-19 storesearch 解析；
# 崩铁/原神/方舟/碧蓝/NIKKE 不在 Steam，见 README）
GAMES = {
    "wuwa": ("鸣潮", 3513350),
    "zzz": ("绝区零", 4162040),
    "endfield": ("明日方舟：终末地", 4732690),
    "infinitynikki": ("无限暖暖", 3164330),
}


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"seen_reviews": {}, "last_round": 0, "rounds": 0, "errors": 0}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                                "AppleWebKit/537.36 Chrome/145.0.0.0 Safari/537.36",
                                  "Accept-Language": "zh-CN,zh;q=0.9"},
                        timeout=30, follow_redirects=True)


def _get_json(c, url: str, params: dict) -> dict | None:
    try:
        r = c.get(url, params=params)
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            return None
        data = r.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError):
        return None


def fetch_app_detail(c, appid: int) -> dict | None:
    payload = _get_json(c, f"{BASE_STORE}/api/appdetails", {"appids": appid, "l": "schinese"})
    return ((payload or {}).get(str(appid)) or {}).get("data")


def fetch_online(c, appid: int) -> int | None:
    payload = _get_json(c, f"{BASE_API}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", {"appid": appid})
    return ((payload or {}).get("response") or {}).get("player_count")


def fetch_spy(c, appid: int) -> dict | None:
    return _get_json(c, BASE_SPY, {"request": "appdetails", "appid": appid})


def fetch_reviews(c, appid: int, community: str, st: dict, ts: str) -> tuple[list[dict], dict | None]:
    """翻页拉最新评价，遇到已见 recommendationid 即停（增量），同时带回好评汇总。"""
    out, seen, dup, summary = [], st.setdefault("seen_reviews", {}), 0, None
    now = time.time()
    for page in range(REVIEW_PAGES):
        payload = _get_json(
            c,
            f"{BASE_STORE}/ajaxappreviews/{appid}",
            {"json": 1, "appids": appid, "language": "schinese", "filter": "recent",
             "review_type": "all", "purchase_type": "all",
             "num_per_page": REVIEW_PAGE_SIZE, "start_offset": page * REVIEW_PAGE_SIZE},
        )
        if payload is None:
            st["errors"] = st.get("errors", 0) + 1
            break
        if page == 0 and isinstance(payload.get("query_summary"), dict):
            qs = payload["query_summary"]
            summary = {
                "score_desc": qs.get("review_score_desc"),
                "total_positive": qs.get("total_positive"),
                "total_negative": qs.get("total_negative"),
            }
        reviews = payload.get("reviews") or []
        if not reviews:
            break
        fresh = []
        for rv in reviews:
            rid = str(rv.get("recommendationid") or "")
            if not rid:
                continue
            if rid in seen:
                dup += 1
                continue
            seen[rid] = now
            fresh.append(rv)
            a = rv.get("author") or {}
            out.append({
                "ts": ts, "community": community, "app_id": appid,
                "review_id": rid, "voted_up": bool(rv.get("voted_up")),
                "score_text": None, "text": (rv.get("review") or "")[:4000],
                "author": a.get("steamid"), "playtime_at_review": a.get("playtime_at_review"),
                "playtime_forever": a.get("playtime_forever"),
                "num_games_owned": a.get("num_games_owned"), "num_reviews": a.get("num_reviews"),
                "votes_up": rv.get("votes_up"), "votes_funny": rv.get("votes_funny"),
                "steam_purchase": rv.get("steam_purchase"), "received_for_free": rv.get("received_for_free"),
                "created": rv.get("timestamp_created"), "updated": rv.get("timestamp_updated"),
            })
        if not fresh:
            break  # 整页都见过，无需再翻
        time.sleep(SPACING)
    if out:
        with (RAW / "reviews.jsonl").open("a", encoding="utf-8") as f:
            for rec in out:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return out, summary


def collect():
    st = load_state()
    now = time.time()
    if now - st.get("last_round", 0) < ROUND_INTERVAL and "--once" not in sys.argv:
        print("未到轮次")
        return
    st["last_round"] = now
    st["rounds"] = st.get("rounds", 0) + 1
    ts = datetime.now(timezone.utc).isoformat()
    rec = {"ts": ts, "round": st["rounds"], "games": {}, "new_reviews": 0, "errors": []}

    with client() as c:
        for community, (name, appid) in GAMES.items():
            g = {"name": name, "app_id": appid}
            detail = fetch_app_detail(c, appid)
            if detail:
                g["price"] = (detail.get("price_overview") or {}).get("final_formatted")
                g["developers"] = detail.get("developers")
                g["release"] = (detail.get("release_date") or {}).get("date")
            else:
                rec["errors"].append(f"{community} detail")
            time.sleep(SPACING)
            g["online"] = fetch_online(c, appid)
            if g["online"] is None:
                rec["errors"].append(f"{community} online")
            with (RAW / "online.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": ts, "community": community, "app_id": appid,
                                    "online": g["online"]}, ensure_ascii=False) + "\n")
            time.sleep(SPACING)
            if not SKIP_SPY:
                spy = fetch_spy(c, appid)
                if spy:
                    g["spy"] = {"owners": spy.get("owners"), "average_forever": spy.get("average_forever"),
                                "median_forever": spy.get("median_forever"), "userscore": spy.get("userscore"),
                                "positive": spy.get("positive"), "negative": spy.get("negative")}
                    with (RAW / "spy.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": ts, "community": community, "app_id": appid, **g["spy"]},
                                           ensure_ascii=False) + "\n")
                else:
                    rec["errors"].append(f"{community} spy")
                time.sleep(SPACING)
            reviews, review_summary = fetch_reviews(c, appid, community, st, ts)
            if review_summary:
                g["review_summary"] = review_summary
            g["new_reviews"] = len(reviews)
            rec["new_reviews"] += len(reviews)
            rec["games"][community] = g
            print(f"  [{community}] {name}: 在线 {g['online']} | spy {g.get('spy', {}).get('median_forever')}min中位 | 新评价 {len(reviews)}")
            time.sleep(SPACING)

    cutoff = now - 7 * 86400
    st["seen_reviews"] = {k: v for k, v in st["seen_reviews"].items() if v > cutoff}
    save_state(st)
    with (RAW / "rounds.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"round {rec['round']}: 新评价 {rec['new_reviews']} | 错误 {len(rec['errors'])} {rec['errors'][:2]}")


if __name__ == "__main__":
    collect()
