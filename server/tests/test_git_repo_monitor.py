"""开发者平台信源（GitHub / GitCode）端到端测试：归一化、ETag 304、限流回退 Atom、Admin 与用户共享定制监控。"""

from __future__ import annotations

from datetime import timedelta, timezone
import json

import httpx
import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.collectors.base import CollectorDeferred, CollectorError
from app.collectors.git_repo import (
    GitcodeCollector,
    GithubCollector,
    git_item_id,
    normalize_repo_config,
    normalize_repo_target,
    repo_web_url,
)
from app.models import CollectTarget, ContentVersion, CustomFeed, CustomFeedItem, CustomFeedSource, RawContent, User
from app.parsers.git_repo import parse_git_item
from app.services.auth import create_session, hash_password
from app.services.collect import run_collection
from app.services.content_diff import build_version_history
from app.services.custom_feeds import sweep_custom_feeds
from app.services.ingest import reparse_contents


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


def _iso_hours_ago(hours: int) -> str:
    return (utcnow() - timedelta(hours=hours)).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _make_response(
    url: str,
    *,
    status_code: int = 200,
    json_body=None,
    text_body: str = "",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    req = httpx.Request("GET", url)
    hdrs = dict(headers or {})
    if json_body is not None:
        content = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("content-type", "application/json; charset=utf-8")
    else:
        content = text_body.encode("utf-8")
    return httpx.Response(status_code=status_code, content=content, headers=hdrs, request=req)


def _releases_atom_xml(repo: str, tag: str, title: str, body_html: str, updated_iso: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Release notes from {repo}</title>
  <entry>
    <id>tag:github.com,2008:Repository/12345/{tag}</id>
    <updated>{updated_iso}</updated>
    <link rel="alternate" type="text/html" href="https://github.com/{repo}/releases/tag/{tag}" />
    <title>{title}</title>
    <content type="html">{body_html}</content>
    <author><name>maintainer</name></author>
  </entry>
</feed>"""


def _discussions_atom_xml(repo: str, num: int, title: str, body_html: str, published_iso: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Recent discussions in {repo}</title>
  <entry>
    <id>tag:github.com,2008:99999</id>
    <link rel="alternate" type="text/html" href="https://github.com/{repo}/discussions/{num}" />
    <title>{title}</title>
    <published>{published_iso}</published>
    <updated>{published_iso}</updated>
    <author><name>alice</name></author>
    <content type="html">{body_html}</content>
  </entry>
</feed>"""


# ---------- 1. 仓库目标归一化与配置校验 ----------


def test_normalize_repo_target_and_config():
    assert normalize_repo_target("github", "vllm-project/vllm") == "vllm-project/vllm"
    assert normalize_repo_target("github", "https://github.com/tianyin231/AniSignal.git") == "tianyin231/AniSignal"
    assert normalize_repo_target("github", "https://github.com/tianyin231/AniSignal/releases/tag/v1.3.0") == "tianyin231/AniSignal"
    assert normalize_repo_target("gitcode", "https://gitcode.com/Ascend/MindSpeed/issues") == "Ascend/MindSpeed"
    assert normalize_repo_target("gitcode", "gitcode.com/openharmony/docs/") == "openharmony/docs"
    assert repo_web_url("github", "tianyin231/AniSignal", "gh_releases") == "https://github.com/tianyin231/AniSignal/releases"
    assert repo_web_url("gitcode", "Ascend/MindSpeed", "gc_issues") == "https://gitcode.com/Ascend/MindSpeed/issues"

    # 跨平台链接与非法路径应被明确拒绝
    with pytest.raises(ValueError, match="不属于 GitHub"):
        normalize_repo_target("github", "https://gitcode.com/Ascend/MindSpeed")
    with pytest.raises(ValueError, match="不属于 GitCode"):
        normalize_repo_target("gitcode", "https://github.com/tianyin231/AniSignal")
    with pytest.raises(ValueError):
        normalize_repo_target("github", "only-owner")
    with pytest.raises(ValueError):
        normalize_repo_target("github", "../etc/passwd")

    # 配置规范化
    assert normalize_repo_config({"include_prerelease": True}, target_type="releases") == {}
    assert normalize_repo_config({"include_prerelease": False}, target_type="releases") == {"include_prerelease": False}
    assert normalize_repo_config({"labels": ["Announcement", "announcement", " roadmap "]}, target_type="issues") == {
        "labels": ["Announcement", "roadmap"]
    }
    with pytest.raises(ValueError, match="最多 10 个"):
        normalize_repo_config({"labels": [f"l{i}" for i in range(11)]}, target_type="issues")


# ---------- 2. GitHub Releases API → Atom 回退、ETag 304 与同 ID 幂等 ----------


async def test_github_releases_api_etag_and_atom_fallback_parity(monkeypatch):
    published = _iso_hours_ago(2)
    calls: list[tuple[str, dict]] = []
    mode = {"rate_limited": False}

    async def fake_request(self, client, url, *, params=None, headers=None):
        calls.append((url, dict(headers or {})))
        if "api.github.com" in url:
            if mode["rate_limited"]:
                return _make_response(
                    url,
                    status_code=403,
                    json_body={"message": "API rate limit exceeded"},
                    headers={"x-ratelimit-remaining": "0"},
                )
            if (headers or {}).get("If-None-Match") == 'W/"gh-rel-1"':
                return _make_response(url, status_code=304, headers={"etag": 'W/"gh-rel-1"'})
            return _make_response(
                url,
                status_code=200,
                headers={"etag": 'W/"gh-rel-1"'},
                json_body=[
                    {
                        "id": 999001,
                        "tag_name": "v2.0.0",
                        "name": "v2.0.0 正式版",
                        "body": "### 新特性\n- 支持 GitHub 与 GitCode 信源",
                        "draft": False,
                        "prerelease": False,
                        "published_at": published,
                        "html_url": "https://github.com/acme/demo/releases/tag/v2.0.0",
                        "author": {"login": "maintainer"},
                        "assets": [{"name": "demo.tar.gz"}],
                    },
                    {
                        "id": 999002,
                        "tag_name": "v2.1.0-rc1",
                        "name": "v2.1.0-rc1",
                        "body": "候选测试版",
                        "draft": False,
                        "prerelease": True,
                        "published_at": published,
                        "html_url": "https://github.com/acme/demo/releases/tag/v2.1.0-rc1",
                        "author": {"login": "maintainer"},
                        "assets": [],
                    },
                    {
                        "id": 999003,
                        "tag_name": "v9.9.9-draft",
                        "name": "Draft",
                        "draft": True,
                        "prerelease": False,
                    },
                ],
            )
        # releases.atom 回退
        xml = _releases_atom_xml(
            "acme/demo",
            "v2.0.0",
            "v2.0.0 正式版",
            "&lt;h3&gt;新特性&lt;/h3&gt;&lt;p&gt;支持 GitHub 与 GitCode 信源&lt;/p&gt;",
            published,
        )
        return _make_response(url, status_code=200, text_body=xml, headers={"etag": 'W/"atom-1"'})

    monkeypatch.setattr("app.collectors.git_repo._BaseGitRepoCollector._request", fake_request)

    # 1) 默认包含预发布、跳过草稿
    collector = GithubCollector(token="")
    res = await collector.fetch("releases", "acme/demo", 10)
    assert res.fetch_kind == "github_releases"
    assert [item["tag_name"] for item in res.items] == ["v2.0.0", "v2.1.0-rc1"]
    assert collector.last_http_cache is not None
    api_item_id = res.items[0]["id"]
    assert api_item_id == git_item_id("acme/demo", "release", "v2.0.0")

    # 2) ETag 命中 304
    collector2 = GithubCollector(token="")
    collector2.source_config = {"_http_cache": collector.last_http_cache}
    res_304 = await collector2.fetch("releases", "acme/demo", 10)
    assert collector2.not_modified is True
    assert res_304.items == []

    # 3) 过滤预发布
    collector3 = GithubCollector(token="")
    collector3.source_config = {"repo": {"include_prerelease": False}}
    res_stable = await collector3.fetch("releases", "acme/demo", 10)
    assert [item["tag_name"] for item in res_stable.items] == ["v2.0.0"]

    # 4) 匿名限流 403 自动回退 Atom，且产出的 external_id 与 API 完全一致
    mode["rate_limited"] = True
    collector4 = GithubCollector(token="")
    res_atom = await collector4.fetch("releases", "acme/demo", 10)
    assert len(res_atom.items) == 1
    assert res_atom.items[0]["source_channel"] == "atom"
    assert res_atom.items[0]["id"] == api_item_id
    parsed = parse_git_item(res_atom.items[0])
    assert parsed.external_id == api_item_id
    assert parsed.content_type == "release"
    assert "支持 GitHub 与 GitCode 信源" in (parsed.text or "")


# ---------- 3. GitHub Issues PR 过滤、标签 OR 筛选、限流 CollectorDeferred 与 Discussions Atom ----------


async def test_github_issues_and_discussions(monkeypatch):
    published = _iso_hours_ago(3)
    captured_params: list[dict] = []

    async def fake_request(self, client, url, *, params=None, headers=None):
        captured_params.append(dict(params or {}))
        if url.endswith("/discussions.atom"):
            xml = _discussions_atom_xml(
                "acme/demo",
                42,
                "2027 Roadmap Discussion",
                "&lt;p&gt;Welcome to our public roadmap.&lt;/p&gt;",
                published,
            )
            return _make_response(url, status_code=200, text_body=xml, headers={"etag": 'W/"disc-1"'})
        if "rate-limit/repo" in url:
            reset_ts = int((utcnow() + timedelta(minutes=10)).replace(tzinfo=timezone.utc).timestamp())
            return _make_response(
                url,
                status_code=403,
                json_body={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset_ts)},
            )
        if "missing/repo" in url:
            return _make_response(url, status_code=404, json_body={"message": "Not Found"})
        return _make_response(
            url,
            status_code=200,
            json_body=[
                {
                    "number": 11,
                    "title": "PR should be skipped",
                    "body": "diff",
                    "pull_request": {"url": "https://api.github.com/repos/acme/demo/pulls/11"},
                    "labels": [{"name": "announcement"}],
                    "state": "open",
                    "created_at": published,
                },
                {
                    "number": 12,
                    "title": "Release Plan Q4",
                    "body": "Details here",
                    "html_url": "https://github.com/acme/demo/issues/12",
                    "labels": [{"name": "Roadmap"}],
                    "state": "open",
                    "comments": 5,
                    "reactions": {"total_count": 9},
                    "user": {"login": "lead"},
                    "created_at": published,
                },
                {
                    "number": 13,
                    "title": "Minor typo",
                    "body": "fix",
                    "html_url": "https://github.com/acme/demo/issues/13",
                    "labels": [{"name": "docs"}],
                    "state": "open",
                    "user": {"login": "bob"},
                    "created_at": published,
                },
            ],
        )

    monkeypatch.setattr("app.collectors.git_repo._BaseGitRepoCollector._request", fake_request)

    # 多标签本地 OR 过滤（不传 AND 参数到 API）且跳过 PR
    c_multi = GithubCollector(token="")
    c_multi.source_config = {"repo": {"labels": ["announcement", "roadmap"]}}
    res = await c_multi.fetch("issues", "acme/demo", 10)
    assert "labels" not in captured_params[-1]
    assert [i["number"] for i in res.items] == [12]
    parsed = parse_git_item(res.items[0])
    assert parsed.metrics == {"reply": 5, "like": 9}
    assert parsed.extra["labels"] == ["Roadmap"]

    # 单标签传上游参数
    c_single = GithubCollector(token="")
    c_single.source_config = {"repo": {"labels": ["roadmap"]}}
    await c_single.fetch("issues", "acme/demo", 10)
    assert captured_params[-1].get("labels") == "roadmap"

    # 匿名限流返回 CollectorDeferred（不计失败）
    with pytest.raises(CollectorDeferred):
        await GithubCollector(token="").fetch("issues", "rate-limit/repo", 10)

    # 404 快速止损
    with pytest.raises(CollectorError) as exc_info:
        await GithubCollector(token="").fetch("issues", "missing/repo", 10)
    assert getattr(exc_info.value, "stop_fallback", False) is True

    # Discussions Atom 匿名采集
    res_disc = await GithubCollector(token="").fetch("discussions", "acme/demo", 10)
    assert res_disc.fetch_kind == "github_discussions"
    assert len(res_disc.items) == 1
    assert res_disc.items[0]["number"] == 42
    assert "public roadmap" in res_disc.items[0]["body"]


# ---------- 4. GitCode OpenAPI v5 Releases & Issues ----------


async def test_gitcode_releases_and_issues(monkeypatch):
    published = _iso_hours_ago(4)

    async def fake_request(self, client, url, *, params=None, headers=None):
        if url.endswith("/releases"):
            return _make_response(
                url,
                status_code=200,
                json_body=[
                    {
                        "tag_name": "v2.2.0",
                        "name": "v2.2.0",
                        "body": "### 新增说明\n- 支持分布式训练加速",
                        "prerelease": False,
                        "created_at": published,
                        "author": {"login": "ascend", "name": "Ascend"},
                        "assets": [],
                    },
                    {
                        "tag_name": "v2.3.0-beta1",
                        "name": "v2.3.0-beta1",
                        "body": "测试版",
                        "prerelease": True,
                        "created_at": published,
                        "author": {"login": "ascend", "name": "Ascend"},
                        "assets": [],
                    },
                ],
            )
        return _make_response(
            url,
            status_code=200,
            json_body=[
                {
                    "id": 4618819,
                    "number": "392",
                    "html_url": "https://gitcode.com/Ascend/MindSpeed/issues/392",
                    "state": "open",
                    "title": "Batch SFT loss reporting",
                    "body": "Performance issue details",
                    "labels": [{"name": "performance"}],
                    "comments": 3,
                    "user": {"login": "dev1", "name": "Dev One"},
                    "created_at": published,
                }
            ],
        )

    monkeypatch.setattr("app.collectors.git_repo._BaseGitRepoCollector._request", fake_request)

    gc = GitcodeCollector(token="")
    gc.source_config = {"repo": {"include_prerelease": False}}
    rel = await gc.fetch("releases", "https://gitcode.com/Ascend/MindSpeed", 10)
    assert rel.fetch_kind == "gitcode_releases"
    assert len(rel.items) == 1
    assert rel.items[0]["url"] == "https://gitcode.com/Ascend/MindSpeed/releases/tag/v2.2.0"

    iss = await GitcodeCollector(token="").fetch("issues", "Ascend/MindSpeed", 10)
    assert iss.fetch_kind == "gitcode_issues"
    assert len(iss.items) == 1
    parsed = parse_git_item(iss.items[0])
    assert parsed.platform == "gitcode"
    assert parsed.content_type == "post"
    assert parsed.metrics == {"reply": 3}


# ---------- 5. Admin 目标全链路（含 Release Notes 编辑版本 Diff 与 reparse） ----------


async def test_admin_git_target_lifecycle_and_version_diff(client, session_factory, admin_headers, monkeypatch):
    monkeypatch.setattr("app.services.collect.SessionLocal", session_factory)
    published = _iso_hours_ago(1)
    release_state = {"body": "### 更新内容\n- 首批发布", "etag": 'W/"v1"'}

    async def fake_request(self, client, url, *, params=None, headers=None):
        if (headers or {}).get("If-None-Match") == release_state["etag"]:
            return _make_response(url, status_code=304, headers={"etag": release_state["etag"]})
        return _make_response(
            url,
            status_code=200,
            headers={"etag": release_state["etag"]},
            json_body=[
                {
                    "tag_name": "v1.4.0",
                    "name": "v1.4.0",
                    "body": release_state["body"],
                    "draft": False,
                    "prerelease": False,
                    "published_at": published,
                    "html_url": "https://github.com/tianyin231/AniSignal/releases/tag/v1.4.0",
                    "author": {"login": "tianyin231"},
                    "assets": [],
                }
            ],
        )

    monkeypatch.setattr("app.collectors.git_repo._BaseGitRepoCollector._request", fake_request)

    created = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "github",
            "target_type": "releases",
            "target_value": "https://github.com/tianyin231/AniSignal/releases",
            "display_name": "AniSignal 正式版",
            "community": "ai",
            "tier": "core",
            "is_official": True,
            "repo_config": {"include_prerelease": False},
        },
    )
    assert created.status_code == 201
    data = created.json()
    assert data["target_value"] == "tianyin231/AniSignal"
    assert data["repo_config"] == {"include_prerelease": False}
    target_id = data["id"]

    fake_redis = _FakeRedis()
    r1 = await run_collection(fake_redis, target_id)
    assert r1["status"] == "success"
    assert r1["items_new"] == 1

    # 第二轮命中 ETag 304
    r2 = await run_collection(fake_redis, target_id)
    assert r2["status"] == "success"
    assert r2["items_found"] == 0

    # 作者修改了 Release Notes -> 追加 v2 版本
    release_state["body"] = "### 更新内容\n- 首批发布\n- 补充 GitCode 匿名支持说明"
    release_state["etag"] = 'W/"v2"'
    r3 = await run_collection(fake_redis, target_id)
    assert r3["status"] == "success"

    # 更新 repo_config 会持久化并清除 _http_cache
    updated = await client.put(
        f"/api/admin/collect-targets/{target_id}",
        headers=admin_headers,
        json={"repo_config": {"include_prerelease": True}},
    )
    assert updated.status_code == 200
    assert updated.json()["repo_config"] == {}

    async with session_factory() as s:
        raw = await s.scalar(select(RawContent).where(RawContent.platform == "github"))
        assert raw is not None
        assert raw.content_type == "release"
        versions = list(
            await s.scalars(
                select(ContentVersion)
                .where(ContentVersion.raw_content_id == raw.id)
                .order_by(ContentVersion.version.asc())
            )
        )
        assert len(versions) == 2
        history = build_version_history(raw.id, versions, platform="github")
        assert history["revision_count"] == 1
        assert history["has_source_revision"] is True

        rep = await reparse_contents(s, "github")
        assert rep["failed"] == 0
        assert rep["checked"] == 1


