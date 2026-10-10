"""AI 新闻采集实验：只写独立 SQLite，不接事件引擎和通知队列。

python experiments/ai_news.py run --state-dir /var/lib/anisignal/ai-news --hours 24
python experiments/ai_news.py report --state-dir /var/lib/anisignal/ai-news
首轮只建立基线；候选来源身份与消息真实性分别保存，所有消息均待核实。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "AniSignal/0.1 (public news collection experiment)"
VOID = set("area base br col embed hr img input link meta param source track wbr".split())
STATUS_PATH = re.compile(r"/([A-Za-z0-9_]+)/status/(\d+)")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class Node:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def walk(self, *, own=False):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                if own and "data-timeline-entry" in child.attrs:
                    continue
                yield from child.walk(own=own)

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children
                       if isinstance(c, str) or c.tag not in {"script", "style"})


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.root = Node()
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def x_item(node, path):
    match = STATUS_PATH.fullmatch(path)
    if not match:
        return None
    own = list(node.walk(own=True))
    bodies = [n.text() for n in own if n.attrs.get("dir") == "auto"
              and "whitespace-pre-wrap" in n.attrs.get("class", "")]
    scripts = ["".join(c for c in n.children if isinstance(c, str))
               for n in own if n.tag == "script"]
    dates = [re.search(r'"timestamp":(\d+)', s) for s in scripts
             if "formatRelativePastTimestamp" in s or "formatFullTimestamp" in s]
    published = next((int(m[1]) / 1000 for m in dates if m), None)
    text = bodies[0] if bodies else ""
    quotes = ["https://x.com" + n.attrs["data-href"] for n in node.walk()
              if n is not node and STATUS_PATH.fullmatch(n.attrs.get("data-href", ""))]
    links = [n.attrs["href"] for n in own if n.tag == "a"
             and n.attrs.get("href", "").startswith(("https://", "http://"))]
    return {"platform": "x", "id": match[2], "author": match[1],
            "url": "https://x.com" + path, "published_at": iso(published) if published else None,
            "text": text.removesuffix("Show more").rstrip(), "truncated": "Show more" in text,
            "pinned": node.text().startswith("Pinned"), "quoted_urls": list(dict.fromkeys(quotes)),
            "links": list(dict.fromkeys(links)), "text_scope": "post"}


def parse_x(source, post_url=None):
    doc = Document(source)
    if post_url:
        path = urllib.parse.urlsplit(post_url).path
        canonical = next((n.attrs.get("content", "") for n in doc.root.walk()
                          if n.tag == "meta" and n.attrs.get("property") == "og:url"), "")
        # 单帖页首篇 article 才是正文；下面的引用帖和回复不能冒充原帖。
        if urllib.parse.urlsplit(canonical).path != path:
            return []
        article = next((n for n in doc.root.walk() if n.tag == "article"), None)
        item = x_item(article, path) if article else None
        return [item] if item else []
    rows = []
    for node in doc.root.walk():
        if "data-timeline-entry" not in node.attrs:
            continue
        parent = node.parent
        while parent and "data-timeline-entry" not in parent.attrs:
            parent = parent.parent
        if parent is None:
            item = x_item(node, node.attrs.get("data-href", ""))
            if item:
                rows.append(item)
    return list({r["id"]: r for r in rows}.values())


def classify_x_response(final_url, source, rows):
    path = urllib.parse.urlsplit(final_url).path
    if path.startswith(("/i/jf/onboarding", "/i/flow/login", "/login")):
        return "needs_auth"
    if rows:
        return "ok"
    # HTTP 200 的空壳、登录页或格式变化均不能记成健康的“没有更新”。
    return "empty_or_changed"


def bili_items(payload, kind):
    rows = payload if isinstance(payload, list) else payload.get("items", [])
    result = []
    for row in rows:
        dynamic = kind == "bili_account"
        external_id = str(row.get("id") if dynamic else row.get("bvid") or "")
        if not external_id or external_id == "None":
            continue
        stamp = row.get("pub_ts") if dynamic else row.get("pubdate")
        text = row.get("text", "") if dynamic else "\n".join(
            dict.fromkeys(str(row.get(k) or "") for k in ("title", "description")))
        text = html.unescape(re.sub(r"</?em[^>]*>", "", text)).strip()
        result.append({"platform": "bilibili", "id": external_id,
                       "author": str(row.get("author_mid") if dynamic else row.get("owner_mid") or row.get("mid") or ""),
                       "author_name": row.get("author_name") if dynamic else row.get("owner_name") or row.get("author"),
                       "url": ("https://t.bilibili.com/" if dynamic else "https://www.bilibili.com/video/") + external_id,
                       "published_at": iso(stamp) if stamp else None,
                       "text": text, "truncated": False, "pinned": False, "quoted_urls": [],
                       "links": re.findall(r'https?://[^\s<>"]+', text),
                       # 搜索简介可能截断，绝不能当成已读完视频或字幕。
                       "text_scope": "dynamic" if dynamic else "search_excerpt"})
    return result


def keyword_matches(text, keywords):
    # 中文常紧挨英文产品名（如“Codex更新”）；单词边界只按 ASCII 判断。
    return [name for name, patterns in keywords.items()
            if any(re.search(pattern, text, re.IGNORECASE | re.ASCII) for pattern in patterns)]


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS posts (
            key TEXT PRIMARY KEY, platform TEXT, author TEXT, source_role TEXT, url TEXT,
            published_at TEXT, first_seen_at TEXT, last_seen_at TEXT, baseline INTEGER,
            text TEXT, truncated INTEGER, keywords TEXT, payload TEXT,
            verification TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS snapshots (
            post_key TEXT, digest TEXT, observed_at TEXT, payload TEXT,
            PRIMARY KEY(post_key, digest));
        CREATE TABLE IF NOT EXISTS discoveries (
            post_key TEXT, target_key TEXT, discovered_at TEXT,
            PRIMARY KEY(post_key, target_key));
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY, at TEXT, target_key TEXT, kind TEXT, status TEXT,
            item_count INTEGER, new_count INTEGER, changed_count INTEGER, detail TEXT);
        """)

    def state(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, value):
        self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    def record(self, items, target, now, baseline, config):
        new_count = changed = 0
        roles = {(t["kind"].split("_")[0], t["value"].lower()): t.get("role", "unknown")
                 for t in config["targets"] if t["kind"] in {"x_account", "bili_account"}}
        for item in items:
            item = dict(item)
            key = item["platform"] + ":" + item["id"]
            old = self.db.execute("SELECT * FROM posts WHERE key=?", (key,)).fetchone()
            self.db.execute("INSERT OR IGNORE INTO discoveries VALUES (?,?,?)", (key, target["key"], iso(now)))
            # 原始解析快照只追加；主页缩略内容不能覆盖已补全的单帖正文。
            payload = json.dumps(item, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            self.db.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)", (key, digest, iso(now), payload))
            if old and target["kind"] != "x_detail" and item["platform"] == "x":
                previous = json.loads(old["payload"])
                if previous.get("text_scope") == "full_post" and item["truncated"]:
                    if previous["text"].startswith(item["text"]):
                        item.update(text=previous["text"], truncated=False, text_scope="full_post")
                    else:
                        # 原帖可能已编辑：保留新快照，并重新核对全文，不能一直沿用旧正文。
                        detail_key = "detail:" + key
                        detail_state = self.state(detail_key, {})
                        detail_state["next_due"] = 0
                        self.set_state(detail_key, detail_state)
            source_platform = "x" if item["platform"] == "x" else "bili"
            role = roles.get((source_platform, item["author"].lower()), "unknown")
            matched = keyword_matches(item["text"], config["keywords"])
            if old:
                changed += old["text"] != item["text"]
            else:
                new_count += 1
            values = (key, item["platform"], item["author"], role, item["url"], item["published_at"],
                      iso(now), iso(now), int(baseline), item["text"], int(item["truncated"]),
                      json.dumps(matched, ensure_ascii=False), json.dumps(item, ensure_ascii=False))
            self.db.execute("""INSERT INTO posts
                (key,platform,author,source_role,url,published_at,first_seen_at,last_seen_at,baseline,text,truncated,keywords,payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                last_seen_at=excluded.last_seen_at, text=excluded.text, truncated=excluded.truncated,
                keywords=excluded.keywords, payload=excluded.payload,
                published_at=COALESCE(excluded.published_at,posts.published_at)""", values)
        self.db.commit()
        return new_count, changed

    def poll(self, target, status, items, now, detail, config):
        key = target["key"]
        state = self.state(key, {})
        new = changed = 0
        if status == "ok":
            baseline = not state.get("initialized", False)
            new, changed = self.record(items, target, now, baseline, config)
            ids = {x["id"] for x in items if not x.get("pinned")}
            previous = set(state.get("ids", []))
            if target["kind"] == "x_account" and previous and ids and not previous.intersection(ids):
                detail["possible_window_gap"] = True
            state.update(initialized=True, ids=sorted(ids), last_success=iso(now))
            detail["baseline"] = baseline
        delay = target["interval"] if status == "ok" else max(target["interval"], 1800)
        if status == "needs_auth":
            delay = 86400
        state.update(next_due=now + delay, last_status=status)
        self.set_state(key, state)
        self.db.execute("INSERT INTO polls(at,target_key,kind,status,item_count,new_count,changed_count,detail) VALUES (?,?,?,?,?,?,?,?)",
                        (iso(now), key, target["kind"], status, len(items), new, changed, json.dumps(detail, ensure_ascii=False)))
        self.db.commit()
        log = dict(at=iso(now), target=key, status=status, items=len(items), new=new, changed=changed, **detail)
        print(json.dumps(log, ensure_ascii=False), flush=True)


