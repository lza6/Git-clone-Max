#!/usr/bin/env python
"""无障碍审计（Rev14 R14-04）：统计主操作与输入控件「读屏读不出的名字」数量。

**为什么口径是 `effective_name` 而不是 `accessibleName`**：Qt 对
`QPushButton` / `QToolButton` / `QCheckBox` / `QRadioButton` 的 accessible name
默认取 `text()`。只数 `accessibleName` 会把"按钮上有字"误报成缺失，基线虚高
之后真正的缺口（没有可见文本的输入框）就被噪音淹没了。

用法：

    python scripts/a11y_audit.py             # 打 JSON；计数比基线差则退出码 1
    python scripts/a11y_audit.py --update    # 把当前计数写成新基线（只能降不能升）

基线存在 `scripts/a11y_baseline.json`：CI 里由 `tests/test_rev14_a11y.py` 断言
「不比基线差」，所以这里不是唯一防线 —— 脚本是给人看细节的，测试是给流水线看的。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASELINE = ROOT / "scripts" / "a11y_baseline.json"

#: 主操作清单：这些是"读屏读不出就不敢按"的入口，覆盖率必须 100%。
MAIN_ACTIONS = (
    "btn_save_list", "combo_load_list", "btn_load_list", "btn_star_import",
    "btn_start", "btn_cancel", "btn_clear", "btn_open", "btn_failures",
    "btn_retry_queue", "btn_diag_bundle", "btn_edit_quick", "btn_pause_batch",
    "btn_more_tools",
)


def audit() -> dict:
    """离屏构造主窗，返回各类控件的「有名字 / 总数」与主操作缺失清单。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QLineEdit,
        QPlainTextEdit,
        QPushButton,
        QSpinBox,
        QToolButton,
    )

    app = QApplication.instance() or QApplication([])
    from gcm.db.settings import SettingsStore
    from gcm.ui.a11y import effective_name
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
    try:
        app.processEvents()
        by_class: dict[str, dict[str, int]] = {}
        for cls in (QPushButton, QToolButton, QLineEdit, QCheckBox, QComboBox,
                    QSpinBox, QPlainTextEdit):
            items = win.findChildren(cls)
            named = sum(1 for w in items if effective_name(w))
            by_class[cls.__name__] = {"total": len(items),
                                      "missing": len(items) - named}
        missing_main = [n for n in MAIN_ACTIONS
                        if getattr(win, n, None) is not None
                        and not effective_name(getattr(win, n))]
        return {
            "main_actions_total": sum(
                1 for n in MAIN_ACTIONS if getattr(win, n, None) is not None),
            "main_actions_missing": missing_main,
            "by_class": by_class,
            "missing_total": sum(v["missing"] for v in by_class.values()),
        }
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()


def load_baseline() -> dict:
    if not BASELINE.exists():
        return {}
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    now = audit()
    if "--update" in argv:
        BASELINE.write_text(json.dumps(now, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"基线已更新：{BASELINE.relative_to(ROOT)}")
        return 0
    base = load_baseline()
    print(json.dumps(now, ensure_ascii=False, indent=2))
    worse: list[str] = []
    if base:
        if now["missing_total"] > base.get("missing_total", 0):
            worse.append(f"缺名控件 {base.get('missing_total')} → {now['missing_total']}")
        if len(now["main_actions_missing"]) > len(base.get("main_actions_missing") or []):
            worse.append("主操作缺名变多")
        for cls, stat in (base.get("by_class") or {}).items():
            cur = (now["by_class"].get(cls) or {}).get("missing", 0)
            if cur > stat.get("missing", 0):
                worse.append(f"{cls} 缺名 {stat.get('missing')} → {cur}")
    if worse:
        print("无障碍退化：\n  - " + "\n  - ".join(worse))
        return 1
    if not now["main_actions_missing"]:
        print("主操作 100% 有名字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
