"""GitHub 原始条目（Release / Trending 仓库）转为主系统统一内容。"""

from __future__ import annotations

from app.collectors.github import git_item_id
from app.collectors.rss import feed_time
from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "1"


def _format_release_title(repo: str, tag_name: str, name: str, prerelease: bool) -> str:
    clean_tag = tag_name.strip()
    clean_name = name.strip()
    if not clean_name or clean_name.casefold() == clean_tag.casefold():
        base = f"{repo} {clean_tag}".strip()
    elif clean_tag and clean_tag.casefold() in clean_name.casefold():
        base = clean_name if repo.casefold() in clean_name.casefold() else f"{repo} {clean_name}"
    elif clean_tag:
        base = f"{repo} {clean_tag} · {clean_name}"
    else:
        base = f"{repo} {clean_name}".strip()
    if prerelease and "预发布" not in base:
        base = f"{base} [预发布]"
    return base[:240]


def parse_github_item(raw: dict) -> NormalizedContent:
    kind = str(raw.get("kind") or "release").strip().lower()
    repo = str(raw.get("repo") or "").strip()
    if not repo:
        raise ValueError("github item missing repo")

    is_release = kind == "release"
    is_trending = kind == "trending"
    tag_name = str(raw.get("tag_name") or "").strip()
    prerelease = bool(raw.get("prerelease"))

    if is_trending:
        title = repo[:240]
        fallback_ident = "repo"
        content_type = ContentType.REPO
    elif is_release:
        title = _format_release_title(repo, tag_name, str(raw.get("name") or ""), prerelease)
        fallback_ident = tag_name or str(raw.get("url") or "")
        content_type = ContentType.RELEASE
    else:
        title = (str(raw.get("title") or "").strip() or repo)[:240]
        fallback_ident = str(raw.get("number") or raw.get("url") or "")
        content_type = ContentType.POST

    ext_id = str(raw.get("id") or git_item_id(repo, kind, fallback_ident))
    official = bool(raw.get("is_official")) if "is_official" in raw else is_release
    identity = Identity.OFFICIAL if official else Identity.USER

    metrics: dict[str, int] = {}
    if is_trending:
        if isinstance(raw.get("period_stars"), int) and raw["period_stars"] > 0:
            metrics["view"] = raw["period_stars"]
        if isinstance(raw.get("stars"), int) and raw["stars"] > 0:
            metrics["like"] = raw["stars"]
        if isinstance(raw.get("forks"), int) and raw["forks"] > 0:
            metrics["fork"] = raw["forks"]

    return NormalizedContent(
        platform=Platform.GITHUB,
        content_type=content_type,
        external_id=ext_id,
        title=title,
        text=(str(raw.get("body") or "").strip() or None),
        url=str(raw.get("url") or "").strip() or None,
        published_at=feed_time(str(raw.get("published") or "")),
        author=NormalizedAuthor(
            external_id=repo.lower(),
            name=repo if is_trending else str(raw.get("source_name") or repo),
            identity=identity,
        ),
        media=[],
        metrics=metrics,
        extra={
            "kind": kind,
            "repo": repo,
            "tag_name": tag_name or None,
            "prerelease": prerelease,
            "assets_count": int(raw.get("assets_count") or 0),
            "language": str(raw.get("language") or "").strip() or None,
            "stars": int(raw.get("stars") or 0) if is_trending else None,
            "forks": int(raw.get("forks") or 0) if is_trending else None,
            "period_stars": int(raw.get("period_stars") or 0) if is_trending else None,
            "period": raw.get("period"),
            "rank": raw.get("rank"),
            "readme": str(raw.get("readme") or "").strip()[:8000] or None,
            "author": str(raw.get("author") or "").strip() or None,
            "source_channel": raw.get("source_channel") or "api",
        },
    )
