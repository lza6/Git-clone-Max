# -*- coding: utf-8 -*-
"""正式 Release 发布脚本（PyGithub）。

用法：
    python scripts/publish_release.py --dry-run      # 校验本地产物 + 远端状态，不上传
    python scripts/publish_release.py --tag v2.0.0   # 打 tag 并创建 Release 上传 exe

依赖 requirements-build.txt（PyGithub）。token 走 git credential manager。
幂等：同名 tag 的 Release 存在时更新其附件；同名附件先删除再上传。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

REPO = "lza6/Git-clone-Max"
DIST = ROOT / "dist" / "Git-clone-Max.exe"
ARTIFACT_NAME = "Git-clone-Max.exe"
BODY = (
    "GitHub 仓库批量并行下载 / 增量更新 / 入库追踪 桌面工具（PyQt6）。\n\n"
    "## 功能\n"
    "- 批量并行（可调 1–16 线程）克隆 / 增量更新，互不阻塞\n"
    "- 断点续传（progress.json 原子写）+ SQLite 入库追踪（repos + sync_history）\n"
    "- 作者__仓库命名 + 冲突保护（本地改动绝不覆盖）\n"
    "- 增量更新自动重试（网络抖动退避）+ 全局超时 + HTTP 代理支持\n"
    "- 无效地址逐行校验提示 + 下载完成自动清空输入框\n"
    "- 并发数 / 超时 / 重试 / 代理 设置持久化（settings.json）\n"
    "- 系统托盘挂机 + 检查更新（发现新版跳转下载）\n"
    "- 单文件自包含 exe（双击即用，无需安装 Python）\n\n"
    "## 使用\n"
    "- 直接下载 `Git-clone-Max.exe` 双击运行\n"
    "- 需系统已安装 git（https://git-scm.com/download/win）\n"
)


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def _get_token() -> str:
    r = subprocess.run(["git", "credential", "fill"],
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True)
    for line in (r.stdout or "").splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise RuntimeError("无法从 git credential manager 获取 GitHub token")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="发布 Git-clone-Max Release")
    ap.add_argument("--dry-run", action="store_true", help="仅校验，不上传")
    ap.add_argument("--tag", default="", help="要发布的 tag，如 v2.0.0")
    args = ap.parse_args()

    if not DIST.exists():
        print(f"[FAIL] 产物不存在：{DIST}")
        return 1
    sha = _sha256(DIST)
    size = DIST.stat().st_size
    print(f"[OK] 本地产物 {ARTIFACT_NAME} {size} bytes  sha256={sha[:16]}…")

    try:
        from github import Auth, Github  # PyGithub
    except ImportError:
        print("[FAIL] 缺少 PyGithub，请先：python -m pip install -r requirements-build.txt")
        return 1

    token = _get_token()
    gh = Github(auth=Auth.Token(token))
    repo = gh.get_repo(REPO)

    # 默认 tag：从 gcm.__init__ 读版本
    tag = args.tag
    if not tag:
        from gcm import __version__
        tag = "v" + __version__.lstrip("v")
    print(f"[INFO] tag = {tag}")

    try:
        rel = repo.get_release(tag)
        print(f"[INFO] Release {tag} 已存在 (id={rel.id})，将更新附件")
    except Exception:
        rel = None

    if args.dry_run:
        if rel is not None:
            existing = list(rel.get_assets())
            print(f"[DRY] 已存在 Release {tag}，assets={[a.name for a in existing]}")
            for a in existing:
                if a.name == ARTIFACT_NAME:
                    print(f"[DRY] 远端同名附件 size={a.size} 与本地 {size} 对比："
                          f"{'一致' if a.size == size else '不一致'}")
        else:
            print(f"[DRY] Release {tag} 不存在，将新建并上传 {size} bytes")
        print("[DRY] 校验通过，未做任何修改。")
        return 0

    # 创建或取已有 Release
    if rel is None:
        rel = repo.create_git_release(
            tag=tag, name=f"Git-clone-Max {tag}", message=BODY,
            draft=False, prerelease=False)
        print(f"[OK] Release 创建成功 id={rel.id}")

    # 幂等：同名附件先删后传
    for a in rel.get_assets():
        if a.name == ARTIFACT_NAME:
            a.delete_asset()
            print(f"[INFO] 已删除旧附件 {ARTIFACT_NAME}")

    print(f"[INFO] 上传 {ARTIFACT_NAME}（{size} bytes）…")
    asset = rel.upload_asset(
        str(DIST), content_type="application/vnd.microsoft.portable-executable")
    print(f"[OK] 附件上传成功 id={asset.id} size={asset.size}")

    # 校验
    server_sha = ""
    try:
        import urllib.request
        req = urllib.request.Request(asset.browser_download_url,
                                     headers={"Accept": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        server_sha = hashlib.sha256(data).hexdigest()
    except Exception as e:
        print(f"[WARN] 服务端下载校验跳过：{e}")

    ok = server_sha == sha
    print(f"[{'OK' if ok else 'FAIL'}] 服务端 sha256={server_sha[:16]}… "
          f"{'一致' if ok else '不一致!'}")
    print(f"[INFO] Release 页面：{rel.html_url}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
