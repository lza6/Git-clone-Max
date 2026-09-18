"""应用自更新：检查 GitHub Releases 最新版，提示跳转下载。

不做自动覆盖安装（避免 UAC/杀软/在跑进程锁文件等坑），只做『发现新版 → 提示』。
版本号统一读自 gcm/__init__.__version__。
"""
from __future__ import annotations

import json
import re
import time

from .. import __version__

_URI = "https://api.github.com/repos/lza6/Git-clone-Max/releases/latest"
_TIMEOUT = 15


def _gh_token() -> str:
    """从 git credential manager 取 GitHub token（未登录时返回空串 → 走匿名限流）。"""
    import subprocess
    try:
        r = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=10,
        )
        for line in (r.stdout or "").splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    except Exception:
        pass
    return ""


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


def extract_release_sha256(body: str) -> str:
    """G44-3 从 Release body 提取首个 sha256（64 位 hex），无则返回空串。

    通用正则（不绑定固定前缀），可命中 `sha256: `XXXX`` / `exe sha256=...` 等写法。
    """
    if not isinstance(body, str) or not body:
        return ""
    m = re.search(r"[0-9a-f]{64}", body, re.IGNORECASE)
    return m.group(0).lower() if m else ""


def check_latest_with_sha(fetcher=None, timeout: int = _TIMEOUT) -> tuple:
    """G44-3 更新检查 + Release body sha256 提取（五元组，含最新 Release body）。

    返回 (has_new, latest_version, download_url, error, sha256)：
    - sha256 从 Release body 提取（发布方公布值，供用户手动核对）；
    - fetcher 注入时须返回 Release JSON 文本（含 body 字段），便于测试。
    """
    if fetcher is None or getattr(fetcher, "__gcm_plain__", False):
        # 无注入：先用标准流程，再补一次带 body 的抓取
        has_new, ver, url, err = check_latest(fetcher=fetcher, timeout=timeout)
        sha = ""
        if not err and url:
            # Release API 响应含 body：复用默认 fetcher 拿 JSON 提取
            try:
                import urllib.request
                req = urllib.request.Request(_URI, headers={
                    "Accept": "application/vnd.github+json", "User-Agent": "Git-clone-Max"})
                tok = _gh_token()
                if tok:
                    req.add_header("Authorization", f"Bearer {tok}")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                body = (json.loads(raw) or {}).get("body") or ""
                sha = extract_release_sha256(body)
            except Exception:
                sha = ""
        return (has_new, ver, url, err, sha)
    # 注入 fetcher：直接解析 JSON 的 body 字段
    try:
        raw = fetcher()
        data = json.loads(raw) if isinstance(raw, str) else raw
        body = (data or {}).get("body") or ""
        sha = extract_release_sha256(body)
        has_new, ver, url, err = check_latest(fetcher=lambda: raw, timeout=timeout)
        return (has_new, ver, url, err, sha)
    except Exception as e:
        return (False, "", "", f"检查更新失败：{e}", "")


def check_latest(fetcher=None, timeout: int = _TIMEOUT) -> tuple:
    """查询最新 Release。fetcher 可注入（测试用）。

    返回 (has_new, latest_version, download_url, error)。
    has_new 表示最新版严格高于当前 __version__。
    """
    if fetcher is None:
        import urllib.request

        def _default():
            token = _gh_token()
            headers = {"Accept": "application/vnd.github+json",
                       "User-Agent": "Git-clone-Max"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            req = urllib.request.Request(_URI, headers=headers)
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
    except Exception as first_err:
        # G21-3：GitHub API 偶发 5xx/抖动，单次重试（2s）后再放弃
        time.sleep(2)
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
            return (False, "", "", f"检查更新失败：{e}（首次：{first_err}）")


if __name__ == "__main__":
    ok, ver, url, err = check_latest()
    print(f"当前版本：{__version__}")
    if err:
        print(err)
    elif ok:
        print(f"发现新版本：{ver}  →  {url}")
    else:
        print(f"已是最新：{ver or __version__}")
