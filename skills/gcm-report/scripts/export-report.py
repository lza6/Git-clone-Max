"""gcm-report 技能：导出仓库报表 CSV/Markdown（只读数据源）。

用法：export-report.py --out <dir> --format csv|markdown [--data-dir <dir>]
输出：JSON 汇总（退出码 0 成功 / 1 失败）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out_dir = Path.cwd() / "reports"
    fmt = "csv"
    data_dir: Path | None = None
    if "--out" in args:
        out_dir = Path(args[args.index("--out") + 1])
    if "--format" in args:
        fmt = args[args.index("--format") + 1].lower()
    if "--data-dir" in args:
        data_dir = Path(args[args.index("--data-dir") + 1])
    if fmt not in ("csv", "markdown"):
        print(json.dumps({"ok": False, "error": f"格式 {fmt} 不支持"}))
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    base = data_dir or Path(os.environ.get("GCM_DATA_DIR") or ROOT / "data")
    try:
        from gcm.db.repo_db import Database
        from gcm.reports import EXPORT_COLUMNS, export_csv, export_markdown
        db = Database(base / "repos.db")
        try:
            if fmt == "csv":
                p = out_dir / "repos_report.csv"
                rows = export_csv(db, p)
            else:
                p = out_dir / "repos_report.md"
                rows = export_markdown(db, p)
        finally:
            db.close()
        doc = {
            "version": "8.0.2",
            "ok": True,
            "counts": {"success": 1, "failed": 0, "other": 0, "total": 1},
            "results": [{"path": str(p), "rows": rows, "format": fmt,
                         "columns": list(EXPORT_COLUMNS)}],
            "invalid": [],
        }
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"version": "8.0.2", "ok": False, "results": [],
                          "invalid": [{"code": 1, "message": str(e),
                                       "redacted": str(e)}]}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
