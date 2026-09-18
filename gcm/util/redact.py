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

_URL_CRED = re.compile(r"(https?://)([^/\s@]+)@")
_BEARER = re.compile(r"(Authorization\s*:\s*)(Bearer|Basic)\s+\S+", re.IGNORECASE)
_PRIVATE = re.compile(r"(PRIVATE-TOKEN\s*:\s*)\S+", re.IGNORECASE)
# G44-4：Windows 用户目录（盘符大小写不敏感）与 POSIX /home/<用户名>
_WIN_HOME = re.compile(r"(?i)[a-z]:\\users\\[^\\/:*?\"<>|]+")
_POSIX_HOME = re.compile(r"/home/[^/\s]+")

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
    if userprofile if userprofile is not None else _REDACT_USERPROFILE:
        out = redact_home(out)
    return out
