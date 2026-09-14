"""敏感信息打码（G28-1）：任何进入日志/UI 的文本先过 redact。

覆盖三类泄露面：
1. URL 内嵌凭据  https://token@host/...  → https://***@host/...
2. HTTP 授权头    Authorization: Bearer x / Basic x
3. GitLab 私有头  PRIVATE-TOKEN: x
"""
from __future__ import annotations

import re

_URL_CRED = re.compile(r"(https?://)([^/\s@]+)@")
_BEARER = re.compile(r"(Authorization\s*:\s*)(Bearer|Basic)\s+\S+", re.IGNORECASE)
_PRIVATE = re.compile(r"(PRIVATE-TOKEN\s*:\s*)\S+", re.IGNORECASE)


def redact(text: str) -> str:
    """打码文本中的凭据；非字符串原样返回。"""
    if not isinstance(text, str) or not text:
        return text
    out = _URL_CRED.sub(r"\1***@", text)
    out = _BEARER.sub(r"\1\2 ***", out)
    out = _PRIVATE.sub(r"\1***", out)
    return out
