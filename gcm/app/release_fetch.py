"""GitHub Release 附件下载（Rev17 Batch C）。

为什么是独立模块：主链路是 **git 克隆**（走 `gcm/git/service.py`），release
附件 / raw 直链是 **HTTP 文件下载**（走 GitHub API + browser_download_url），
子进程模型完全不同。单独一层，不污染 GitService。

蓝本与取舍（参考项目对标结论）：
- asset 平台匹配（os/arch 正则 + anti 规则 + 优先级）来自 eget `detect.go`
  的 DetectorChain 思想 —— 这里只取本机 Windows/当前平台的**单平台匹配**，
  不做多平台选择器链（桌面工具只给自己下）；
- 断点续传语义表（.part + Range + 206 起始偏移校验 + 416 收尾 + 本地 OSError
  与网络错误分离）来自 web-crawler `_download.py` —— 那套状态机是做下载
  可靠性前必读的规范，这里按同一语义实现（文件级而非分片级：release 附件
  通常 ≤ 200MB，单流 + 续传已经够，Range 分片并发留给未来有证据再开）；
- 重试判据复用 `app/errors.classify_error`（唯一来源红线）。

零 LLM、零付费 API；token 经 `updater._gh_token()` 同源取法（Authorization
只发给 api.github.com / github.com）。
"""
from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_USER_AGENT = "Git-clone-Max"
_PART_SUFFIX = ".part"
#: 断点续传单次失败后的瞬时重试次数（网络类错误才退避；本地 OSError 不重试）
_NET_RETRIES = 2
_RETRY_DELAYS = (2.0, 6.0)

# --- 平台匹配（eget detect.go 单平台版）------------------------------------
# anti 规则先判：命中即排除（eget 同款语义）
_ASSET_ANTI = re.compile(r"checksums?|\.sig$|\.sbom$|sha256|\.txt$|\.json$|source", re.I)
# 词必须带边界/后缀上下文：裸 `win` 会误中 darwin（dar**win**），裸 `64` 会误中 arm64。
_ASSET_OS = {
    "windows": re.compile(r"windows|win(?:32|64|dows)[-._]|[-._]win[-._]|\.exe$|\.msi$",
                          re.I),
    "linux": re.compile(r"linux|\.AppImage$|\.deb$|\.rpm$|[-._]linux[-._]", re.I),
    "darwin": re.compile(r"darwin|macos|osx|\.dmg$|\.pkg$|[-._]mac[-._]", re.I),
}
_ASSET_ARCH = {
    "amd64": re.compile(r"x86_64|amd64|[-._]x64[-_.]|[-._]64[-_.]", re.I),
    "arm64": re.compile(r"aarch64|arm64|[-._]arm64[-_.]", re.I),
}
#: 无 OS 词时的兜底扩展名（绿色二进制 / 通用压缩包）
_GENERIC_OK = re.compile(r"\.(zip|7z|tar\.gz|tgz|tar\.xz|tar\.bz2|exe|msi)$", re.I)


@dataclass
class ReleaseAsset:
    """一个 release 附件的最小元数据。"""
    name: str
    url: str
    size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "url": self.url, "size": self.size}


def _platform_tags() -> tuple[str, str]:
    if os.name == "nt":
        return "windows", "amd64"
    import platform
    m = platform.system().lower()
    os_tag = {"windows": "windows", "linux": "linux", "darwin": "darwin"}.get(m, m)
    return os_tag, (platform.machine().lower() or "amd64")


def pick_asset(assets: list[dict[str, Any]],
               os_tag: str = "", arch_tag: str = "") -> ReleaseAsset | None:
    """从 release assets 里挑本机平台最匹配的一个（eget 单平台版）。

    匹配顺序：anti 排除 → OS 命中（有 anti 的 OS 词不算）→ arch 加权 →
    通用压缩包兜底。全不中返回 None（调用方报「没有本机平台资产」）。
    """
    os_tag = os_tag or _platform_tags()[0]
    arch_tag = arch_tag or _platform_tags()[1]
    os_re = _ASSET_OS.get(os_tag)
    arch_re = _ASSET_ARCH.get(arch_tag)
    scored: list[tuple[int, Any]] = []

    def _field(a: Any, key: str) -> Any:
        """dict 或 ReleaseAsset 双形态取值（CLI 直传 ReleaseAsset 列表也成立）。"""
        if isinstance(a, dict):
            return a.get(key)
        return getattr(a, key, None)

    for a in assets:
        name = str(_field(a, "name") or "")
        if not name or _ASSET_ANTI.search(name):
            continue
        score = 0
        if os_re and os_re.search(name):
            score += 2
        elif any(rx.search(name) for tag, rx in _ASSET_OS.items() if tag != os_tag):
            continue  # 明确写了**别的**平台 → 排除（无平台词的中性资产不排除）
        if arch_re and arch_re.search(name):
            score += 1
        if _GENERIC_OK.search(name) and score == 0:
            score = 1  # 无平台词的通用压缩包：兜底候选，低于明确命中
        if score:
            scored.append((score, a))
    if not scored:
        return None
    scored.sort(key=lambda kv: (-kv[0], str(_field(kv[1], "name") or "")))
    best = scored[0][1]
    if isinstance(best, ReleaseAsset):
        return best
    return ReleaseAsset(name=str(_field(best, "name") or ""),
                        url=str(_field(best, "browser_download_url")
                                or _field(best, "url") or ""),
                        size=int(_field(best, "size") or 0))


