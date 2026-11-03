"""项目公开资料的展示、并发缓存和 GitHub 暂不可用回退。"""

import asyncio

import httpx
import pytest

from app.services import project_github as github


@pytest.fixture
def github_api(monkeypatch):
    monkeypatch.setattr(github, "_cache", None)
    monkeypatch.setattr(github, "_expires_at", 0)
    monkeypatch.setattr(github, "_lock", asyncio.Lock())
    state = {"requests": [], "fail": set(), "release": False, "anonymous_author": False}
    original_client = httpx.AsyncClient

    def respond(request):
        path = request.url.path.removeprefix("/repos/tianyin231/AniSignal")
        state["requests"].append(path)
        if path in state["fail"]:
            return httpx.Response(503, json={"message": "temporarily unavailable"})
        if path == "":
            value = {"name": "AniSignal", "full_name": github.REPOSITORY, "description": "项目简介",
                     "default_branch": "main", "stargazers_count": 12, "forks_count": 3}
        elif path == "/contributors":
            value = [{"login": "contributor", "html_url": "https://github.com/contributor",
                      "avatar_url": "https://avatars.githubusercontent.com/u/1", "contributions": 38}]
        elif path == "/commits":
            value = [{"sha": "a" * 40, "html_url": github.REPOSITORY_URL + "/commit/" + "a" * 40,
                      "commit": {"message": "feat: 新增项目页\n\n显示贡献者和最近更新。", "author": {"name": "提交作者", "date": "2026-11-02T00:00:00Z"},
                                 "committer": {"date": "2026-11-03T12:00:00Z"}},
                      "author": None if state["anonymous_author"] else {"login": "contributor", "html_url": "https://github.com/contributor", "avatar_url": "https://avatars.githubusercontent.com/u/1"}}]
        else:
            value = [{"name": "公开测试版", "tag_name": "v1.0", "html_url": github.REPOSITORY_URL + "/releases/tag/v1.0",
                      "published_at": "2026-11-03T08:00:00Z", "body": "发布说明"}] if state["release"] else []
        return httpx.Response(200, json=value)

    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    return state


async def test_public_project_page_data_maps_commits_and_contributors(client, github_api):
    response = await client.get("/api/project/github")
    assert response.status_code == 200
    data = response.json()
    assert data["repository"]["stars"] == 12 and data["repository"]["default_branch"] == "main"
    assert data["commits"][0]["title"] == "feat: 新增项目页"
    assert data["commits"][0]["body"] == "显示贡献者和最近更新。"
    assert data["commits"][0]["committed_at"] == "2026-11-03T12:00:00Z"
    assert data["contributors"][0]["contributions"] == 38
    assert data["release"] is None and not data["stale"] and data["fetched_at"]


async def test_many_visitors_share_one_github_request_batch(github_api):
    results = await asyncio.gather(*(github.project_overview() for _ in range(15)))
    assert len(github_api["requests"]) == 4
    assert all(data == results[0] for data in results)


async def test_github_failure_keeps_previous_records_without_repeated_requests(github_api, monkeypatch):
    first = await github.project_overview()
    github_api["fail"] = {"", "/commits", "/contributors", "/releases"}
    monkeypatch.setattr(github, "_expires_at", 0)
    cached = await github.project_overview()
    assert cached["stale"] and cached["commits"] == first["commits"]
    assert cached["fetched_at"] == first["fetched_at"]
    await github.project_overview()
    assert len(github_api["requests"]) == 8


async def test_initial_failure_still_provides_repository_link(client, github_api):
    github_api["fail"] = {"", "/commits", "/contributors", "/releases"}
    data = (await client.get("/api/project/github")).json()
    assert data["stale"] and data["repository"]["url"] == github.REPOSITORY_URL
    assert data["repository"]["stars"] is None and data["commits"] == []
    assert data["fetched_at"] is None


async def test_releases_and_unlinked_commit_authors_are_displayable(github_api):
    github_api.update(release=True, anonymous_author=True)
    data = await github.project_overview()
    assert data["release"]["tag"] == "v1.0"
    assert data["commits"][0]["author"] == {"name": "提交作者", "url": None, "avatar_url": None}
