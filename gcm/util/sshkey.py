"""仓库级 SSH 私钥路径的校验（G80-B8 / 用户决策 U-2）。

设计边界（**只存路径，不存内容**）
--------------------------------
- 项目在 `repos.ssh_key` 里只保存**私钥文件的路径**，绝不保存私钥内容；
- 因此这里的校验会拒绝「看起来是私钥本体」的输入（含 `BEGIN ... PRIVATE KEY`），
  否则用户把整段 key 粘进来会被静默写进数据库 —— 那是最糟的一种"能跑但危险"。
- 校验通过不代表这把 key 真能用（那要等 git 实际连接）；这里只挡「明显会失败的输入」。

为什么单独成模块：`gcm/ui` 与 `gcm/app` 都要用同一套判断（CLAUDE.md §3「单一真相」），
而校验逻辑不依赖 Qt，放 `util` 层最合适。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

# 常见私钥文件头（OpenSSH / PEM / PuTTY）
_KEY_MARKERS = (b"PRIVATE KEY", b"PuTTY-User-Key-File")


def validate_ssh_key_path(raw: str) -> tuple[bool, str]:
    """校验「仓库级 SSH 私钥文件的路径」。返回 (ok, 错误说明)；ok=True 时第二项为空串。"""
    text = str(raw or "").strip()
    if not text:
        return (False, "路径为空")
    if "-----BEGIN" in text or "\n" in text:
        return (False, "这里要填私钥的文件路径，不是私钥内容（内容不会被保存）")
    path = Path(text).expanduser()
    if not path.exists():
        return (False, f"私钥文件不存在：{text}")
    if path.is_dir():
        return (False, f"这是目录，不是私钥文件：{text}")
    try:
        head = path.read_bytes()[:400]
    except OSError as e:
        return (False, f"无法读取私钥文件：{e}")
    if not any(marker in head for marker in _KEY_MARKERS):
        return (False, "该文件看起来不是 OpenSSH/PEM/PuTTY 私钥（缺少私钥头）")
    return (True, "")


def permission_warning(path: str | Path) -> str:
    """POSIX 下私钥权限过宽时返回提示（**只提示不阻断**）；无需提示时返回空串。"""
    if os.name == "nt":
        return ""
    try:
        mode = stat.S_IMODE(Path(path).expanduser().stat().st_mode)
    except OSError:
        return ""
    if mode & 0o077:
        return f"私钥权限过宽（{oct(mode)}），建议 chmod 600"
    return ""
