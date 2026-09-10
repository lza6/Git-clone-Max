# -*- coding: utf-8 -*-
"""统一 Git URL 解析器：多平台直克隆 + 作者__仓库 命名。

设计目标（对应 计划书/下一步改进指南.md G01-1/G01-2/G01-6）：
- 把「只认 github.com」升级为通用 Git 托管平台解析器。
- 支持格式：HTTPS / SSH（git@host:o/r 与 ssh://git@host/o/r）/ 短格式 owner/repo。
- 支持子组路径（gitlab 的 group/subgroup/repo）：取最后两段为 owner/repo。
- host 白名单：github.com / gitlab.com / gitee.com / codeberg.org / bitbucket.org / git.sr.ht。
- 命名升级：跨 host 时 folder_name = host__owner__repo；github 保持 owner__repo 兼容。
- 兼容旧 API：parse_repo_url / is_github_url / normalize_url / host_of 全部保留。
"""
from __future__ import annotations

import re
from typing import List, Tuple
from urllib.parse import urlparse

from ..models import RepoSpec

# 允许的 Git 托管平台（新增平台只需在此登记；host 判定与命名共用）
KNOWN_HOSTS = (
    "github.com", "gitlab.com", "gitee.com", "codeberg.org",
    "bitbucket.org", "git.sr.ht",
)
# 仅这些平台走"跨 host 前缀命名"；其余域名主机直接用 host 名
GITHUB_HOST = "github.com"

# 通用 HTTPS：https://host/group/sub/.../owner/repo(.git)(/…)
_HTTPS_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?"
    r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:\.[A-Za-z]{2,})+)"   # host（域名）
    r"/([^/\s]+(?:/[^/\s]+)*?)"                                          # path
    r"(?:\.git)?(?:/.*)?$",
    re.IGNORECASE,
)
# SSH scp 风格：git@host:owner/repo(.git)(/…) 或 ssh://git@host/o/r
_SSH_SCP_RE = re.compile(
    r"^(?:ssh://)?git@([^:/]+)[:/]([^/\s]+(?:/[^/\s]+)*?)(?:\.git)?(?:/.*)?$",
    re.IGNORECASE,
)
# 短格式仅接受 owner/repo（无 host）；owner 含域名点段 → 判为「带 host 的完整地址」由上面处理
# 可选 @tag 后缀：owner/repo@v1.2.0
_SHORT_RE = re.compile(
    r"^([A-Za-z0-9_][A-Za-z0-9_.-]{0,38})/([A-Za-z0-9_][A-Za-z0-9_.-]{0,38}?)(?:\.git)?(?:@[^\s/]+)?(?:/.*)?$"
)

_INVALID_DIR_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_name(name: str) -> str:
    """清洗不可用作文件名的字符。"""
    return _INVALID_DIR_CHARS.sub("_", name).strip().strip(".") or "repo"


def _is_known_host(host: str) -> bool:
    return (host or "").lower() in KNOWN_HOSTS


# G08-2 @tag 合法性校验（F3）：拒绝空白/控制字符/路径穿越/前缀 - /git 保留前缀
_INVALID_REF_RE = re.compile(
    r"[\x00-\x1f\x7f\s~^:?*\[\\]|^\.\.|\.\.$|^\.$|^git$|^refs/|^-"
)


def _is_valid_ref(ref: str) -> bool:
    """校验 @tag 分支/标签名是否合法（近似 git check-ref-format --branch 语义）。"""
    ref = (ref or "").strip()
    if not ref:
        return False
    if ref.startswith("-"):
        return False
    if _INVALID_REF_RE.search(ref):
        return False
    if ref.startswith(".") or ref.endswith("."):
        return False
    if ref.endswith("/") or "//" in ref:
        return False
    if ".." in ref:
        return False  # 路径穿越
    return True


def _extract_tag_suffix(raw: str) -> str:
    """G08-2 从输入提取 @tag 后缀（仓库名后最后一个 @…）。

    仅接受「仓库路径之后」的 @tag（如 owner/repo@v1.2.0、git@host:o/r.git@v1）。
    host 前的 @（凭据前置）不在仓库路径后 → 返回空。
    """
    raw = (raw or "").strip()
    if "@" not in raw:
        return ""
    # 去掉可能的协议与 host 段后再找路径段后的 @
    cleaned = raw.split("://", 1)[-1]
    # 去掉 ssh 的 git@host: 前缀
    if cleaned.startswith("git@"):
        idx = cleaned.find(":")
        if idx >= 0:
            cleaned = cleaned[idx + 1:]
    # 现在 cleaned 形如 owner/repo@v1 或 owner/repo.git@v1（SSH 的 git@host 前缀
    # 已在上面去掉；但 ssh://git@host/… 形式的 @ 尚未处理，用「@ 前必须有 /」判定）
    at_idx = cleaned.rfind("@")
    if at_idx < 0:
        return ""
    if "/" not in cleaned[:at_idx]:
        return ""  # @ 出现在路径开始前（ssh 的 user@host）→ 非 tag
    tag = cleaned[at_idx + 1:].strip()
    # 去掉 query/fragment
    tag = tag.split("?", 1)[0].split("#", 1)[0]
    if not tag:
        return ""
    return tag


