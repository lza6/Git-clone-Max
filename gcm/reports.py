"""G07-3 报表导出：CSV（Excel 友好）与 Markdown（阅读友好）。

数据源：repos JOIN sync_history 全量。CSV 用 utf-8-sig 保证 Excel 中文不乱码。
G37-3：新增可选 filters 筛选（start/end 日期范围、host、status），
参数化查询（? 占位）防注入；None/空值不筛，向后兼容既有调用。
G44-1：导出列固定为仓库/同步元数据（owner/repo/host/status/action/message/
commits/duration_ms/started_at），不包含 token / host_tokens / editor 等敏感列
（凭据只存在于 settings.json 加密存储，不进入报表导出）。
"""
from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path

# G44-1：CSV/Markdown 导出行列（不含 token/host_tokens/editor 等敏感字段，仅供展示元数据）
EXPORT_COLUMNS: tuple[str, ...] = (
    "owner", "repo", "host", "status", "action",
    "message", "commits", "duration_ms", "started_at",
)


def _parse_date(s: str) -> datetime | None:
    """解析 'YYYY-MM-DD' 日期文本；无效返回 None。"""
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _fetch_rows(db, filters: dict | None = None) -> list[dict]:
    """拉取全部仓库 + 同步历史（含无历史的仓库）；filters 可选筛选。

    filters 支持：start/end（"YYYY-MM-DD"，作用于 started_at 起止）、
    host（r.host 精确匹配）、status（h.status 精确匹配）。
    约定：None/空值忽略；无效日期忽略；有效 start > end 返回空。
    """
    try:
        filters = filters or {}
        start = str(filters.get("start") or "").strip()
        end = str(filters.get("end") or "").strip()
        host = str(filters.get("host") or "").strip()
        status = str(filters.get("status") or "").strip()

        where, params = [], []
        d_start = _parse_date(start)
        d_end = _parse_date(end)
        if d_start and d_end and d_start > d_end:
            return []  # 起始晚于截止：不可达区间，直接无数据
        if d_start:
            where.append("h.started_at >= ?")
            params.append(f"{start} 00:00:00")
        if d_end:
            where.append("h.started_at <= ?")
            params.append(f"{end} 23:59:59")
        if host:
            where.append("r.host = ?")
            params.append(host)
        if status:
            where.append("h.status = ?")
            params.append(status)

        sql = """
            SELECT r.owner, r.repo, r.host,
                   h.status, h.action, h.message, h.commits,
                   h.duration_ms, h.started_at
            FROM repos r
            LEFT JOIN sync_history h ON h.repo_id = r.id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.owner, r.repo, h.id DESC"
        rows = db._conn.execute(sql, params).fetchall()
        out = []
        seen = set()
        for r in rows:
            d = dict(r)
            if d.get("started_at") is None:
                # 无历史仓库只保留一行
                if (d["owner"], d["repo"], None) in seen:
                    continue
                seen.add((d["owner"], d["repo"], None))
            out.append(d)
        return out
    except Exception:
        return []


def export_csv(db, out_path: str | Path, filters: dict | None = None) -> int:
    """导出 CSV 报表；返回行数（不含表头）。filters 可选，默认全量。"""
    rows = _fetch_rows(db, filters)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(EXPORT_COLUMNS))  # G44-1 导出列不含凭据
        for r in rows:
            w.writerow([
                r.get("owner") or "", r.get("repo") or "", r.get("host") or "",
                r.get("status") or "", r.get("action") or "",
                r.get("message") or "", int(r.get("commits") or 0),
                int(r.get("duration_ms") or 0), r.get("started_at") or "",
            ])
    return len(rows)


def export_markdown(db, out_path: str | Path, filters: dict | None = None) -> int:
    """导出 Markdown 报表；返回行数。filters 可选，默认全量。"""
    rows = _fetch_rows(db, filters)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Git-clone-Max 同步报表",  # G44-1 表头列同 EXPORT_COLUMNS，不含凭据
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

# --------------------------------------------------------------- G47-3 xlsx 导出
EXPORT_COLUMNS_XLSX = ("owner", "repo", "host", "local_path", "folder_name",
                       "last_sync_at", "status", "action", "commits", "message")


def export_xlsx(db, out_path: str | Path, filters: dict | None = None) -> int:
    """G47-3 导出 xlsx（openpyxl 可选依赖）：自动列宽 + 冻结首行 + 汇总行。

    openpyxl 缺失时抛带安装提示的 RuntimeError（不静默降级，避免用户误以为已导出）。
    """
    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
    except ImportError as e:  # pragma: no cover - 依赖缺失提示
        raise RuntimeError(
            "导出 xlsx 需要 openpyxl，请先执行：pip install openpyxl（或用 CSV/Markdown）"
        ) from e
    rows = _fetch_rows(db, filters)
    wb = Workbook()
    ws = wb.active
    ws.title = "repos"
    headers = list(EXPORT_COLUMNS_XLSX)
    ws.append(headers)
    for r in rows:
        ws.append([r.get(k, "") for k in headers])
    # 自动列宽（中文按 2 倍估算）+ 冻结首行
    for col_idx, name in enumerate(headers, start=1):
        width = max(10, min(40, max(
            (len(str(name)) * 2 + 2),
            max((len(str(r.get(name, ""))) * 2 + 2) for r in rows if rows) or 12,
        )))
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.freeze_panes = "A2"
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out))
    return len(rows)
