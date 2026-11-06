#!/usr/bin/env python
"""改尺寸重排基准（Rev14 R14-16）：8 尺寸 × 3 字号，量「用户能感知」的那一段。

**为什么量 resize→processEvents 而不量 `_on_layout_tick()`**：后者只占 0.03ms
（实测），真正的成本在 Qt 布局内部 —— 只量 tick 会得出"重排几乎免费"的错误结论
（Rev13 `R13-13` 就是按这个错误归因立的任务，实测收益 ≈0）。

同时记录 `moveEvent` 次数：它是「布局抖动」在桌面端的等价物（Web 的 CLS）。
首帧之后的重排不该让可见元素来回移动 —— 次数不为 0 说明有控件在和自己拉扯。

用法：

    python scripts/bench_resize.py
    python scripts/bench_resize.py --json out.json

门禁：`FRAME_BUDGET_MS`（60fps 一帧）。超出只会列进 `over_budget`，
不判失败 —— 首次重排本来就比稳态贵，这个数字是给人看的，不是给流水线看的。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FRAME_BUDGET_MS = 16.7
#: 位移计数（`moveEvent` 次数）—— 桌面端的「布局抖动」等价物
_MOVE_COUNTER = [0]
#: 首帧后 **50ms 以上**才发生的位移 —— 用户真正看得见的那一类（首帧边界内的不算）
_LATE_MOVES = [0]
_T0 = [0.0]
SIZES = (
    (1020, 584), (1020, 560), (1100, 700), (1240, 729),
    (1366, 768), (1440, 900), (1600, 1000), (1920, 1080),
)
SCALES = (1.0, 1.3, 1.6)


class _MoveWatcher(QObject):
    """统计**整棵控件树**的 `moveEvent` 次数（Rev14 V3，CLS 的桌面等价物）。

    只挂窗口自己是不够的：窗口 `resize` 时自身不移动（不产生 Move），
    真正会抖的是里面的控件。所以对 `win` + 全部子控件都装过滤器，
    并在**首帧后静置 500ms** 窗口内计数 —— 用户没操作时的自主位移才是「抖动」，
    用户拖窗口造成的位移是正常的。
    """

    def __init__(self, roots) -> None:
        super().__init__()
        self._roots = [w for w in roots if w is not None]
        for w in self._roots:
            w.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:      # noqa: N802 —— Qt 命名
        if event.type() == QEvent.Type.Move:
            _MOVE_COUNTER[0] += 1
            if (time.perf_counter() - _T0[0]) * 1000 > 50:
                _LATE_MOVES[0] += 1
        return False

    def detach(self) -> None:
        for w in self._roots:
            try:
                w.removeEventFilter(self)
            except RuntimeError:      # 控件已销毁
                pass


def _pump(app, times: int = 6) -> None:
    for _ in range(times):
        app.processEvents()


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gcm.db.settings import SettingsStore
    from gcm.ui.main_window import MainWindow

    tmp = Path(tempfile.mkdtemp())
    store = SettingsStore(tmp / "settings.json")
    s = store.load()
    s.first_run_done = True
    s.checklist_prompted = True
    s.animations = False
    s.minimize_to_tray = False
    store.save(s)
    win = MainWindow(data_dir=tmp)
    win.show()
    _pump(app)
    # Rev14 V3：首帧后静置 500ms 计「自主位移」（CLS 等价）—— 这期间没有任何用户操作
    from PyQt6.QtWidgets import QWidget
    _MOVE_COUNTER[0] = 0
    _LATE_MOVES[0] = 0
    _T0[0] = time.perf_counter()
    _watcher = _MoveWatcher([win, *win.findChildren(QWidget)])
    from PyQt6.QtTest import QTest
    QTest.qWait(500)
    first_frame_moves = _MOVE_COUNTER[0]
    late_moves = _LATE_MOVES[0]
    _watcher.detach()

    rows: list[dict] = []
    over: list[str] = []
    for scale in SCALES:
        win.settings.font_scale = scale
        win._apply_font_scale()
        _pump(app, 3)
        for w, h in SIZES:
            win.resize(w, h)
            # Rev14 V3：记录 `moveEvent` 次数 —— 桌面端的「布局抖动」等价物
            # （Web 的 CLS）。首帧之后的重排不该让可见元素来回移动。
            start = time.perf_counter()
            _pump(app)
            elapsed = (time.perf_counter() - start) * 1000
            from gcm.ui.layout import breakpoint_name
            bp = breakpoint_name(win.width(), win.height())
            rows.append({"w": w, "h": h, "scale": scale,
                         "actual": [win.width(), win.height()],
                         "reflow_ms": round(elapsed, 1), "breakpoint": bp})
            if elapsed > FRAME_BUDGET_MS:
                over.append(f"{w}x{h}@{scale}: {elapsed:.1f}ms")
    win.close()
    win.deleteLater()
    _pump(app)

    result = {"frame_budget_ms": FRAME_BUDGET_MS,
              # CLS 等价：首帧后 500ms 内、无用户操作时的控件位移次数（目标 0）
              "first_frame_moves": first_frame_moves,
              # 用户可见口径：首帧 50ms 之后的位移（目标 0）
              "late_moves": late_moves,
              "rows": rows,
              "over_budget": over}
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