def fetch(target, config):
    kind, value = target["kind"], target["value"]
    if kind.startswith("x_"):
        url = "https://x.com/" + value if kind == "x_account" else value
        if kind == "x_search":
            url = "https://x.com/search?" + urllib.parse.urlencode({"q": value, "src": "typed_query", "f": "live"})
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            source = response.read().decode("utf-8", errors="replace")
            final_url = response.url
        rows = parse_x(source, value if kind == "x_detail" else None)
        if kind == "x_detail":
            for row in rows:
                row["text_scope"] = "full_post"
        return classify_x_response(final_url, source, rows), rows, {"http": 200, "final_url": final_url}
    command = list(config["bili_command"])
    if kind == "bili_search":
        command += ["search", value, "--type", "video", "--order", "pubdate"]
    else:
        command += ["dynamics", value]
    command += ["-n", "15", "-o", "json", "--no-cache", "--retries", "0"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=50)
    if result.returncode:
        status = "refused" if result.returncode in {4, 6} else "error"
        return status, [], {"exit_code": result.returncode, "error": result.stderr[-800:]}
    rows = bili_items(json.loads(result.stdout), kind)
    return ("ok" if rows else "empty_or_changed"), rows, {"order": "pubdate" if kind == "bili_search" else "account"}


def attempt(target, store, config):
    now = time.time()
    # B站动态与搜索是不同端点；某个动态流拒绝访问不应停掉仍可读的搜索。
    family = "x" if target["kind"].startswith("x_") else target["kind"]
    if store.state("cooldown:" + family, 0) > now:
        return False
    try:
        status, rows, detail = fetch(target, config)
    except urllib.error.HTTPError as error:
        status, rows = "refused" if error.code in {401, 403, 429} else "error", []
        detail = {"http": error.code}
        retry = error.headers.get("Retry-After", "")
        if retry.isdigit():
            store.set_state("cooldown:" + family, now + max(1800, int(retry)))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        status, rows, detail = "error", [], {"error": str(error)[:500]}
    observed = time.time()
    if status == "refused":
        store.set_state("cooldown:" + family, max(store.state("cooldown:" + family, 0), observed + 1800))
    store.poll(target, status, rows, observed, detail, config)
    time.sleep(config.get("request_delay_seconds", 3))
    return True


