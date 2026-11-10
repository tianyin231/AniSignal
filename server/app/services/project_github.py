"""项目公开资料：匿名读取 GitHub，短期缓存，页面访问不涉及更新部署。

项目由两个公开仓库组成：主仓库（主站与服务端）和单独发布的 AstrBot 插件仓库。
贡献者与正式发布只读主仓库；插件仓库由主仓库同步发版，读取其提交和 metadata.yaml 中的版本号。
"""

import asyncio
import re
import time
from datetime import timezone

import httpx

from app.clock import utcnow

REPOSITORIES = (
    {"key": "anisignal", "full_name": "tianyin231/AniSignal", "role": "主站与服务端",
     "contributors": True, "releases": True, "version_file": None},
    {"key": "plugin", "full_name": "tianyin231/AstrBot_Plugin_AniSignal", "role": "AstrBot 插件",
     "contributors": False, "releases": False, "version_file": "metadata.yaml"},
)
API_ROOT = "https://api.github.com/repos/"
RAW_ROOT = "https://raw.githubusercontent.com/"
COMMITS_PER_REPOSITORY = 12
CACHE_SECONDS = 15 * 60
RETRY_SECONDS = 5 * 60
_VERSION = re.compile(r"^version:\s*[\"']?v?(\d+(?:\.\d+)*)", re.MULTILINE)
_cache: dict | None = None
_expires_at = 0.0
_lock = asyncio.Lock()


def _url(repo: dict) -> str:
    return f"https://github.com/{repo['full_name']}"


def _initial() -> dict:
    return {
        "repositories": [{"key": repo["key"], "role": repo["role"], "name": repo["full_name"].split("/")[1],
                          "full_name": repo["full_name"], "url": _url(repo), "description": None,
                          "default_branch": None, "stars": None, "forks": None, "version": None,
                          "release": None, "commits": []} for repo in REPOSITORIES],
        "contributors": [], "contributors_has_more": False, "fetched_at": None, "stale": True,
    }


async def _get(client, url, params=None):
    response = await client.get(url, params=params)
    response.raise_for_status()
    return ([] if response.status_code == 204 else response.json()), bool(response.links.get("next"))


async def _get_text(client, url):
    # raw.githubusercontent.com 不计入 API 匿名额度，版本号从这里读。
    response = await client.get(url)
    response.raise_for_status()
    return response.text, False


def _requests(client, repo):
    api = API_ROOT + repo["full_name"]
    yield "repository", _get(client, api)
    yield "commits", _get(client, api + "/commits", {"per_page": COMMITS_PER_REPOSITORY})
    if repo["contributors"]:
        yield "contributors", _get(client, api + "/contributors", {"per_page": 100})
    if repo["releases"]:
        yield "release", _get(client, api + "/releases", {"per_page": 1})
    if repo["version_file"]:
        yield "version", _get_text(client, f"{RAW_ROOT}{repo['full_name']}/HEAD/{repo['version_file']}")


def _commit(row: dict) -> dict:
    details = row["commit"]
    title, _, body = details["message"].strip().partition("\n")
    author = row.get("author") or {}
    return {"sha": row["sha"], "url": row["html_url"], "title": title, "body": body.strip() or None,
            "committed_at": (details.get("committer") or {}).get("date") or details["author"]["date"],
            "author": {"name": author.get("login") or details["author"]["name"],
                       "url": author.get("html_url"), "avatar_url": author.get("avatar_url")}}


def _apply(data: dict, target: dict, repo: dict, section: str, value, has_more: bool) -> None:
    if section == "repository":
        target.update(name=value["name"], full_name=value["full_name"], url=_url(repo),
                      description=value.get("description"), default_branch=value["default_branch"],
                      stars=value["stargazers_count"], forks=value["forks_count"])
    elif section == "commits":
        target["commits"] = [_commit(row) for row in value]
    elif section == "contributors":
        data["contributors"] = [{"name": row["login"], "url": row["html_url"],
            "avatar_url": row["avatar_url"], "contributions": row["contributions"]} for row in value]
        data["contributors_has_more"] = has_more
    elif section == "release":
        release = value[0] if value else None
        target["release"] = ({"name": release.get("name") or release["tag_name"], "tag": release["tag_name"],
            "url": release["html_url"], "published_at": release["published_at"],
            "body": release.get("body") or None} if release else None)
    else:
        match = _VERSION.search(value)
        if not match:
            raise ValueError(f"{repo['full_name']} 的 {repo['version_file']} 没有版本号")
        target["version"] = "v" + match[1]


async def project_overview() -> dict:
    global _cache, _expires_at
    if _cache is not None and time.monotonic() < _expires_at:
        return _cache
    async with _lock:
        if _cache is not None and time.monotonic() < _expires_at:
            return _cache
        previous = _cache or _initial()
        # 某一项读取失败时沿用上次的记录，所以逐个仓库复制后再覆盖。
        data = {**previous, "repositories": [dict(repo) for repo in previous["repositories"]], "stale": False}
        async with httpx.AsyncClient(timeout=6, headers={
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "AniSignal-project-page",
        }) as client:
            jobs = [(target, repo, section, request) for target, repo in zip(data["repositories"], REPOSITORIES)
                    for section, request in _requests(client, repo)]
            results = await asyncio.gather(*(job[3] for job in jobs), return_exceptions=True)
        for (target, repo, section, _), result in zip(jobs, results):
            try:
                if isinstance(result, Exception):
                    raise result
                _apply(data, target, repo, section, *result)
            except Exception:
                data["stale"] = True
        if not data["stale"]:
            data["fetched_at"] = utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        _cache, _expires_at = data, time.monotonic() + (RETRY_SECONDS if data["stale"] else CACHE_SECONDS)
        return data
