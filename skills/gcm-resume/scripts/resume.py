"""gcm-resume 技能：断点续跑（同批 URL 重跑，引擎跳过已完成目录）。

用法：python skills/gcm-resume/scripts/resume.py <urls.txt> --dir <out>
输出：CLI --json + summary；退出码 0=无失败 / 1=仍有失败 / 2=无有效输入。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("用法：resume.py <urls.txt> --dir <out>")
        return 2
    src = args[0]
    out_dir = Path.cwd() / "clones"
    if "--dir" in args:
        out_dir = Path(args[args.index("--dir") + 1])
    proc = subprocess.run(
        [sys.executable, "-m", "gcm", "--cli", "--json", "--dir", str(out_dir), str(src)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900)
    try:
        doc = json.loads(proc.stdout or "{}")
    except ValueError:
        print("CLI 输出非 JSON：", proc.stdout[:500])
        return 2
    failed = [r for r in doc.get("results", []) if r.get("status") == "failed"]
    ok = [r for r in doc.get("results", []) if r.get("status") == "success"]
    doc["summary"] = {"remaining_failed": len(failed),
                      "already_ok": len(ok)}
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    if failed:
        print("[resume] 仍有失败：", file=sys.stderr)
        for r in failed:
            print(f"  - {r['url']}: {r['message']}", file=sys.stderr)
    return 1 if failed else (0 if doc.get("results") else 2)


if __name__ == "__main__":
    sys.exit(main())
