#!/usr/bin/env python
"""启动分段基准（Rev14 R14-16）：把「冷启动」拆成可比较的四段。

**为什么需要固定口径**：历史上这个数字被引用成 3 秒、也被引用成 4 秒，因为
两条路径混在一起算 —— 离屏 Python 路径（解释器已起）与打包 exe（含
PyInstaller 解包）不是同一件事。这里只测**离屏 Python 路径**，并把 exe 的口径
明确标为 `exe_ms`（从 `evidence/health_*.json` 读历史值，不在这里猜）。

用法：

    python scripts/bench_startup.py            # 打 JSON 到 stdout
    python scripts/bench_startup.py --json out.json

四段的口径：
    qt_import_ms   import PyQt6.QtWidgets
    ui_import_ms   import gcm.ui.main_window（含全部 UI 模块）
    ctor_ms        MainWindow(data_dir=临时目录) 构造
    first_frame_ms show() + processEvents 一次
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 门禁（离屏口径）。超了才允许立"启动优化"任务 —— 先测准再改。
BUDGET = {"ctor_ms": 120, "first_frame_ms": 50}


def _read_exe_ms() -> int | None:
    """读最近一次打包健康检查的 **smoke_ms**（没有就 None，不猜）。

    `elapsed_ms` 是整个健康检查的耗时（含 42MB exe + 82MB zip 的 SHA-256 与
    版本提取），把它当启动耗时是历史误判。旧台账没有 `smoke_ms` 时返回 None，
    不再用 `elapsed_ms` 冒充 —— 宁可缺一个数字，也不混两个口径。
    """
    best: tuple[str, int] | None = None
    for p in sorted((ROOT / "evidence").glob("health_*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ms = data.get("smoke_ms")
        if isinstance(ms, int) and ms > 0 and (best is None or p.name > best[0]):
            best = (p.name, ms)
    return best[1] if best else None


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    marks: dict[str, float] = {}

    marks["start"] = time.perf_counter()
    from PyQt6.QtWidgets import QApplication
    marks["qt_import"] = time.perf_counter()
    app = QApplication.instance() or QApplication([])
    marks["app"] = time.perf_counter()
    from gcm.db.settings import SettingsStore
    from gcm.ui.main_window import MainWindow
    marks["ui_import"] = time.perf_counter()

    tmp = Path(tempfile.mkdtemp())
    store = SettingsStore(tmp / "settings.json")
    s = store.load()
    s.first_run_done = True
    s.checklist_prompted = True
    s.animations = False
    s.minimize_to_tray = False
    store.save(s)
    win = MainWindow(data_dir=tmp)
    marks["ctor"] = time.perf_counter()
    win.show()
    app.processEvents()
    marks["first_frame"] = time.perf_counter()

    result = {
        "qt_import_ms": round((marks["qt_import"] - marks["start"]) * 1000, 1),
        "ui_import_ms": round((marks["ui_import"] - marks["app"]) * 1000, 1),
        "ctor_ms": round((marks["ctor"] - marks["ui_import"]) * 1000, 1),
        "first_frame_ms": round((marks["first_frame"] - marks["ctor"]) * 1000, 1),
        "total_ms": round((marks["first_frame"] - marks["start"]) * 1000, 1),
        "exe_ms": _read_exe_ms(),
        "budget": BUDGET,
    }
    result["over_budget"] = [k for k, v in BUDGET.items() if result[k] > v]
    win.close()
    win.deleteLater()
    app.processEvents()

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
