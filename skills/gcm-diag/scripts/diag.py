"""gcm-diag 技能：网络/环境诊断（复用 gcm/app/diag.py + CLI --diag）。

用法：python skills/gcm-diag/scripts/diag.py [--json]
退出码：0=绿 / 1=黄或红。只读、不触网写操作。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in args
    cmd = [sys.executable, "-m", "gcm", "--cli", "--diag"]
    if json_mode:
        cmd.append("--json")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    out = proc.stdout.strip()
    if json_mode:
        try:
            doc = json.loads(out)
            print(json.dumps(doc, ensure_ascii=False, indent=2))
            return 0 if doc.get("ok") else 1
        except ValueError:
            print("诊断输出非 JSON：", out[:500])
            return 1
    print(out or proc.stderr)
    return proc.returncode



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
