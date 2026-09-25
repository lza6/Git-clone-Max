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


if __name__ == "__main__":
    sys.exit(main())
