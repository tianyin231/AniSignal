"""GitHub Trending 分期刊物（日刊/周刊/月刊）解析、跨期去重打标、采集归档与公开接口端到端测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.collectors.git_repo import (
    normalize_trending_target,
    parse_github_trending_html,
    parse_trending_target,
)
from app.models import GitTrendingEdition, RawContent
from app.services.collect import run_collection
from app.services.git_trending import (
    get_trending_editions_view,
    save_trending_edition,
    trending_period_key,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value, ex=None, px=None, nx: bool = False):
        if key.startswith("anisignal:politeness:"):
            return True
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def pttl(self, key: str):
        return 0

    async def delete(self, *keys: str):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    async def release_if_match(self, key: str, token: str):
        if self.store.get(key) == token:
            self.store.pop(key, None)


def _trending_html(rows: list[tuple[str, str, str, int, int, int, str]]) -> str:
    articles = []
    for repo, desc, lang, stars, forks, gain, gain_unit in rows:
        lang_html = f'<span itemprop="programmingLanguage">{lang}</span>' if lang else ""
        articles.append(
            f"""
            <article class="Box-row">
              <h2><a href="/{repo}">{repo}</a></h2>
              <p>{desc}</p>
              <div>
                {lang_html}
                <a class="Link--muted" href="/{repo}/stargazers">{stars:,}</a>
                <a class="Link--muted" href="/{repo}/forks">{forks:,}</a>
                <span class="float-sm-right">{gain:,} stars {gain_unit}</span>
              </div>
            </article>
            """
        )
    return "<!doctype html><html><body>" + "\n".join(articles) + "</body></html>"


def _make_response(url: str, *, status_code: int = 200, text_body: str = "", json_body=None) -> httpx.Response:
    req = httpx.Request("GET", url)
    if json_body is not None:
        return httpx.Response(
            status_code=status_code,
            content=json.dumps(json_body, ensure_ascii=False).encode("utf-8"),
            headers={"content-type": "application/json"},
            request=req,
        )
    return httpx.Response(
        status_code=status_code,
        content=text_body.encode("utf-8"),
        headers={"content-type": "text/html; charset=utf-8"},
        request=req,
    )


# ---------- 1. 目标归一化、HTML 解析与北京时间期次键 ----------


def test_trending_target_normalization_and_html_parse():
    assert normalize_trending_target("daily") == "daily"
    assert normalize_trending_target("WEEKLY:Python") == "weekly:python"
    assert normalize_trending_target("https://github.com/trending?since=monthly") == "monthly"
    assert normalize_trending_target("https://github.com/trending/rust?since=weekly") == "weekly:rust"
    assert parse_trending_target("daily:typescript") == ("daily", "typescript")

    with pytest.raises(ValueError, match="daily / weekly / monthly"):
        normalize_trending_target("yearly")
    with pytest.raises(ValueError):
        normalize_trending_target("https://example.com/trending")

    # 北京时间跨日边界：UTC 17:00 = 北京次日 01:00
    utc_late = datetime(2026, 11, 26, 17, 0, 0)
    assert trending_period_key("daily", utc_late) == "2026-11-27"
    assert trending_period_key("monthly", utc_late) == "2026-11"
    assert trending_period_key("weekly", utc_late).startswith("2026-W")

    html = _trending_html(
        [
            ("acme/alpha", "Fast AI tool", "Python", 12340, 890, 1450, "today"),
            ("acme/beta", "Web toolkit", "TypeScript", 5200, 310, 620, "today"),
        ]
    )
    raw_items, entries = parse_github_trending_html(html, period="daily")
    assert len(raw_items) == 2
    assert len(entries) == 2
    assert entries[0]["rank"] == 1
    assert entries[0]["repo"] == "acme/alpha"
    assert entries[0]["stars"] == 12340
    assert entries[0]["forks"] == 890
    assert entries[0]["period_stars"] == 1450
    assert raw_items[0]["id"] == "acme/alpha:trending:repo"


# ---------- 2. 跨期去重、首次上榜 / 连榜 / 复榜 / 跨榜标记计算 ----------


async def test_trending_cross_edition_dedupe_and_badges(session_factory):
    base = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    day1 = base - timedelta(days=2)
    day2 = base - timedelta(days=1)
    day3 = base

    async with session_factory() as s:
        # Day 1：A(#1), B(#2)
        e1 = await save_trending_edition(
            s,
            community="wuwa",
            period="daily",
            entries=[
                {"rank": 1, "repo": "org/repo-a", "language": "Python", "stars": 1000, "forks": 50, "period_stars": 300},
                {"rank": 2, "repo": "org/repo-b", "language": "Rust", "stars": 2000, "forks": 90, "period_stars": 250},
            ],
            now=day1,
        )
        assert e1.new_count == 2

        # Day 2：B(#1 上升1位), C(#2 首次上榜)
        e2 = await save_trending_edition(
            s,
            community="wuwa",
            period="daily",
            entries=[
                {"rank": 1, "repo": "org/repo-b", "language": "Rust", "stars": 2400, "forks": 110, "period_stars": 400},
                {"rank": 2, "repo": "org/repo-c", "language": "Go", "stars": 800, "forks": 30, "period_stars": 200},
            ],
            now=day2,
        )
        assert e2.new_count == 1

        # Day 3：D(#1 首次上榜), A(#2 复榜), B(#3 连榜3天下降2位)
        e3 = await save_trending_edition(
            s,
            community="wuwa",
            period="daily",
            entries=[
                {"rank": 1, "repo": "org/repo-d", "language": "Python", "stars": 500, "forks": 20, "period_stars": 450},
                {"rank": 2, "repo": "org/repo-a", "language": "Python", "stars": 1500, "forks": 70, "period_stars": 320},
                {"rank": 3, "repo": "org/repo-b", "language": "Rust", "stars": 2700, "forks": 130, "period_stars": 300},
            ],
            now=day3,
        )
        assert e3.new_count == 1

        # 同周周刊包含 repo-b
        await save_trending_edition(
            s,
            community="wuwa",
            period="weekly",
            entries=[
                {"rank": 1, "repo": "org/repo-b", "language": "Rust", "stars": 2700, "forks": 130, "period_stars": 950},
            ],
            now=day3,
        )
        await s.commit()

        # 读取 Day 3 视图
        view = await get_trending_editions_view(s, community="wuwa", period="daily")
        assert view["has_editions"] is True
        assert len(view["editions"]) == 3
        cur = view["current"]
        assert cur["issue_number"] == 3
        assert cur["stats"] == {"total": 3, "new_count": 1, "streak_count": 1, "returning_count": 1}
        assert cur["prev_key"] == trending_period_key("daily", day2)
        assert cur["next_key"] is None

        by_repo = {item["repo"]: item for item in cur["entries"]}
        assert by_repo["org/repo-d"]["status"] == "new"
        assert by_repo["org/repo-d"]["appearances"] == 1
        assert by_repo["org/repo-d"]["rank_delta"] is None

        assert by_repo["org/repo-a"]["status"] == "returning"
        assert by_repo["org/repo-a"]["appearances"] == 2
        assert by_repo["org/repo-a"]["first_seen_key"] == trending_period_key("daily", day1)
        assert by_repo["org/repo-a"]["best_rank"] == 1

        assert by_repo["org/repo-b"]["status"] == "streak"
        assert by_repo["org/repo-b"]["streak_count"] == 3
        assert by_repo["org/repo-b"]["appearances"] == 3
        assert by_repo["org/repo-b"]["rank_delta"] == -2
        assert by_repo["org/repo-b"]["best_rank"] == 1
        assert by_repo["org/repo-b"]["also_in"] == ["weekly"]

        # 语言统计
        assert cur["languages"] == [{"name": "Python", "count": 2}, {"name": "Rust", "count": 1}]

        # 翻阅历史 Day 2
        view_d2 = await get_trending_editions_view(
            s, community="wuwa", period="daily", period_key=trending_period_key("daily", day2)
        )
        assert view_d2["current"]["issue_number"] == 2
        assert view_d2["current"]["prev_key"] == trending_period_key("daily", day1)
        assert view_d2["current"]["next_key"] == trending_period_key("daily", day3)


# ---------- 3. 端到端：自定义开源分区 + 主仓库发版 + Trending 采集 + 公开接口 ----------


async def test_git_community_trending_and_main_repo_e2e(client, session_factory, admin_headers, monkeypatch):
    monkeypatch.setattr("app.services.collect.SessionLocal", session_factory)
    published = (utcnow() - timedelta(hours=2)).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

    daily_html = _trending_html(
        [
            ("acme/vision", "Vision agent framework", "Python", 9800, 420, 1200, "today"),
            ("acme/fastui", "Tiny UI runtime", "TypeScript", 4300, 190, 510, "today"),
        ]
    )
    weekly_html = _trending_html(
        [
            ("acme/vision", "Vision agent framework", "Python", 9800, 420, 4800, "this week"),
        ]
    )

    async def fake_request(self, http_client, url, *, params=None, headers=None):
        if "/trending" in url:
            since = (params or {}).get("since", "daily")
            return _make_response(url, text_body=weekly_html if since == "weekly" else daily_html)
        if "/releases" in url:
            return _make_response(
                url,
                json_body=[
                    {
                        "tag_name": "v1.3.0",
                        "name": "v1.3.0",
                        "body": "主仓库版本发布说明",
                        "draft": False,
                        "prerelease": False,
                        "published_at": published,
                        "html_url": "https://github.com/tianyin231/AniSignal/releases/tag/v1.3.0",
                        "author": {"login": "tianyin231"},
                        "assets": [],
                    }
                ],
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("app.collectors.git_repo._BaseGitRepoCollector._request", fake_request)

    async def fake_readme(repo: str, **_kwargs) -> str:
        return f"# {repo}\n\nMock README documentation for {repo}."

    monkeypatch.setattr("app.collectors.git_repo.fetch_github_repo_readme", fake_readme)

    # 1) 创建自定义主题流分区 git / 开源
    c_resp = await client.post(
        "/api/admin/communities",
        headers=admin_headers,
        json={"key": "git", "label": "开源", "en": "Open Source", "color": "violet", "domain": "feed"},
    )
    assert c_resp.status_code == 201

    # 2) 创建主仓库发版 + 日刊 + 周刊目标
    t_rel = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "github",
            "target_type": "releases",
            "target_value": "tianyin231/AniSignal",
            "display_name": "AniSignal 主仓库",
            "community": "git",
            "tier": "core",
            "is_official": True,
        },
    )
    assert t_rel.status_code == 201

    t_daily = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "github",
            "target_type": "trending",
            "target_value": "https://github.com/trending?since=daily",
            "display_name": "GitHub 日榜",
            "community": "git",
            "tier": "core",
            "interval_minutes": 360,
        },
    )
    assert t_daily.status_code == 201
    assert t_daily.json()["target_value"] == "daily"

    t_weekly = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "github",
            "target_type": "trending",
            "target_value": "weekly",
            "display_name": "GitHub 周榜",
            "community": "git",
            "tier": "core",
            "interval_minutes": 720,
        },
    )
    assert t_weekly.status_code == 201

    # 3) 依次采集（daily 重复跑两次验证同仓库去重、同期原地刷新）
    redis = _FakeRedis()
    assert (await run_collection(redis, t_rel.json()["id"]))["status"] == "success"
    assert (await run_collection(redis, t_daily.json()["id"]))["status"] == "success"
    assert (await run_collection(redis, t_daily.json()["id"]))["status"] == "success"
    assert (await run_collection(redis, t_weekly.json()["id"]))["status"] == "success"

    async with session_factory() as s:
        repos = list(
            await s.scalars(
                select(RawContent).where(RawContent.community == "git", RawContent.content_type == "repo")
            )
        )
        # acme/vision 在日榜跑了 2 次 + 周榜跑了 1 次，在 raw_contents 里严格只保留 1 条去重档案！
        assert len(repos) == 2
        editions = list(await s.scalars(select(GitTrendingEdition).where(GitTrendingEdition.community == "git")))
        assert len(editions) == 2

    # 4) 公开接口返回完整刊物与主仓库发版
    api_resp = await client.get("/api/git-trending/editions", params={"community": "git", "period": "daily"})
    assert api_resp.status_code == 200
    payload = api_resp.json()
    assert payload["has_editions"] is True
    assert payload["current"]["stats"]["total"] == 2
    assert payload["current"]["entries"][0]["repo"] == "acme/vision"
    assert payload["current"]["entries"][0]["also_in"] == ["weekly"]
    repo_content_id = payload["current"]["entries"][0]["raw_content_id"]
    assert repo_content_id is not None
    assert len(payload["releases"]) == 1
    assert payload["releases"][0]["tag_name"] == "v1.3.0"

    # 5) 站内详情返回 README、仓库元数据与日/周刊上榜轨迹
    detail_resp = await client.get(f"/api/contents/{repo_content_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["repo_meta"]["repo"] == "acme/vision"
    assert "Mock README documentation" in (detail["repo_meta"]["readme"] or "")
    periods_hit = [item["period"] for item in detail["repo_meta"]["trending_history"]]
    assert periods_hit == ["daily", "weekly"]

    # 6) 通用主题流总览接口返回焦点、热门与来源状态
    ov_resp = await client.get("/api/feeds/overview", params={"community": "git"})
    assert ov_resp.status_code == 200
    ov = ov_resp.json()
    assert ov["total_count"] == 3
    assert len(ov["hot_items"]) >= 2
    assert len(ov["sources"]) == 3
