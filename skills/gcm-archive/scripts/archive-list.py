"""gcm-archive 技能：批量归档仓库 HEAD → zip（复用 gcm/git/archive.py）。

用法：python skills/gcm-archive/scripts/archive-list.py <urls.txt> --out <dir> [--ref HEAD]
输出：统一 JSON（契约 §2 对齐）；退出码 0/1/2。
不触网：本地路径输入即 file:// 裸仓。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 1:
        print("用法：archive-list.py <urls.txt> --out <dir> [--ref HEAD]")
        return 2
    src = Path(args[0])
    out_dir = Path.cwd() / "zips"
    ref = "HEAD"
    if "--out" in args:
        i = args.index("--out")
        out_dir = Path(args[i + 1])
    if "--ref" in args:
        i = args.index("--ref")
        ref = args[i + 1]
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [ln.strip() for ln in src.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    from gcm.app.url_lib import parse_any_repo_url
    from gcm.git.archive import download_archive
    from gcm.util.redact import redact

    results, invalid = [], []
    for raw in lines:
        spec = parse_any_repo_url(raw)
        if spec is None:
            p = Path(raw)
            if p.exists() and (str(p).endswith(".git") and p.is_dir() or (p / ".git").is_dir()):
                spec = None  # 本地仓：直接按路径归档
                target = f"{p.resolve().stem}.zip"
                try:
                    z = download_archive(str(p), out_dir / target, ref=ref)
                    results.append({"url": redact(raw), "status": "success",
                                    "action": "archived", "path": str(z),
                                    "message": "归档完成", "code": 0})
                    continue
                except Exception as e:  # noqa: BLE001
                    results.append({"url": redact(raw), "status": "failed",
                                    "action": "archived", "path": "",
                                    "message": f"归档失败：{e}", "code": 1})
                    continue
            invalid.append({"code": 2, "message": f"无法解析：{raw}",
                            "redacted": redact(f"无法解析：{raw}")})
            continue
        target = f"{spec.folder_name}@{ref}.zip"
        try:
            z = download_archive(spec.url_https, out_dir / target, ref=ref)
            results.append({"url": redact(spec.url_https), "status": "success",
                            "action": "archived", "path": str(z), "message": "归档完成",
                            "code": 0})
        except Exception as e:  # noqa: BLE001
            results.append({"url": redact(spec.url_https), "status": "failed",
                            "action": "archived", "path": "",
                            "message": f"归档失败：{e}", "code": 1})
    ok = sum(1 for r in results if r["status"] == "success")
    fail = sum(1 for r in results if r["status"] == "failed")
    doc = {
        "version": "8.0.2",
        "ok": fail == 0 and bool(results),
        "counts": {"success": ok, "failed": fail, "other": len(results) - ok - fail,
                   "total": len(results)},
        "results": results,
        "invalid": invalid,
    }
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 1 if fail else (0 if results else 2)


if __name__ == "__main__":
    sys.exit(main())
