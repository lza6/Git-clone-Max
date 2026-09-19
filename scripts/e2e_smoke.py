"""G45-7 真实 git E2E 冒烟脚本（CI 独立 job 使用，也可本机运行）。

流程（全程本地裸仓，不依赖外网）：
1. init --bare 远端 + 初始提交 push（main）
2. clone 远端 → 校验内容正确
3. 远端新提交 → 本地 fetch + merge --ff-only → 校验更新成功
4. 本地未提交改动 vs 远端更新 → ff 被拒 且 本地内容不被覆盖（冲突保护）
5. 本地提交冲突改动 → merge 冲突（双方内容都保留）→ merge --abort 恢复本地提交

确定性：所有 git 命令带 -c core.autocrlf=false / -c user.email / -c user.name
/ -c core.editor=true，Windows/Linux/macOS 行为一致。
退出码：全部 PASS → 0；任一 FAIL → 1。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IDENT = ["-c", "user.email=ci@example.com", "-c", "user.name=CI"]
BASE = ["git", "-c", "core.autocrlf=false", "-c", "core.editor=true"]


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        BASE + IDENT + list(args),
        cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed rc={r.returncode}: {r.stderr.strip()}")
    return r


def _git_dir(gitdir: Path, *args: str) -> None:
    r = subprocess.run(
        BASE + IDENT + ["--git-dir", str(gitdir)] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(
            f"git --git-dir {' '.join(args)} failed rc={r.returncode}: {r.stderr.strip()}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gcm_e2e_smoke_"))
    fails = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal fails
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
        if not ok:
            fails += 1

    try:
        bare = tmp / "remote.git"
        _git(tmp, "init", "--bare", "-q", str(bare))
        _git_dir(bare, "symbolic-ref", "HEAD", "refs/heads/main")

        # 初始提交并 push
        origin = tmp / "origin_work"
        origin.mkdir()
        _git(origin, "init", "-q")
        (origin / "f.txt").write_text("v1\n", encoding="utf-8")
        _git(origin, "add", ".")
        _git(origin, "commit", "-q", "-m", "c1")
        _git(origin, "branch", "-M", "main")
        _git(origin, "remote", "add", "origin", str(bare))
        _git(origin, "push", "-q", "-u", "origin", "main")

        # 1. clone
        clone = tmp / "clone"
        _git(tmp, "clone", "-q", str(bare), str(clone))
        check((clone / "f.txt").read_text(encoding="utf-8") == "v1\n", "clone 内容正确")

        # 2. 远端新提交 → fetch + ff 更新
        (origin / "f.txt").write_text("v2\n", encoding="utf-8")
        _git(origin, "commit", "-q", "-am", "c2")
        _git(origin, "push", "-q", "origin", "main")
        _git(clone, "fetch", "-q", "origin")
        _git(clone, "merge", "--ff-only", "origin/main")
        check((clone / "f.txt").read_text(encoding="utf-8") == "v2\n", "fetch+ff 更新正确")

        # 3. 本地未提交改动 → 冲突保护（ff 被拒且内容不被覆盖）
        (origin / "f.txt").write_text("v3\n", encoding="utf-8")
        _git(origin, "commit", "-q", "-am", "c3")
        _git(origin, "push", "-q", "origin", "main")
        (clone / "f.txt").write_text("local-v3\n", encoding="utf-8")
        _git(clone, "fetch", "-q", "origin")
        r = _git(clone, "merge", "--ff-only", "origin/main", check=False)
        protected = (r.returncode != 0
                     and (clone / "f.txt").read_text(encoding="utf-8") == "local-v3\n")
        check(protected, "未提交改动不被覆盖（ff 被拒+内容保留）", f"rc={r.returncode}")

        # 4. 提交本地冲突 → merge 冲突双方保留 → abort 恢复
        _git(clone, "commit", "-q", "-am", "local c3")
        r2 = _git(clone, "merge", "--no-edit", "origin/main", check=False)
        content = (clone / "f.txt").read_text(encoding="utf-8")
        conflicted = (r2.returncode != 0 and "v3" in content
                      and "local-v3" in content
                      and ("<<<<<<<" in content or ">>>>>>>" in content))
        check(conflicted, "冲突双方内容都保留", f"rc={r2.returncode}")
        _git(clone, "merge", "--abort")
        check((clone / "f.txt").read_text(encoding="utf-8") == "local-v3\n",
              "merge --abort 恢复本地提交")

        # 5. HEAD 有效
        head = _git(clone, "rev-parse", "HEAD").stdout.strip()
        check(bool(head), "HEAD 存在")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("E2E_SMOKE_RESULT:", "PASS" if fails == 0 else f"FAIL({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())