def latest_release_assets(repo: str, token: str = "",
                          timeout: float = 15.0) -> tuple[str, list[ReleaseAsset]]:
    """取 owner/repo 最新 release 的 (tag, assets)。匿名走限流；token 只发 api.github.com。"""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json_loads(resp.read())
    tag = str(data.get("tag_name") or "")
    assets = [a for a in (data.get("assets") or []) if isinstance(a, dict)]
    return tag, [ReleaseAsset(name=str(a.get("name") or ""),
                              url=str(a.get("browser_download_url") or ""),
                              size=int(a.get("size") or 0))
                 for a in assets]


def json_loads(b: bytes) -> dict[str, Any]:
    import json
    return json.loads(b.decode("utf-8", errors="replace"))


def download_asset(asset: ReleaseAsset, dest_dir: str | Path,
                   token: str = "", on_bytes: Callable[[int], None] | None = None,
                   should_abort: Callable[[], bool] | None = None,
                   timeout: float = 60.0) -> Path:
    """下载一个附件到 dest_dir，带断点续传。

    语义表（web-crawler `_download.py` 同款）：
    - 半成品一律 `<dest>.part`，成功后 `os.replace` 原子落盘；
    - 续传请求带 `Range: bytes=<已写字节>-`；**206 起始偏移必须等于已写字节**
      （错位宁可重下，绝不静默拼接损坏文件）；
    - 服务器忽略 Range 返回 200 → 视为整文件重下（清空 .part）；
    - 416 且本地已有完整文件（大小 == asset.size）→ 直接收尾（已下完）；
    - **本地 OSError 不重试**（磁盘满/权限是结构性的，重试只是烧时间）；
      网络类错误按 _RETRY_DELAYS 退避重试 _NET_RETRIES 次。
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / asset.name
    part = out.with_name(out.name + _PART_SUFFIX)
    written = part.stat().st_size if part.exists() else 0
    if asset.size and written > asset.size:
        part.unlink(missing_ok=True)
        written = 0
    for attempt in range(_NET_RETRIES + 1):
        if should_abort and should_abort():
            raise RuntimeError("已取消")
        headers = {"User-Agent": _USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if written:
            headers["Range"] = f"bytes={written}-"
        try:
            req = urllib.request.Request(asset.url, headers=headers)
            try:
                resp_ctx = urllib.request.urlopen(req, timeout=timeout)
            except urllib.error.HTTPError as he:
                if he.code == 416:
                    if asset.size and written == asset.size:
                        break  # 已完整：直接收尾（416 收尾分支）
                    part.unlink(missing_ok=True)
                    written = 0
                    continue  # 不完整却被 416 → 重下
                raise  # 其余 HTTP 错误走统一重试/抛出
            with resp_ctx as resp:
                status = resp.status
                if status == 206:
                    cr = str(resp.headers.get("Content-Range") or "")
                    m = re.match(r"bytes\s+(\d+)-", cr)
                    if not m or int(m.group(1)) != written:
                        # 错位 → 丢弃 .part 重下（宁重下不损坏）
                        part.unlink(missing_ok=True)
                        written = 0
                        continue
                elif status == 200 and written:
                    # 服务器忽略 Range → 整文件重下
                    part.unlink(missing_ok=True)
                    written = 0
                with open(part, "ab") as f:
                    while True:
                        if should_abort and should_abort():
                            raise RuntimeError("已取消")
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
                        if on_bytes:
                            on_bytes(len(chunk))
                break  # 正常读完
        except OSError:
            # 本地 IO 错误（磁盘满/权限）不重试 —— 与网络错误分离（web-crawler 语义）。
            # .part 保留：续传语义下重跑从已写字节继续，删除反而丢进度。
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            # 末次重试仍失败 → 抛出（上层用 classify_error 语义给用户可读提示）
            if attempt < _NET_RETRIES:
                time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
    if asset.size and written != asset.size:
        raise RuntimeError(
            f"下载不完整：{written}/{asset.size} 字节（{asset.name}）")
    os.replace(part, out)
    return out
