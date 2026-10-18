"""G58-2 镜像多备援（纯逻辑，不触网、不依赖 Qt）。

设计目标
--------
国内网络访问 github.com 经常很慢或不可达。做法是让克隆/更新走第三方
GitHub 镜像前缀，并且**失败一定回退源站**，而不是把用户卡死在镜像上。

三条硬约束
----------
1. **只对 HTTPS 生效**：SSH 地址无法通过前缀代理（`ssh://`、`git@` 原样跳过）。
2. **有凭据时绝不用镜像**：镜像方会看到请求（包括 `Authorization` 头）。
   只要该 host 配置了 token / host_tokens / 认证头，就只走源站。
3. **源站永远是最后一个候选**：镜像全部不可用也必须能继续，不能因为镜像挂了
   导致本来能克隆的仓库失败。

预设镜像列表是**实测过**的（2026-10-18，用 `git ls-remote` 验证可代理 git
smart HTTP），按当时测得的中位延迟排序。第三方服务随时可能失效，所以：
- 用户可以在设置页覆盖/追加；
- 设置页有「测速并排序」按钮，按当前网络重新排序；
- 运行时每个仓库先做一次短超时 `ls-remote` 探测，探测失败直接跳下一个候选。
"""
from __future__ import annotations

from urllib.parse import urlparse

#: 内置 GitHub 镜像（2026-10-18 实测可代理 git；按当时中位延迟排序）。
#: 注意：这是**第三方服务**，可用性会变化。设置页可关闭或改写。
DEFAULT_GITHUB_MIRRORS: tuple[str, ...] = (
    "https://gh.llkk.cc",       # ~2.4s
    "https://gh-proxy.com",     # ~3.3s
    "https://ghfast.top",       # ~3.6s
    "https://ghproxy.net",      # ~8.0s
)

#: 预设列表的实测日期（写进 UI 提示，避免用户以为是"永久有效"）。
PRESET_SCANNED_AT = "2026-10-18"


def _clean_prefix(value: str) -> str:
    """归一化单个镜像前缀：去空白、去尾部斜杠。"""
    return (value or "").strip().rstrip("/")


def normalize_prefix_list(value) -> tuple[str, ...]:
    """把设置里的镜像值归一为有序元组。

    兼容三种历史/新写法：
    - `"https://a"`（旧单值）
    - `["https://a", "https://b"]`（列表）
    - `"https://a, https://b https://c"`（逗号/空白分隔的文本）

    去重且保留首次出现顺序（顺序即优先级）。
    """
    items: list[str] = []
    if value is None:
        return ()
    if isinstance(value, str):
        raw = value.replace(",", " ").replace("，", " ").split()
        items = [raw_item for raw_item in raw]
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, str):
                items.extend(item.replace(",", " ").replace("，", " ").split())
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        p = _clean_prefix(item)
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


def normalize_mirror_prefix(mapping) -> dict[str, tuple[str, ...]]:
    """把 `{host: str|list}` 归一为 `{host: tuple[str, ...]}`（host 小写、去 www）。"""
    out: dict[str, tuple[str, ...]] = {}
    if not isinstance(mapping, dict):
        return out
    for key, value in mapping.items():
        host = str(key or "").strip().lower()
        host = host[4:] if host.startswith("www.") else host
        host = host.rstrip("/")
        prefixes = normalize_prefix_list(value)
        if host and prefixes:
            out[host] = prefixes
    return out


def mirror_prefixes_for(host: str, mapping, use_presets: bool = True) -> tuple[str, ...]:
    """取某 host 使用的镜像前缀（用户配置优先，其次内置预设）。

    `mapping` 可以是原始 settings 值或已归一结果（两种都接受）。
    `*` 键 = 对所有 host 生效（但仍受「仅 HTTPS」约束）。
    """
    normalized = (mapping if isinstance(mapping, dict) and
                  all(isinstance(v, tuple) for v in mapping.values())
                  else normalize_mirror_prefix(mapping))
    h = (host or "").strip().lower()
    h = h[4:] if h.startswith("www.") else h
    out: list[str] = []
    for key in (h, "*"):
        out.extend(normalized.get(key, ()))
    if use_presets and h in ("", "github.com"):
        out.extend(DEFAULT_GITHUB_MIRRORS)
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return tuple(uniq)


def build_mirror_urls(source_url: str, prefixes: tuple[str, ...] | list[str]) -> list[str]:
    """构造镜像候选 URL 列表（**不含**源站；源站由调用方最后追加）。

    - 非 HTTPS → 空列表（SSH/本地路径无法用前缀代理）
    - 前缀已包含在 URL 里（重复拼接）→ 跳过
    """
    url = (source_url or "").strip()
    if not url.lower().startswith("https://"):
        return []
    # 安全：URL 内嵌凭据（https://user:pass@host/...）绝不拼接 ——
    # 镜像前缀会把整段（含凭据）放进请求路径，等于把密码发给第三方。
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            return []
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for prefix in prefixes or ():
        p = _clean_prefix(prefix)
        if not p or not p.lower().startswith(("http://", "https://")):
            continue
        candidate = f"{p}/{url}"
        if candidate in seen or candidate == url:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def requires_credentials(host: str, token: str, host_tokens) -> bool:
    """该 host 是否会携带应用内凭据（带了就禁止走第三方镜像）。

    规则与 `GitService._env` 保持一致：
    - github.com / 空 host / gitlab.com：全局 token 或映射 token
    - 其它 host：仅当 host_tokens 显式登记

    注意：全局 git 配置里的 `http.extraHeader` 不在这里判断 ——
    它由 `GitService._global_git_auth_configured()` 单独拦（需要起子进程）。
    """
    h = (host or "").strip().lower().replace("www.", "").rstrip("/")
    mapped = ""
    if isinstance(host_tokens, dict):
        mapped = str(host_tokens.get(h) or "").strip()
    tok = str(token or "").strip()
    if h in ("", "github.com", "gitlab.com", "gitlab"):
        return bool(mapped or tok)
    return bool(mapped)
