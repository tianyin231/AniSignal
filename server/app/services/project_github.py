"""项目公开资料：匿名读取 GitHub，短期缓存，页面访问不涉及更新部署。"""

import asyncio
import time
from datetime import timezone

import httpx

from app.clock import utcnow

REPOSITORY = "tianyin231/AniSignal"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"
API_URL = f"https://api.github.com/repos/{REPOSITORY}"
CACHE_SECONDS = 15 * 60
RETRY_SECONDS = 5 * 60
_cache: dict | None = None
_expires_at = 0.0
_lock = asyncio.Lock()


def _initial() -> dict:
    return {
        "repository": {"name": "AniSignal", "full_name": REPOSITORY, "url": REPOSITORY_URL,
                       "description": None, "default_branch": None, "stars": None, "forks": None},
        "commits": [], "contributors": [], "contributors_has_more": False, "release": None,
        "fetched_at": None, "stale": True,
    }


async def _get(client, path, params=None):
    response = await client.get(API_URL + path, params=params)
    response.raise_for_status()
    return ([] if response.status_code == 204 else response.json()), bool(response.links.get("next"))


async def project_overview() -> dict:
    global _cache, _expires_at
    if _cache is not None and time.monotonic() < _expires_at:
        return _cache
    async with _lock:
        if _cache is not None and time.monotonic() < _expires_at:
            return _cache
        data = {**(_cache or _initial()), "stale": False}
        async with httpx.AsyncClient(timeout=6, headers={
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "AniSignal-project-page",
        }) as client:
            results = await asyncio.gather(
                _get(client, ""), _get(client, "/commits", {"per_page": 12}),
                _get(client, "/contributors", {"per_page": 100}),
                _get(client, "/releases", {"per_page": 1}), return_exceptions=True,
            )
        for section, result in zip(("repository", "commits", "contributors", "release"), results):
            if isinstance(result, Exception):
                data["stale"] = True
                continue
            value, has_more = result
            if section == "repository":
                data[section] = {"name": value["name"], "full_name": value["full_name"],
                    "url": REPOSITORY_URL, "description": value.get("description"),
                    "default_branch": value["default_branch"], "stars": value["stargazers_count"],
                    "forks": value["forks_count"]}
            elif section == "commits":
                commits = []
                for row in value:
                    details = row["commit"]
                    title, _, body = details["message"].strip().partition("\n")
                    author = row.get("author") or {}
                    commits.append({"sha": row["sha"], "url": row["html_url"], "title": title,
                        "body": body.strip() or None,
                        "committed_at": (details.get("committer") or {}).get("date") or details["author"]["date"],
                        "author": {"name": author.get("login") or details["author"]["name"],
                                   "url": author.get("html_url"), "avatar_url": author.get("avatar_url")}})
                data[section] = commits
            elif section == "contributors":
                data[section] = [{"name": row["login"], "url": row["html_url"],
                    "avatar_url": row["avatar_url"], "contributions": row["contributions"]} for row in value]
                data["contributors_has_more"] = has_more
            else:
                release = value[0] if value else None
                data[section] = ({"name": release.get("name") or release["tag_name"], "tag": release["tag_name"],
                    "url": release["html_url"], "published_at": release["published_at"],
                    "body": release.get("body") or None} if release else None)
        if not data["stale"]:
            data["fetched_at"] = utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        _cache, _expires_at = data, time.monotonic() + (RETRY_SECONDS if data["stale"] else CACHE_SECONDS)
        return data
