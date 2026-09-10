# -*- coding: utf-8 -*-
"""G07-3 报表导出：CSV（Excel 友好）与 Markdown（阅读友好）。

数据源：repos JOIN sync_history 全量。CSV 用 utf-8-sig 保证 Excel 中文不乱码。
"""
from __future__ import annotations

import csv
import time
from pathlib import Path


def _fetch_rows(db) -> list[dict]:
    """拉取全部仓库 + 最近同步历史（含无历史的仓库）。"""
    try:
        rows = db._conn.execute(
            """
            SELECT r.owner, r.repo, r.host,
                   h.status, h.action, h.message, h.commits,
                   h.duration_ms, h.started_at
            FROM repos r
            LEFT JOIN sync_history h ON h.repo_id = r.id
            ORDER BY r.owner, r.repo, h.id DESC
            """
        ).fetchall()
        out = []
        seen = set()
        for r in rows:
            d = dict(r)
            key = (d["owner"], d["repo"], d["started_at"])
            if d.get("started_at") is None:
                # 无历史仓库只保留一行
                if (d["owner"], d["repo"], None) in seen:
                    continue
                seen.add((d["owner"], d["repo"], None))
            out.append(d)
        return out
    except Exception:
        return []


def export_csv(db, out_path: str | Path) -> int:
    """导出 CSV 报表；返回行数（不含表头）。"""
    rows = _fetch_rows(db)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["owner", "repo", "host", "status", "action",
                    "message", "commits", "duration_ms", "started_at"])
        for r in rows:
            w.writerow([
                r.get("owner") or "", r.get("repo") or "", r.get("host") or "",
                r.get("status") or "", r.get("action") or "",
                r.get("message") or "", int(r.get("commits") or 0),
                int(r.get("duration_ms") or 0), r.get("started_at") or "",
            ])
    return len(rows)


def export_markdown(db, out_path: str | Path) -> int:
    """导出 Markdown 报表；返回行数。"""
    rows = _fetch_rows(db)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Git-clone-Max 同步报表",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| owner | repo | host | 状态 | 动作 | 说明 | 提交数 | 耗时(ms) | 时间 |",
        "|-------|------|------|------|------|------|--------|----------|------|",
    ]
    _STATUS = {"success": "成功", "failed": "失败", "conflict": "冲突",
               "cancelled": "已取消", "skipped": "跳过"}
    for r in rows:
        status = _STATUS.get(str(r.get("status") or ""), str(r.get("status") or "—"))
        lines.append(
            f"| {r.get('owner') or ''} | {r.get('repo') or ''} | {r.get('host') or ''} | "
            f"{status} | {r.get('action') or '—'} | {str(r.get('message') or '')[:40]} | "
            f"{int(r.get('commits') or 0)} | {int(r.get('duration_ms') or 0)} | "
            f"{r.get('started_at') or '—'} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)
