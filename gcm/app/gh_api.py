"""G35-10 GitHub Star 列表导入（纯逻辑层）。

从 GitHub Star API（GET https://api.github.com/users/{username}/starred）拉取
用户所有 Star 仓库的 owner/repo 列表，供 UI 层批量导入克隆。

设计要点：
- fetch_starred 分页拉取全部页：读响应头 Link 的 rel="next" 循环；上限
  MAX_STARRED_PAGES（10 页）防御，杜绝无限循环。
- fetcher 可注入（测试用）：签名 fetcher(url, token) -> (json_list, link_header)
  或抛异常。默认实现在函数内 lazy import urllib.request，绝不模块级触网。
- 失败（non-200 / 异常）→ 返回已拉部分列表 + 非空错误信息，不抛异常。
- parse_starred_payload / next_page_url 为纯函数，独立可测。
"""
from __future__ import annotations

import json
import re

# 分页防御上限：最多拉取 10 页（per_page=100 → 至多约 1000 条）
MAX_STARRED_PAGES = 10

_DEFAULT_PER_PAGE = 100
_USER_AGENT = "Git-clone-Max/7.0"

# Link header 形如 <https://...>; rel="next"，rel 值带不带引号都接受
_NEXT_LINK_RE = re.compile(r"<([^>]+)>\s*;\s*rel=\"?next\"?", re.IGNORECASE)


def parse_starred_payload(payload: list[dict] | None) -> list[str]:
    """从 Star API JSON 列表提取 full_name；非法条目（非 dict / 无有效名）跳过。"""
    result: list[str] = []
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        name = item.get("full_name")
        if isinstance(name, str) and name.strip():
            result.append(name)
    return result


def next_page_url(link_header: str) -> str:
    """解析 Link header 里 rel="next" 的 URL；无则返回空串。"""
    for m in _NEXT_LINK_RE.finditer(link_header or ""):
        return m.group(1)
    return ""


def _default_fetcher(url: str, token: str = "") -> tuple[list, str]:
    """默认抓取器：urllib 请求 Star API，返回 (json_list, link_header)。

    函数内 lazy import（不触网不依赖模块加载）；non-200 抛 RuntimeError，
    由 fetch_starred 统一转为 (partial_list, err)。token 非空 → 请求头
    Authorization: Bearer <token>。
    """
    import urllib.request  # lazy：仅真实抓取时才引入

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GitHub API 返回 HTTP {resp.status}")
        link = resp.headers.get("Link") or ""
        payload = json.loads(resp.read().decode("utf-8"))
    return payload, link


def fetch_starred(
    username: str,
    token: str = "",
    per_page: int = _DEFAULT_PER_PAGE,
    fetcher=None,
) -> tuple[list[str], str]:
    """拉取用户全部 Star 仓库，返回 (["owner/repo", ...], error)。

    - 分页：读 Link 头 rel="next" 循环；上限 MAX_STARRED_PAGES 页防御。
    - 中途失败（异常/non-200）→ 立即停止，返回已拉部分 + 非空 err。
    - fetcher 可注入（测试用）：fetcher(url, token) -> (json_list, link_header)。
    """
    username = (username or "").strip()
    if not username:
        return [], "用户名为空"

    fetch = fetcher or _default_fetcher
    repos: list[str] = []
    page_url = (
        f"https://api.github.com/users/{username}/starred"
        f"?per_page={per_page}"
    )
    page_no = 0
    while page_url and page_no < MAX_STARRED_PAGES:
        page_no += 1
        try:
            payload, link_header = fetch(page_url, token)
        except Exception as exc:  # noqa: BLE001 —— 统一转 (partial, err) 语义
            return repos, f"获取第 {page_no} 页失败: {exc}"
        repos.extend(parse_starred_payload(payload))
        page_url = next_page_url(link_header or "")
    return repos, ""
