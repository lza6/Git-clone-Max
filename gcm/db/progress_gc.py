"""进度文件治理（G52-4）。

data/progress.json 滚动保留 + 归档 + 超阈值裁剪：
- 每批完成时把超出保留上限的旧条目裁剪，旧条目压缩为 summary 行归档到
  data/progress_archive/（进度可追溯性不丢）；
- 文件超阈值（默认 5MB）→ 强裁：只保留最近 max_finished 条，仍超则整批
  归档重置；
- 裁剪后 progress.json 结构保持 load_progress 兼容（finished/in_progress/
  failed 三键齐全，可继续读）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

# 每批完成滚动保留的 finished 条目上限（超出裁剪为 summary 归档）
_DEFAULT_MAX_FINISHED = 200
# 文件大小阈值（字节）：默认 5MB，超阈值强裁
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
# 归档子目录名（相对 progress.json 所在目录）
_ARCHIVE_DIR = "progress_archive"


def _load_raw(path: Path) -> dict[str, Any]:
    """读取 progress.json；缺失 / 损坏 → 空结构（不抛异常）。"""
    if not path.exists():
        return {"finished": [], "in_progress": {}, "failed": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"finished": [], "in_progress": {}, "failed": []}
    except Exception:
        return {"finished": [], "in_progress": {}, "failed": []}
    data.setdefault("finished", [])
    data.setdefault("in_progress", {})
    data.setdefault("failed", [])
    return data


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    """原子写（tmp + os.replace），保证裁剪/归档过程不损坏进度文件。"""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _archive_path(progress_path: Path) -> Path:
    """归档目录：progress.json 同级 progress_archive/。"""
    return progress_path.parent / _ARCHIVE_DIR


def _summary_rows(finished: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把旧 finished 条目压缩为 summary 行（去 message 细节，保留计数）。"""
    counts: dict[str, int] = {}
    for item in finished:
        st = str(item.get("status") or "unknown")
        counts[st] = counts.get(st, 0) + 1
    return [{"status": st, "count": c} for st, c in sorted(counts.items())]


def rotate_progress(path: str | Path,
                    archive_dir: Optional[str | Path] = None,
                    max_finished: int = _DEFAULT_MAX_FINISHED,
                    max_bytes: int = _DEFAULT_MAX_BYTES,
                    force: bool = False) -> dict[str, Any]:
    """滚动治理 progress.json；返回统计结果（不抛异常）。

    - force=False：仅当 finished 超上限或文件超阈值时裁剪/归档；
    - force=True：无条件归档整批（测试用）。
    """
    p = Path(path)
    try:
        data = _load_raw(p)
        finished: list[dict[str, Any]] = list(data.get("finished") or [])
        arch_dir = Path(archive_dir) if archive_dir else _archive_path(p)
        size = p.stat().st_size if p.exists() else 0
        archived_rows: list[dict[str, Any]] = []
        trimmed = 0

        if force:
            # 无条件归档整批（保留结构完整性）
            if finished:
                archived_rows = list(finished)
                finished = []
                trimmed = len(archived_rows)
        elif len(finished) > max_finished:
            # 滚动保留：超出部分压缩为 summary 归档
            overflow = finished[: len(finished) - max_finished]
            archived_rows = _summary_rows(overflow)
            finished = finished[len(finished) - max_finished:]
            trimmed = len(overflow)
        elif size > max_bytes:
            # 超阈值：先按上限裁（若未裁过），仍超 → 整批归档重置
            if len(finished) > max_finished:
                overflow = finished[: len(finished) - max_finished]
                archived_rows = _summary_rows(overflow)
                finished = finished[len(finished) - max_finished:]
                trimmed = len(overflow)
            # 归档后重算体积仍超 → 整批移走（历史保留在归档文件）
            new_size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            if new_size > max_bytes:
                if finished:
                    archived_rows = _summary_rows(finished)
                    trimmed += len(finished)
                finished = []

        if archived_rows:
            arch_dir.mkdir(parents=True, exist_ok=True)
            name = f"progress_{time.strftime('%Y%m%d_%H%M%S')}.json"
            dest = arch_dir / name
            payload = {
                "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": archived_rows,
                "trimmed_rows": trimmed,
            }
            tmp = dest.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, dest)

        data["finished"] = finished
        data["in_progress"] = data.get("in_progress") or {}
        data["failed"] = data.get("failed") or []
        data["gc"] = {
            "last_gc": time.strftime("%Y-%m-%d %H:%M:%S"),
            "trimmed": trimmed,
            "archived": len(archived_rows),
        }
        if trimmed or force:
            _atomic_write(p, data)
        return {
            "trimmed": trimmed,
            "archived": len(archived_rows),
            "rows_kept": len(finished),
            "size_before": size,
            "size_after": p.stat().st_size if p.exists() else 0,
            "archive_dir": str(arch_dir),
        }
    except Exception:
        return {"trimmed": 0, "archived": 0, "rows_kept": 0,
                "size_before": 0, "size_after": 0, "archive_dir": ""}


def archive_progress(path: str | Path, archive_dir: Optional[str | Path] = None) -> Path:
    """强制归档当前整批进度（engine 批次完成时可选调用）；返回归档文件路径。"""
    rotate_progress(path, archive_dir=archive_dir, force=True)
    arch_dir = Path(archive_dir) if archive_dir else _archive_path(Path(path))
    if not arch_dir.exists():
        return Path(path)
    files = sorted(arch_dir.glob("progress_*.json"))
    return files[-1] if files else Path(path)
