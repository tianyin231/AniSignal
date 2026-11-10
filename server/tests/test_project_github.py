"""项目公开资料的展示、并发缓存和 GitHub 暂不可用回退。"""

import asyncio

import httpx
import pytest

from app.services import project_github as github

MAIN, PLUGIN = (repo["full_name"] for repo in github.REPOSITORIES)
ALL_REQUESTS = {"anisignal:", "anisignal:/commits", "anisignal:/contributors", "anisignal:/releases",
                "plugin:", "plugin:/commits", "plugin:metadata.yaml"}


@pytest.fixture
def github_api(monkeypatch):
    monkeypatch.setattr(github, "_cache", None)
    monkeypatch.setattr(github, "_expires_at", 0)
    monkeypatch.setattr(github, "_lock", asyncio.Lock())
    state = {"requests": [], "fail": set(), "release": False, "anonymous_author": False,
             "metadata": "name: astrbot_plugin_anisignal\nversion: v1.14.5\n"}
    original_client = httpx.AsyncClient

    def commit(full_name, sha, message, date):
        return {"sha": sha * 40, "html_url": f"https://github.com/{full_name}/commit/{sha * 40}",
                "commit": {"message": message, "author": {"name": "提交作者", "date": "2026-11-02T00:00:00Z"},
                           "committer": {"date": date}},
                "author": None if state["anonymous_author"] else {"login": "contributor", "html_url": "https://github.com/contributor", "avatar_url": "https://avatars.githubusercontent.com/u/1"}}

    def respond(request):
        path = request.url.path
        if request.url.host == "raw.githubusercontent.com":
            assert path == f"/{PLUGIN}/HEAD/metadata.yaml"
            key = "plugin:metadata.yaml"
        else:
            label, prefix = next((label, f"/repos/{name}") for label, name in (("anisignal", MAIN), ("plugin", PLUGIN))
                                 if path == f"/repos/{name}" or path.startswith(f"/repos/{name}/"))
            key = f"{label}:{path.removeprefix(prefix)}"
        state["requests"].append(key)
        if key in state["fail"]:
            return httpx.Response(503, json={"message": "temporarily unavailable"})
        if key == "plugin:metadata.yaml":
            return httpx.Response(200, text=state["metadata"])
        if key == "anisignal:":
            value = {"name": "AniSignal", "full_name": MAIN, "description": "项目简介",
                     "default_branch": "main", "stargazers_count": 12, "forks_count": 3}
        elif key == "plugin:":
            value = {"name": "AstrBot_Plugin_AniSignal", "full_name": PLUGIN, "description": "插件简介",
                     "default_branch": "main", "stargazers_count": 2, "forks_count": 0}
        elif key == "anisignal:/contributors":
            value = [{"login": "contributor", "html_url": "https://github.com/contributor",
                      "avatar_url": "https://avatars.githubusercontent.com/u/1", "contributions": 38}]
        elif key == "anisignal:/commits":
            value = [commit(MAIN, "a", "feat: 新增项目页\n\n显示贡献者和最近更新。", "2026-11-03T12:00:00Z")]
        elif key == "plugin:/commits":
            value = [commit(PLUGIN, "b", "release: v1.14.5（AniSignal 587a4b6）", "2026-11-10T14:00:00Z")]
        else:
            value = [{"name": "公开测试版", "tag_name": "v1.0", "html_url": f"https://github.com/{MAIN}/releases/tag/v1.0",
                      "published_at": "2026-11-03T08:00:00Z", "body": "发布说明"}] if state["release"] else []
        return httpx.Response(200, json=value)

    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    return state


async def test_public_project_page_shows_both_repositories(client, github_api):
    response = await client.get("/api/project/github")
    assert response.status_code == 200
    data = response.json()
    main, plugin = data["repositories"]
    assert (main["key"], main["role"], plugin["key"], plugin["role"]) == ("anisignal", "主站与服务端", "plugin", "AstrBot 插件")
    assert main["stars"] == 12 and main["default_branch"] == "main" and main["url"] == f"https://github.com/{MAIN}"
    assert main["commits"][0]["title"] == "feat: 新增项目页"
    assert main["commits"][0]["body"] == "显示贡献者和最近更新。"
    assert main["commits"][0]["committed_at"] == "2026-11-03T12:00:00Z"
    assert plugin["description"] == "插件简介" and plugin["version"] == "v1.14.5"
    assert plugin["commits"][0]["title"] == "release: v1.14.5（AniSignal 587a4b6）"
    assert main["version"] is None and plugin["release"] is None
    assert data["contributors"][0]["contributions"] == 38
    assert main["release"] is None and not data["stale"] and data["fetched_at"]
    assert set(github_api["requests"]) == ALL_REQUESTS


async def test_many_visitors_share_one_github_request_batch(github_api):
    results = await asyncio.gather(*(github.project_overview() for _ in range(15)))
    assert len(github_api["requests"]) == len(ALL_REQUESTS)
    assert all(data == results[0] for data in results)


async def test_github_failure_keeps_previous_records_without_repeated_requests(github_api, monkeypatch):
    first = await github.project_overview()
    github_api["fail"] = set(ALL_REQUESTS)
    monkeypatch.setattr(github, "_expires_at", 0)
    cached = await github.project_overview()
    assert cached["stale"] and cached["repositories"] == first["repositories"]
    assert cached["fetched_at"] == first["fetched_at"]
    await github.project_overview()
    assert len(github_api["requests"]) == 2 * len(ALL_REQUESTS)


async def test_one_repository_failing_keeps_the_other_fresh(github_api, monkeypatch):
    first = await github.project_overview()
    github_api["fail"] = {"plugin:/commits", "plugin:metadata.yaml"}
    github_api["metadata"] = "version: v9.9.9\n"
    monkeypatch.setattr(github, "_expires_at", 0)
    data = await github.project_overview()
    main, plugin = data["repositories"]
    assert data["stale"] and plugin["commits"] == first["repositories"][1]["commits"] and plugin["version"] == "v1.14.5"
    assert main["commits"][0]["title"] == "feat: 新增项目页"


async def test_unreadable_plugin_version_is_reported_as_stale(github_api):
    github_api["metadata"] = "name: astrbot_plugin_anisignal\n"
    data = await github.project_overview()
    assert data["stale"] and data["repositories"][1]["version"] is None
    assert data["repositories"][1]["commits"]


async def test_initial_failure_still_provides_repository_links(client, github_api):
    github_api["fail"] = set(ALL_REQUESTS)
    data = (await client.get("/api/project/github")).json()
    assert data["stale"] and [repo["url"] for repo in data["repositories"]] == [f"https://github.com/{MAIN}", f"https://github.com/{PLUGIN}"]
    assert all(repo["stars"] is None and repo["commits"] == [] for repo in data["repositories"])
    assert data["fetched_at"] is None


async def test_releases_and_unlinked_commit_authors_are_displayable(github_api):
    github_api.update(release=True, anonymous_author=True)
    data = await github.project_overview()
    main = data["repositories"][0]
    assert main["release"]["tag"] == "v1.0"
    assert main["commits"][0]["author"] == {"name": "提交作者", "url": None, "avatar_url": None}
