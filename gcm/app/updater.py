# -*- coding: utf-8 -*-
"""应用自更新：检查 GitHub Releases 最新版，提示跳转下载。

不做自动覆盖安装（避免 UAC/杀软/在跑进程锁文件等坑），只做『发现新版 → 提示』。
版本号统一读自 gcm/__init__.__version__。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .. import __version__

_URI = "https://api.github.com/repos/lza6/Git-clone-Max/releases/latest"
_TIMEOUT = 15


def _parse_version(v: str) -> tuple:
    """把 "v2.0.0-beta.1" 拆成可比较元组 (2, 0, 0, 4)。段不足补 0。"""
    m = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-.](.+))?$", (v or "").strip())
    if not m:
        return (0, 0, 0, 0)
    def _num(x):
        return int(x) if x and x.isdigit() else 0
    major, minor, patch = (_num(m.group(1)), _num(m.group(2)), _num(m.group(3)))
    # 预发/rc 版本低于正式版
    pre = (m.group(4) or "").lower()
    if pre and ("rc" in pre or "beta" in pre or "alpha" in pre or "pre" in pre):
        return (major, minor, patch, -1)
    return (major, minor, patch, 0)


def check_latest(fetcher=None, timeout: int = _TIMEOUT) -> tuple:
    """查询最新 Release。fetcher 可注入（测试用）。

    返回 (has_new, latest_version, download_url, error)。
    has_new 表示最新版严格高于当前 __version__。
    """
    if fetcher is None:
        import urllib.request

        def _default():
            req = urllib.request.Request(
                _URI, headers={"Accept": "application/vnd.github+json",
                               "User-Agent": "Git-clone-Max"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        fetcher = _default
    try:
        raw = fetcher()
        data = json.loads(raw)
        latest = str(data.get("tag_name") or "")
        assets = data.get("assets") or []
        exe = next((a for a in assets if str(a.get("name", "")).lower().endswith(".exe")), None)
        url = str(exe.get("browser_download_url") or "") if exe else \
            str(data.get("html_url") or "")
        cur = _parse_version(__version__)
        lat = _parse_version(latest)
        return (lat > cur, latest, url, "")
    except Exception as e:
        return (False, "", "", f"检查更新失败：{e}")


if __name__ == "__main__":
    ok, ver, url, err = check_latest()
    print(f"当前版本：{__version__}")
    if err:
        print(err)
    elif ok:
        print(f"发现新版本：{ver}  →  {url}")
    else:
        print(f"已是最新：{ver or __version__}")