def _match_generic(raw: str):
    """尝试多种格式解析，返回 (host, *path_segments) 或 None。

    返回段为 host 之后完整的 path 段列表（含子组），后续由调用方决定
    取最后两段还是保留全段重建 URL。"""
    raw = raw.strip()
    if not raw:
        return None

    # HTTPS / 无协议完整地址（含域名）
    m = _HTTPS_RE.match(raw)
    if m:
        host = m.group(1).lower()
        # 手工切分：取 host 后完整 path（含子组），避免正则分组被截断
        auth = m.string
        host_tok = m.group(1) + "/"
        idx = auth.find(host_tok)
        if idx < 0:
            idx = auth.lower().find(host_tok.lower())
        rest = auth[idx + len(host_tok):] if idx >= 0 else ""
        rest = rest.split("?", 1)[0].split("#", 1)[0]
        segments = []
        for seg in rest.split("/"):
            seg = seg.rstrip(".")
            if seg.endswith(".git"):
                seg = seg[:-4]  # 只剥精确的 .git 后缀，绝不 rstrip 字符集
            if seg:
                segments.append(seg)
        # 仓库详情页动作段（GitHub 保留路径）：命中后取动作段之前的仓库路径。
        # 覆盖 tree/blob/raw/releases/actions/wiki/issues/pulls/commits/network/tags。
        _ACTION_SEG = {"tree", "blob", "raw", "releases", "actions", "wiki",
                       "issues", "pulls", "commits", "network", "tags", "src"}
        # 动作段出现在仓库路径之后（即前面已有 ≥2 个段）→ 仓库名是动作段之前那段，
        # 无论动作段后是否还有内容都剥离（/actions 2段、/releases/latest 4段都处理）
        for i, s in enumerate(segments):
            if s in _ACTION_SEG and i >= 2:
                segments = segments[:i]
                break
        # G08-2 把 @tag 从最后一段 repo 剥出（repo@v1.2.0 → repo + tag 后缀由调用方提取）
        if segments:
            last = segments[-1]
            at = last.rfind("@")
            if 0 < at < len(last) - 1:
                segments[-1] = last[:at]
        if not segments:
            return None
        return host, segments

    # SSH scp 风格：git@host:owner/repo(.git)(/…) 或 ssh://git@host/o/r
    m = _SSH_SCP_RE.match(raw)
    if m:
        host = m.group(1).lower()
        # host 之后的分隔符：: 或 /（scp 或 ssh:// 形式）
        rest = m.string
        host_tok = m.group(1)
        idx = rest.find(host_tok)
        if idx >= 0:
            after = rest[idx + len(host_tok):]
            if after[:1] in (":", "/"):
                after = after[1:]
            tail = after.split("?", 1)[0].split("#", 1)[0]
            # 先剥末段 @tag（repo.git@v1 → repo.git），再剥 .git
            tail = tail.rstrip("/")
            at = tail.rfind("@")
            if at > 0:
                tail = tail[:at]
            segments = []
            for seg in tail.split("/"):
                if seg.endswith(".git"):
                    seg = seg[:-4]
                if seg:
                    segments.append(seg)
            if segments and segments[-1] in ("tree", "blob"):
                segments.pop()
            if segments:
                return host, segments
        # 兜底：直接用正则捕获的 path 段
        parts = [p for p in (m.group(2) or "").split("/") if p]
        if len(parts) < 2:
            return None
        return host, parts

    # 短格式 owner/repo（无 host → 默认 github.com）
    m = _SHORT_RE.match(raw)
    if m:
        owner, repo = m.group(1), m.group(2)
        if "." in owner:
            return None  # 含域名点段 → 非短格式，交给 HTTPS 分支判定
        return GITHUB_HOST, [owner, repo]

    return None


