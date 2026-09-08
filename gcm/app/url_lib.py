# -*- coding: utf-8 -*-
"""GitHub 仓库地址解析与规范目录命名。"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..models import RepoSpec

# 匹配 https/ssh 格式
_HTTPS_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$",
    re.IGNORECASE,
)
_SSH_RE = re.compile(
    r"^(?:ssh://)?git@github\.com:"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$",
    re.IGNORECASE,
)
_SHORT_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$"
)

_INVALID_DIR_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_name(name: str) -> str:
    """清洗不可用作文件名的字符。"""
    return _INVALID_DIR_CHARS.sub("_", name).strip().strip(".") or "repo"


def parse_repo_url(raw: str) -> RepoSpec | None:
    """把用户输入解析成 RepoSpec；非法返回 None。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    m = _HTTPS_RE.match(raw)
    if not m:
        m = _SSH_RE.match(raw)
    if not m:
        m = _SHORT_RE.match(raw)
    if not m:
        return None
    owner = sanitize_name(m.group(1))
    repo = sanitize_name(m.group(2))
    if not owner or not repo:
        return None
    return RepoSpec(owner=owner, repo=repo, url_https=f"https://github.com/{owner}/{repo}.git")


def normalize_url(raw: str) -> str | None:
    spec = parse_repo_url(raw)
    return spec.url_https if spec else None


def is_github_url(raw: str) -> bool:
    return parse_repo_url(raw) is not None


def host_of(raw: str) -> str:
    """返回规范化 host（github.com / gitlab.com / gitee.com / codeberg.org …）。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    p = urlparse(raw if "://" in raw else "https://" + raw)
    host = (p.hostname or "").lower().replace("www.", "")
    if host in ("github.com", "gitlab.com", "gitee.com", "codeberg.org", "bitbucket.org"):
        return host
    # git@host:owner/repo
    m = re.match(r"^(?:ssh://)?git@([^:/]+):", raw, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return host