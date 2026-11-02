"""gcm-clone 技能冒烟：本地裸仓 E2E，验证 CLI --json 全链路。

不触网：全部用临时目录本地裸仓（file://）。成功输出 GCM-CLONE SMOKE OK。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_remote(tmp: Path, name: str) -> Path:
    remote = tmp / name
    work = tmp / f"work_{name}"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    work.mkdir()
    _git(work, "init")
    _git(work, "config", "user.email", "t@t.com")
    _git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("hi " + name, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                   cwd=remote, check=True, capture_output=True)
    return remote


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gcm_clone_smoke_"))
    remotes = [_init_remote(tmp, f"r{i}.git") for i in range(2)]
    urls = tmp / "urls.txt"
    urls.write_text("\n".join(map(str, remotes)) + "\n", encoding="utf-8")
    out = tmp / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "gcm", "--cli", "--json", "--dir", str(out), str(urls)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    if proc.returncode != 0:
        print("SMOKE FAIL（退出码", proc.returncode, "）：", proc.stderr[-1000:])
        return 1
    doc = json.loads(proc.stdout)
    if doc["counts"]["success"] != 2 or doc["counts"]["failed"] != 0:
        print("SMOKE FAIL（counts 不符）：", doc["counts"])
        return 1
    print("GCM-CLONE SMOKE OK", doc["counts"])
    return 0



def _selftest() -> int:
    """Rev8 P1-4：自校验入口 —— 证明脚本本体可被当前解释器导入执行。

    打包 exe 环境下 sys.executable 不是 python（skills/G56-1-验收说明.md
    的遗留风险），所以先探测解释器再决定是否跑子进程自检。
    """
    import sys as _sys
    exe = _sys.executable or ""
    base = exe.lower().replace(".exe", "")
    if not base.endswith("python") and "python" not in base:
        print("SELFTEST SKIP: non-CPython interpreter (packaged exe)")
        return 0
    import importlib.util as _iu
    import pathlib as _pl
    spec = _iu.spec_from_file_location("_skill_self", _pl.Path(__file__).resolve())
    mod = _iu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    sys.exit(main())