def parse_any_repo_url(raw: str) -> RepoSpec | None:
    """解析任意 Git 托管平台仓库地址 → RepoSpec；非法返回 None。

    - 子组路径（gitlab a/x/repo）保留完整 path 段重建 URL：克隆到对的那个仓库。
    - 命名：github owner__repo；跨 host host__owner__repo（取最后两段）。
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    got = _match_generic(raw)
    if got is None:
        return None
    host, segments = got
    # F1 安全加固：仅接受白名单 host（token 不会发给未知域名）。
    # 自建主机的合法仓库仍可通过「本地扫描导入」纳入管理，不走此白名单入口。
    if not _is_known_host(host):
        return None
    if len(segments) < 2:
        return None
    owner, repo = segments[-2], segments[-1]
    # 凭据前置式主机覆盖（git 生态已知）：owner/repo 段含 '@' → 拒绝，
    # 防止 `https://github.com/attacker@evil/r` 把 URL 重拼后 token 发往恶意主机。
    # G08-2 @tag 例外：仅当 @ 位于仓库名之后（owner/repo@v1）才允许，
    # 此时 repo 段不含 @（正则已把 @tag 拆到 tag 后缀），只需再次确认。
    # F2 加固：所有 path 段（含子组前缀）含 @ 一律拒绝，避免绕过。
    # （合法的 owner/repo@tag 已在 _match_generic 中把 @tag 从段剥离，故段内无 @；
    #  SSH 的 git@host 前缀也在切分时去除。）
    if any("@" in s for s in segments):
        return None
    # 段 == '..' 或 '.git' 等边界：无法确定真实仓库 → 拒绝，避免静默改写仓库
    for seg in (owner, repo):
        if seg in ("..", ".", ".git"):
            return None
    owner = sanitize_name(owner)
    repo = sanitize_name(repo)
    if not owner or not repo:
        return None
    # 重建 URL 时保留分组前缀段，避免 gitlab 子组被折叠成错误仓库
    prefix = segments[:-2]
    path = [*prefix, owner, repo]
    url_https = f"https://{host}/{'/'.join(path)}.git"
    # G08-2 @tag：从原始输入提取标签后缀；F3 非法 ref 视为无 tag
    ref = _extract_tag_suffix(raw)
    if not _is_valid_ref(ref):
        ref = ""
    # 不同 @tag 应克隆到不同目录，避免覆盖（owner__repo@v1 vs owner__repo@v2）
    if host == GITHUB_HOST:
        folder = f"{owner}__{repo}" if not ref else f"{owner}__{repo}@{ref}"
    else:
        folder = (f"{host}__{owner}__{repo}" if not ref
                  else f"{host}__{owner}__{repo}@{ref}")
    return RepoSpec(owner=owner, repo=repo, url_https=url_https,
                    folder_name=folder, ref=ref)


def parse_repo_url(raw: str) -> RepoSpec | None:
    """旧 API：保留 GitHub 语义（非 GitHub 一律返回 None），供既有 UI/测试使用。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    # 仅 github 才走旧解析（短格式默认 github.com 也算）
    got = _match_generic(raw)
    if got is None:
        return None
    host, segments = got
    if host != GITHUB_HOST or len(segments) < 2:
        return None
    owner, repo = segments[-2], segments[-1]
    if "@" in owner or "@" in repo:
        return None
    owner = sanitize_name(owner)
    repo = sanitize_name(repo)
    if not owner or not repo:
        return None
    return RepoSpec(owner=owner, repo=repo,
                    url_https=f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


def parse_urls(text: str) -> Tuple[List[RepoSpec], List[str]]:
    """批量解析多行文本（每行一个地址）。

    返回 (有效规格列表, 无效行列表)。空行与纯空白行不算无效。
    多平台直克隆：任何 KNOWN_HOSTS 平台都算有效。
    """
    valid: List[RepoSpec] = []
    invalid: List[str] = []
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        spec = parse_any_repo_url(raw)
        if spec:
            valid.append(spec)
        else:
            invalid.append(raw.strip())
    return valid, invalid


def normalize_url(raw: str) -> str | None:
    spec = parse_any_repo_url(raw)
    return spec.url_https if spec else None


def is_github_url(raw: str) -> bool:
    """旧 API：是否 GitHub 仓库（严格）。"""
    raw = (raw or "").strip()
    if not raw:
        return False
    got = _match_generic(raw)
    if got is None:
        return False
    host, _ = got
    return host == GITHUB_HOST


def is_git_url(raw: str) -> bool:
    """是否任意受支持平台的 Git 仓库地址。"""
    return parse_any_repo_url(raw) is not None


def host_of(raw: str) -> str:
    """返回规范化 host（github.com / gitlab.com / 本地路径等）。

    - 无协议短格式（owner/repo）→ github.com
    - https/ssh 完整地址 → 主域名（去 www.）
    - 其他 URL → 其 hostname
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    got = _match_generic(raw)
    if got is not None:
        host, _ = got
        return host
    # 通用 URL 兜底
    p = urlparse(raw if "://" in raw else "https://" + raw)
    host = (p.hostname or "").lower().replace("www.", "")
    return host
