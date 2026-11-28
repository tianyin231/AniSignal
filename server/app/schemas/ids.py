"""确定性外部内容与来源标识（纯哈希/字符串规范化底层原语，不依赖采集器或服务层）。"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

_GITHUB_HOSTS = {"github.com", "www.github.com"}
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$|^[A-Za-z0-9]$")


def normalize_github_repo(raw: str) -> str:
    """将用户输入规整为 `owner/repo`；支持裸路径与公开 GitHub 仓库链接。"""
    value = str(raw or "").strip()
    if not value:
        raise ValueError("请填写 GitHub 仓库（如 owner/repo）")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value) or any(ch.isspace() for ch in value):
        raise ValueError("GitHub 仓库标识不能包含空白或控制字符")

    path = value
    lower = value.lower()
    if "://" in value or lower.startswith(("github.com/", "www.github.com/")):
        candidate = value if "://" in value else f"https://{value}"
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("请填写有效的公开 GitHub 仓库地址或 owner/repo")
        if host not in _GITHUB_HOSTS:
            raise ValueError("该链接不属于 GitHub，请填写 GitHub 仓库地址或 owner/repo")
        path = parsed.path

    parts = [seg for seg in path.strip("/").split("/") if seg]
    if len(parts) < 2:
        raise ValueError("请填写完整的 GitHub 仓库（如 owner/repo 或仓库链接）")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if ".." in owner or ".." in repo or not _SEGMENT_RE.fullmatch(owner) or not _SEGMENT_RE.fullmatch(repo):
        raise ValueError("GitHub 仓库格式无效，请使用 owner/repo 形式")
    full = f"{owner}/{repo}"
    if len(full) > 128:
        raise ValueError("GitHub 仓库路径过长（最多 128 字符）")
    return full


def rss_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:48]


def web_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:48]


def git_item_id(repo: str, kind: str, ident: str) -> str:
    """生成跨 API / Atom 回退稳定的 external_id（长度始终 ≤ 64，兼容 RawContent 与 CustomFeedItem）。"""
    clean_ident = str(ident or "").strip()
    base = f"{repo.lower()}:{kind}:{clean_ident}"
    if len(base) <= 64:
        return base
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:48]
    return f"{kind}:{digest}"[:64]
