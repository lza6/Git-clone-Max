"""gcm-clone 技能：从 URL 列表调用 CLI --json 并给出汇总/失败清单。

用法：python skills/gcm-clone/scripts/clone-list.py <urls.txt> [--dir <out>] [--no-json]
退出码：0/1/2 与 CLI 一致（0 全成功 / 1 有失败 / 2 无有效输入）。
不触网：urls.txt 用本地路径即可当作 file:// 裸仓。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent  # 仓库根


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("用法：clone-list.py <urls.txt> [--dir <out>] [--no-json]")
        return 2
    src = args[0]
    cmd = [sys.executable, "-m", "gcm", "--cli", "--json", "--dir"]
    out_dir = Path.cwd() / "clones"
    if "--dir" in args:
        i = args.index("--dir")
        out_dir = Path(args[i + 1])
    cmd += [str(out_dir), str(src)]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    try:
        doc = json.loads(proc.stdout or "{}")
    except ValueError:
        print("CLI 输出非 JSON：", proc.stdout[:500])
        return 2
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    failed = [r for r in doc.get("results", []) if r.get("status") == "failed"]
    if failed:
        print(f"[clone-list] 失败 {len(failed)} 个：", file=sys.stderr)
        for r in failed:
            print(f"  - {r['url']}: {r['message']}", file=sys.stderr)
    return proc.returncode if proc.returncode is not None else 2


if __name__ == "__main__":
    sys.exit(main())