# ---------- 6. 用户定制监控：共享单次采集 + 按订阅者独立过滤预发布与标签 ----------


async def _create_authed_user(session_factory, email: str) -> tuple[int, str]:
    async with session_factory() as s:
        user = User(
            email=email,
            display_name=email.split("@")[0],
            password_hash=hash_password("Pass123456!"),
            is_verified=True,
            status="active",
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        token = await create_session(s, user)
        await s.commit()
        return user.id, token


async def test_custom_feeds_shared_git_repo_and_per_user_filters(client, session_factory, admin_headers, monkeypatch):
    published = _iso_hours_ago(2)
    fetch_counter = {"github_rel": 0, "gitcode_iss": 0}

    async def fake_request(self, client, url, *, params=None, headers=None):
        if "api.github.com/repos/" in url and url.endswith("/releases"):
            fetch_counter["github_rel"] += 1
            return _make_response(
                url,
                status_code=200,
                headers={"etag": 'W/"shared-1"'},
                json_body=[
                    {
                        "tag_name": "v1.0.0",
                        "name": "v1.0.0",
                        "body": "Stable release",
                        "draft": False,
                        "prerelease": False,
                        "published_at": published,
                        "html_url": "https://github.com/acme/tool/releases/tag/v1.0.0",
                        "author": {"login": "acme"},
                    },
                    {
                        "tag_name": "v1.1.0-alpha1",
                        "name": "v1.1.0-alpha1",
                        "body": "Alpha build",
                        "draft": False,
                        "prerelease": True,
                        "published_at": published,
                        "html_url": "https://github.com/acme/tool/releases/tag/v1.1.0-alpha1",
                        "author": {"login": "acme"},
                    },
                ],
            )
        if "api.gitcode.com/api/v5/repos/" in url and url.endswith("/issues"):
            fetch_counter["gitcode_iss"] += 1
            return _make_response(
                url,
                status_code=200,
                json_body=[
                    {
                        "id": 101,
                        "number": "10",
                        "html_url": "https://gitcode.com/Ascend/Demo/issues/10",
                        "title": "Announcement: New Release",
                        "body": "Important notice",
                        "labels": [{"name": "announcement"}],
                        "state": "open",
                        "created_at": published,
                        "user": {"login": "maintainer"},
                    },
                    {
                        "id": 102,
                        "number": "11",
                        "html_url": "https://gitcode.com/Ascend/Demo/issues/11",
                        "title": "Question about install",
                        "body": "Help",
                        "labels": [{"name": "question"}],
                        "state": "open",
                        "created_at": published,
                        "user": {"login": "user2"},
                    },
                ],
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("app.collectors.git_repo._BaseGitRepoCollector._request", fake_request)

    _, token_a = await _create_authed_user(session_factory, "git_a@example.com")
    _, token_b = await _create_authed_user(session_factory, "git_b@example.com")

    # 用户 A：大小写不同，包含预发布；公开
    resp_a = await client.post(
        "/api/account/custom-feeds",
        cookies={"anisignal_session": token_a},
        json={
            "name": "Acme 全版本",
            "kind": "gh_releases",
            "target_value": "https://github.com/Acme/Tool/releases",
            "is_public": True,
            "filters": {"include": [], "exclude": []},
        },
    )
    assert resp_a.status_code == 201
    feed_a_id = resp_a.json()["id"]

    # 用户 B：同一仓库小写，只要正式版（排除预发布）
    resp_b = await client.post(
        "/api/account/custom-feeds",
        cookies={"anisignal_session": token_b},
        json={
            "name": "Acme 仅正式版",
            "kind": "gh_releases",
            "target_value": "acme/tool",
            "is_public": False,
            "filters": {"include": [], "exclude": [], "include_prerelease": False},
        },
    )
    assert resp_b.status_code == 201
    feed_b_id = resp_b.json()["id"]

    # 用户 A 同时建一个 GitCode 议题监控（按 announcement 标签筛选）
    resp_gc = await client.post(
        "/api/account/custom-feeds",
        cookies={"anisignal_session": token_a},
        json={
            "name": "Ascend 公告议题",
            "kind": "gc_issues",
            "target_value": "https://gitcode.com/Ascend/Demo",
            "is_public": True,
            "filters": {"include": [], "exclude": [], "labels": ["announcement"]},
        },
    )
    assert resp_gc.status_code == 201
    feed_gc_id = resp_gc.json()["id"]

    async with session_factory() as s:
        stats = await sweep_custom_feeds(s, _FakeRedis())
        assert stats["failed"] == 0
        # Acme/Tool 与 acme/tool 复用同一个 CustomFeedSource，只抓取一次！
        assert fetch_counter["github_rel"] == 1
        assert fetch_counter["gitcode_iss"] == 1

        gh_sources = list(await s.scalars(select(CustomFeedSource).where(CustomFeedSource.platform == "github")))
        assert len(gh_sources) == 1

        items_a = list(await s.scalars(select(CustomFeedItem).where(CustomFeedItem.feed_id == feed_a_id)))
        items_b = list(await s.scalars(select(CustomFeedItem).where(CustomFeedItem.feed_id == feed_b_id)))
        items_gc = list(await s.scalars(select(CustomFeedItem).where(CustomFeedItem.feed_id == feed_gc_id)))
        assert len(items_a) == 2
        assert len(items_b) == 1
        assert "v1.0.0" in (items_b[0].title or "")
        assert len(items_gc) == 1
        assert "#10" in (items_gc[0].title or "")

    # 公开定制监控详情页返回正确的仓库直链
    pub = await client.get(f"/api/custom/feeds/{feed_a_id}")
    assert pub.status_code == 200
    assert pub.json()["source_url"] == "https://github.com/Acme/Tool/releases"

    # 管理端按新类型筛选用户监控正常通过
    adm = await client.get("/api/admin/custom-feeds", headers=admin_headers, params={"kind": "gh_releases"})
    assert adm.status_code == 200
    assert adm.json()["total"] == 2