def report(store):
    start = store.state("experiment_started_at", iso(time.time()))
    posts = store.db.execute("SELECT * FROM posts").fetchall()
    fresh = [r for r in posts if not r["baseline"] and r["published_at"] and r["published_at"] >= start]
    delays = sorted((datetime.fromisoformat(r["first_seen_at"]) - datetime.fromisoformat(r["published_at"])).total_seconds()
                    for r in fresh)
    delays = [x for x in delays if x >= 0]
    polls = [dict(r) for r in store.db.execute("""SELECT target_key,kind,status,COUNT(*) AS polls,
              SUM(new_count) AS discovered,SUM(changed_count) AS changed,MAX(at) AS latest_poll
              FROM polls GROUP BY target_key,kind,status""")]
    gaps = store.db.execute("SELECT COUNT(*) FROM polls WHERE detail LIKE '%\"possible_window_gap\": true%'").fetchone()[0]
    latest = sorted(posts, key=lambda r: r["first_seen_at"], reverse=True)[:15]
    return {"started_at": start, "generated_at": iso(time.time()), "ends_at": store.state("experiment_ends_at"),
            "unique_posts": len(posts), "baseline_posts": sum(bool(r["baseline"]) for r in posts),
            "keyword_matched_posts": sum(r["keywords"] != "[]" for r in posts),
            "fresh_posts_after_baseline": len(fresh), "possible_window_gaps": gaps,
            "latency_sample_count": len(delays), "latency_p50_seconds": delays[len(delays) // 2] if delays else None,
            "latency_max_seconds": max(delays) if delays else None,
            "verification": "全部候选待核实；无窗口断层不等于证明没有漏帖；搜索简介不等于视频全文",
            "polls": polls, "latest": [{k: r[k] for k in ("key", "author", "source_role", "url", "published_at", "first_seen_at", "baseline", "text", "keywords", "verification")} for r in latest]}


def run(store, config, state_dir, hours, once=False):
    now = time.time()
    if not store.state("experiment_started_at"):
        store.set_state("experiment_started_at", iso(now))
        store.set_state("experiment_ends_at", iso(now + hours * 3600))
    deadline = datetime.fromisoformat(store.state("experiment_ends_at")).timestamp()
    while time.time() < deadline:
        for target in config["targets"]:
            if time.time() >= deadline:
                break
            if time.time() >= store.state(target["key"], {}).get("next_due", 0):
                attempt(target, store, config)
        # 每轮只补少量近期长帖；失败保持待核实，不凭被截断的半句话下结论。
        recent = iso(time.time() - 86400)
        pending = store.db.execute("SELECT * FROM posts WHERE platform='x' AND (truncated=1 OR text='') AND published_at>=? ORDER BY first_seen_at DESC", (recent,)).fetchall()
        used = 0
        for row in pending:
            target = {"key": "detail:" + row["key"], "kind": "x_detail", "value": row["url"], "interval": 3600}
            if time.time() >= deadline or used >= config.get("detail_limit_per_cycle", 2):
                break
            if time.time() >= store.state(target["key"], {}).get("next_due", 0):
                used += bool(attempt(target, store, config))
        destination = state_dir / "summary.json"
        temporary = state_dir / "summary.tmp"
        temporary.write_text(json.dumps(report(store), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)
        if once:
            break
        time.sleep(min(30, max(0, deadline - time.time())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "report"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("ai_news_sources.json"))
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    store = Store(args.state_dir / "observations.sqlite")
    if args.action == "report":
        print(json.dumps(report(store), ensure_ascii=False, indent=2))
    else:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        run(store, config, args.state_dir, args.hours, args.once)


if __name__ == "__main__":
    main()
