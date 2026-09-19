"""P-perf 本地扫描基准：同机 before/after 计时 + git 子进程计数。

构造 N 个真实 git 仓库（含 origin + 一次提交），分别用：
- legacy：旧实现的逐仓 `git remote get-url origin` + `git rev-parse HEAD`（2 次子进程/仓）
- new   ：当前 scanner（config/HEAD/packed-refs 纯文件读，0 子进程/仓，线程池并发）

用法：python scripts/bench_scan.py [--repos 300] [--keep]
输出：创建耗时 / legacy(秒, 子进程数) / new(秒, 子进程数) / 加速比。
注意：legacy 在 Windows 上每个 spawn ~200ms，N=300 约需 2 分钟；请耐心等待。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app import scanner as new_scanner  # noqa: E402


# ---------------------------------------------------------------- 创建仓库
def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "t")
    _git(path, "remote", "add", "origin", "https://github.com/o/r.git")
    (path / "a.txt").write_text("v1", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "v1")


def build_repos(base: Path, n: int, workers: int = 8) -> None:
    paths = [base / f"repo{i:04d}" for i in range(n)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(make_repo, paths))


# ---------------------------------------------- legacy 副本（基准专用，非产品代码）
def _run_git_legacy(path: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(path), *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def scan_legacy(root: Path, max_depth: int = 2, counter: list = None):
    from gcm.app.scanner import _make_info  # 复用解析，但覆盖造成进程的部分
    results = []
    for dirpath, dirnames, _ in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        has_git = ".git" in dirnames
        dirnames[:] = [d for d in dirnames
                       if d not in {"node_modules", ".venv", "venv", "dist", "build",
                                    "__pycache__", ".idea", ".vscode", "site-packages"}
                       and (d == ".git" or not d.startswith("."))]
        if has_git:
            p = Path(dirpath)
            # 模拟旧实现：每次都起 2 个子进程
            url = _run_git_legacy(p, "remote", "get-url", "origin")
            sha = _run_git_legacy(p, "rev-parse", "HEAD")
            if counter is not None:
                counter[0] += 2
            # 用新 _make_info 拼 RepoInfo，但把 url/head 替换为 legacy 进程读出的值
            info = _make_info(root, p, depth)
            info.remote_url = url
            info.head_sha = sha
            results.append(info)
            continue
        if depth >= max_depth:
            dirnames[:] = []
    results.sort(key=lambda i: i.path)
    return results


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="本地扫描 before/after 基准")
    ap.add_argument("--repos", type=int, default=300)
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="bench_scan_"))
    base = tmp / "clones"
    t0 = time.time()
    build_repos(base, args.repos)
    build_s = time.time() - t0
    print(f"[INFO] 创建 {args.repos} 个仓库（含 origin+commit）耗时 {build_s:.1f}s")

    # legacy
    counter = [0]
    t0 = time.time()
    legacy_infos = scan_legacy(base, max_depth=2, counter=counter)
    legacy_s = time.time() - t0
    print(f"[bench] legacy : {legacy_s:.2f}s | git spawns={counter[0]} | found={len(legacy_infos)}")

    # new
    t0 = time.time()
    new_infos = new_scanner.scan_git_dirs(base, max_depth=2)
    new_s = time.time() - t0
    print(f"[bench] new    : {new_s:.2f}s | found={len(new_infos)}")

    ratio = legacy_s / new_s if new_s > 0 else float("inf")
    print(f"[bench] 加速比 legacys/new ≈ {ratio:.1f}x")
    print(f"[bench] per-repo legacy {legacy_s / max(1, args.repos) * 1000:.0f}ms")
    print(f"[bench] per-repo new    {new_s / max(1, args.repos) * 1000:.0f}ms")
    if not args.keep:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
