"""敏感信息打码（G28-1 / G44-4）：任何进入日志/UI 的文本先过 redact。

覆盖四类泄露面：
1. URL 内嵌凭据  https://token@host/...  → https://***@host/...
2. HTTP 授权头    Authorization: Bearer x / Basic x
3. GitLab 私有头  PRIVATE-TOKEN: x
4. （G44-4，默认关）本地 home 目录路径 C:\\Users\\<用户名> / /home/<用户名> → ~

G44-4 提供两个入口（默认均不影响既有行为）：
- set_redact_userprofile(enable)：全局开关，开启后 redact() 额外打码 home 目录；
- redact_home(text, home)：独立辅助，直接打码 home 目录（可自定义 home 路径）。
"""
from __future__ import annotations

import re
from typing import Any

_URL_CRED = re.compile(r"(https?://)([^/\s@]+)@")
# G66-11：补齐 Authorization 方案词。原实现只认 Bearer|Basic，漏掉 GitHub 旧式
# `Authorization: token <x>`（以及 Token/Negotiate 等），这类文本会原样进 UI/日志。
# G67-6：凭据体用 `[^\s"']+` 而不是 `\S+` —— 后者会把紧邻的收尾引号一起吃掉，
# 例如 `curl -H 'Authorization: Negotiate TlRM'` 会变成 `...Negotiate ***`（丢了 `'`），
# 让打码后的文本看起来像被截断。
_CRED = r"[^\s\"']+"
_BEARER = re.compile(
    r"(Authorization\s*:\s*)(Bearer|Basic|Token|token|NEGOTIATE|Negotiate)\s+" + _CRED,
    re.IGNORECASE)
_PRIVATE = re.compile(r"(PRIVATE-TOKEN\s*:\s*)" + _CRED, re.IGNORECASE)
# G66-11：其它常见私有头（GitLab Job-Token / 通用 X-Auth-Token / 自建服务 X-API-Key）
_OTHER_HEADERS = re.compile(
    r"((?:X-Auth-Token|X-API-Key|JOB-TOKEN|X-Gitlab-Token)\s*:\s*)" + _CRED,
    re.IGNORECASE)
# G66-11：URL query 里的凭据参数（_URL_CRED 只处理 userinfo，query 里的 token 会漏）
# 覆盖 access_token / token / private_token / api_key / apikey / password / auth
_SENSITIVE_QPARAM = re.compile(
    r"([?&](?:access_token|private_token|refresh_token|id_token|api_key|apikey"
    r"|auth|authorization|password|passwd|pwd|token)=)[^&#\s]*",
    re.IGNORECASE)
# G44-4：Windows 用户目录（盘符大小写不敏感）与 POSIX /home/<用户名>
# G72：补齐正斜杠、UNC、旧版 Documents and Settings；
# 只 mask「用户名」这一段，保留后续子路径，便于排障。
_WIN_HOME = re.compile(
    r"(?i)(?:[a-z]:[\\/]+users|[a-z]:[\\/]+documents and settings"
    r"|\\\\[^\\/]+[\\/]+users)[\\/]+[^\\/:*?\"<>|]+")
_POSIX_HOME = re.compile(r"/home/[^/\s]+")
# G72 高置信 token 前缀（不匹配裸 40 位 hex，避免把 commit SHA 当凭据）
_TOKEN_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[abp]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
)

# G44-4 全局开关：默认关闭，不影响既有 redact 行为
_REDACT_USERPROFILE = False


def set_redact_userprofile(enable: bool) -> None:
    """G44-4 全局开关：开启后 redact() 额外打码 home 目录路径（默认关）。

    供设置页后续接入「日志打码 home 目录」开关；enable 非空即视为开启。
    """
    global _REDACT_USERPROFILE
    _REDACT_USERPROFILE = bool(enable)


def redact_home(text: str, home: str | None = None) -> str:
    """G44-4 打码 home 目录：C:\\Users\\<用户名> / /home/<用户名> → ~。

    home 提供时额外按其精确路径打码（如自定义数据目录）；省略时按常见
    home 模式自动识别任意用户名。非字符串 / 空文本原样返回。
    """
    if not isinstance(text, str) or not text:
        return text
    out = _WIN_HOME.sub("~", text)
    out = _POSIX_HOME.sub("~", out)
    if home:
        h = str(home).rstrip("/\\")
        if h:
            flags = re.IGNORECASE if "\\" in h else 0
            out = re.sub(re.escape(h), "~", out, flags=flags)
    return out


def redact(text: str, *, userprofile: bool | None = None) -> str:
    """打码文本中的凭据；非字符串原样返回。

    userprofile：True=额外打码 home 目录；False=不打码；
    None（默认）=跟随 set_redact_userprofile 全局开关（默认关）。
    """
    if not isinstance(text, str) or not text:
        return text
    out = _URL_CRED.sub(r"\1***@", text)
    out = _BEARER.sub(r"\1\2 ***", out)
    out = _PRIVATE.sub(r"\1***", out)
    out = _OTHER_HEADERS.sub(r"\1***", out)
    out = _SENSITIVE_QPARAM.sub(r"\1***", out)
    # G72：独立出现的高置信 token（不一定带 Authorization/query）
    for pat in _TOKEN_PATTERNS:
        if pat.search(out):
            out = pat.sub("***", out)
    if userprofile if userprofile is not None else _REDACT_USERPROFILE:
        out = redact_home(out)
    return out


def _redact_value(value: Any) -> Any:
    """递归打码：str 走 redact；dict/list/tuple 逐项递归；其它类型原样返回。"""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    return value


def redact_row(row: Any) -> Any:
    """G80-B1：**出站打码的唯一实现** —— 对一行记录/一个事件负载的全部文本值跑 redact。

    为什么需要它：原先 http_api 与 mcp_server 各写一份 `_sanitize_row`，且**只对 url 打码**，
    于是 `message` / `detail` 里的 `https://user:pass@host`、`Authorization: ...`、
    `ghp_xxx` 会原样进入 /api/report、CSV/xlsx、MCP 导出与 CLI stderr（违反
    `docs/agent-contract.md` §1 与 CLAUDE.md §3 的脱敏红线）。新增一个出口就漏一次，
    因此收敛成单点：**任何面向 UI / 日志 / agent 的行或负载，出站前都过这里。**

    语义：非 dict 原样返回（保持旧 `_sanitize_row` 行为）；只改文本值，
    数字/布尔/None 等保持原样，避免改变契约字段的类型。
    """
    if not isinstance(row, dict):
        return row
    return {k: _redact_value(v) for k, v in row.items()}
