"""验证实验最容易误导新闻判断的地方：原帖时间、引用归属、基线与断流。"""

import json

from experiments.ai_news import Store, bili_items, classify_x_response, iso, keyword_matches, parse_x, report


START = 1791601200
TARGET = {"key": "x:staff", "kind": "x_account", "value": "staff", "role": "team_member", "interval": 300}
CONFIG = {"targets": [TARGET], "keywords": {"Claude": [r"\bclaude\b"], "Codex": [r"\bcodex\b"]}}


def entry(number, text, timestamp=None, quote="", pinned=False, author="staff"):
    stamp = (f'<script>formatRelativePastTimestamp({{"timestamp":{timestamp * 1000}}})</script>'
             if timestamp else "")
    return (f'<div data-timeline-entry="" data-href="/{author}/status/{number}"><article>'
            f'{"Pinned" if pinned else ""}<div dir="auto" class="whitespace-pre-wrap">{text}</div>'
            f'{quote}{stamp}</article></div>')


def item(number, timestamp, text="Codex update", pinned=False):
    return parse_x(entry(number, text, timestamp, pinned=pinned))[0]


def test_quote_does_not_replace_author_time_or_keyword_text():
    quote = entry("99", "Claude is available", START - 500, author="someone_else")
    rows = parse_x(entry("100", "I have not verified this", START, quote=quote))
    assert len(rows) == 1
    assert rows[0]["author"] == "staff"
    assert rows[0]["published_at"] == iso(START)
    assert rows[0]["text"] == "I have not verified this"
    assert rows[0]["quoted_urls"] == ["https://x.com/someone_else/status/99"]


def test_full_post_uses_own_timestamp_not_earlier_quoted_timestamp():
    source = ('<meta property="og:url" content="https://x.com/staff/status/100">'
              '<article><div dir="auto" class="whitespace-pre-wrap">Complete Codex text.</div>'
              + entry("99", "Other announcement", START - 500, author="someone_else")
              + f'<script>formatFullTimestamp({{"timestamp":{START * 1000}}})</script></article>'
              + entry("101", "A reply", START + 50, author="reader"))
    rows = parse_x(source, "https://x.com/staff/status/100")
    assert len(rows) == 1
    assert rows[0]["published_at"] == iso(START)
    assert rows[0]["text"] == "Complete Codex text."
    assert parse_x(source, "https://x.com/staff/status/999") == []


def test_missing_publication_time_never_uses_collection_or_creation_time():
    assert parse_x(entry("100", "Codex"))[0]["published_at"] is None
    result = bili_items([{"bvid": "BV123", "title": "Codex", "ctime": START,
                          "fetched_at": iso(START)}], "bili_search")
    assert result[0]["published_at"] is None
    assert result[0]["text_scope"] == "search_excerpt"


def test_http_200_login_and_empty_shell_are_not_successful_searches():
    assert classify_x_response("https://x.com/i/jf/onboarding/web?mode=login", "", []) == "needs_auth"
    assert classify_x_response("https://x.com/search?q=Codex", "<html></html>", []) == "empty_or_changed"


def test_keywords_match_product_names_next_to_chinese_but_not_inside_words():
    assert keyword_matches("Codex更新了，Claude也有更新", CONFIG["keywords"]) == ["Claude", "Codex"]
    assert keyword_matches("codexification", CONFIG["keywords"]) == []


def test_baseline_duplicates_and_real_new_posts_have_separate_counts(tmp_path):
    store = Store(tmp_path / "observations.sqlite")
    store.set_state("experiment_started_at", iso(START))
    old = item("100", START - 86400, pinned=True)
    store.poll(TARGET, "ok", [old], START, {}, CONFIG)
    store.poll(TARGET, "ok", [old], START + 300, {}, CONFIG)
    new = item("101", START + 310)
    store.poll(TARGET, "ok", [old, new], START + 330, {}, CONFIG)
    data = report(store)
    assert data["unique_posts"] == 2
    assert data["baseline_posts"] == 1
    assert data["fresh_posts_after_baseline"] == 1
    assert data["latency_p50_seconds"] == 20
    assert store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2

    assert store.db.execute("SELECT verification FROM posts WHERE key='x:101'").fetchone()[0] == "pending"


def test_pinned_post_cannot_hide_a_possible_window_gap(tmp_path):
    store = Store(tmp_path / "observations.sqlite")
    pinned = item("1", START - 86400, pinned=True)
    store.poll(TARGET, "ok", [pinned, item("2", START)], START, {}, CONFIG)
    store.poll(TARGET, "ok", [pinned, item("8", START + 300)], START + 301, {}, CONFIG)
    assert report(store)["possible_window_gaps"] == 1


def test_failed_first_poll_does_not_turn_old_posts_into_new_news(tmp_path):
    store = Store(tmp_path / "observations.sqlite")
    store.poll(TARGET, "empty_or_changed", [], START, {}, CONFIG)
    store.poll(TARGET, "ok", [item("100", START - 86400)], START + 1800, {}, CONFIG)
    assert store.db.execute("SELECT baseline FROM posts").fetchone()[0] == 1


def test_full_text_survives_profile_repoll_and_raw_snapshots_are_preserved(tmp_path):
    store = Store(tmp_path / "observations.sqlite")
    short = item("100", START, "Codex update Show more")
    full = dict(short, text="Codex update with all conditions.", truncated=False, text_scope="full_post")
    detail = {"key": "detail:x:100", "kind": "x_detail", "value": full["url"], "interval": 3600}
    store.poll(TARGET, "ok", [short], START, {}, CONFIG)
    store.poll(detail, "ok", [full], START + 5, {}, CONFIG)
    store.poll(TARGET, "ok", [short], START + 300, {}, CONFIG)
    row = store.db.execute("SELECT * FROM posts").fetchone()
    assert row["text"] == full["text"]
    assert row["first_seen_at"] == iso(START)
    assert row["source_role"] == "team_member"
    assert store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2

    edited = dict(short, text="Correction: rollout is paused", truncated=True)
    store.poll(TARGET, "ok", [edited], START + 600, {}, CONFIG)
    row = store.db.execute("SELECT * FROM posts").fetchone()
    assert row["text"] == edited["text"]
    assert row["truncated"] == 1
    assert store.state("detail:x:100")["next_due"] == 0


def test_discovered_author_does_not_inherit_monitored_accounts_authority(tmp_path):
    store = Store(tmp_path / "observations.sqlite")
    quoted_author = dict(item("100", START), author="someone_else")
    store.poll(TARGET, "ok", [quoted_author], START, {}, CONFIG)
    row = store.db.execute("SELECT * FROM posts").fetchone()
    assert row["source_role"] == "unknown"
    assert json.loads(row["keywords"]) == ["Codex"]
    assert row["verification"] == "pending"
