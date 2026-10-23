"""G70 UI 性能基准（离屏；可重复运行，不参与打包）。

用法：
    QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/bench_g70_ui.py

输出 JSON；用于验证「500 行批次不再创建 500 个 QProgressBar」与 QSS 缓存收益。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QProgressBar, QTableWidget  # noqa: E402

app = QApplication.instance() or QApplication([])

from gcm.models import RepoSpec  # noqa: E402
from gcm.ui import theme  # noqa: E402
from gcm.ui.progress_table import ProgressTable  # noqa: E402

ROWS = 500


def _spec(i: int) -> RepoSpec:
    return RepoSpec("owner", f"repo{i}", f"https://github.com/owner/repo{i}.git")


def bench_progress_table() -> dict:
    pt = ProgressTable(owner=None)
    t0 = time.perf_counter()
    pt._prepare_table(ROWS)
    pt.begin_bulk()
    for i in range(ROWS):
        pt._add_table_row(i, _spec(i))
    pt.end_bulk()
    add_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    for i in range(ROWS):
        pt._on_worker_progress(i, i % 100)
    pt._flush_progress_batch()
    update_ms = (time.perf_counter() - t0) * 1000.0

    widgets = len(pt.table.findChildren(QProgressBar))
    pt.deleteLater()
    return {"rows": ROWS, "add_ms": round(add_ms, 2),
            "update_ms": round(update_ms, 2),
            "progress_widgets": widgets}


def bench_old_cell_widgets() -> float:
    """等价对照：旧实现每行 setCellWidget(QProgressBar)。"""
    table = QTableWidget(ROWS, 1)
    t0 = time.perf_counter()
    for i in range(ROWS):
        bar = QProgressBar()
        bar.setRange(0, 0)
        table.setCellWidget(i, 0, bar)
    elapsed = (time.perf_counter() - t0) * 1000.0
    table.deleteLater()
    return round(elapsed, 2)


def bench_old_updates() -> float:
    """等价对照：旧实现 500 次 bar.setValue。"""
    table = QTableWidget(ROWS, 1)
    bars = []
    for i in range(ROWS):
        bar = QProgressBar()
        bar.setRange(0, 100)
        table.setCellWidget(i, 0, bar)
        bars.append(bar)
    t0 = time.perf_counter()
    for i, bar in enumerate(bars):
        bar.setValue(i % 100)
    elapsed = (time.perf_counter() - t0) * 1000.0
    table.deleteLater()
    return round(elapsed, 2)


def bench_qss_cache() -> dict:
    # G75：QSS 缓存随 `_build_qss` 迁到 `gcm/ui/qss_build.py`（theme.py 只剩兼容再导出），
    # 清缓存也要清真正持有它的那个模块，否则测出来的是「已命中缓存」的假快。
    from gcm.ui import qss_build as _qb
    _qb._QSS_CACHE.clear()
    t0 = time.perf_counter()
    first = theme.qss_for_theme("deep", 1.0)
    first_ms = (time.perf_counter() - t0) * 1000.0
    t0 = time.perf_counter()
    second = theme.qss_for_theme("deep", 1.0)
    second_ms = (time.perf_counter() - t0) * 1000.0
    return {"first_ms": round(first_ms, 3), "cached_ms": round(second_ms, 3),
            "identical": first == second, "chars": len(first)}


def bench_startup() -> float:
    from gcm.db.repo_db import Database
    from gcm.db.settings import SettingsStore
    from gcm.ui.main_window import MainWindow
    tmp = Path(tempfile.mkdtemp(prefix="gcm_bench_"))
    t0 = time.perf_counter()
    w = MainWindow(data_dir=tmp, db=Database(tmp / "t.db"),
                   settings=SettingsStore(tmp / "s.json"))
    elapsed = (time.perf_counter() - t0) * 1000.0
    w.close()
    return round(elapsed, 2)


if __name__ == "__main__":
    out = {
        "progress_table": bench_progress_table(),
        "old_cell_widgets_ms": bench_old_cell_widgets(),
        "old_updates_ms": bench_old_updates(),
        "qss_cache": bench_qss_cache(),
        "startup_empty_db_ms": bench_startup(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
