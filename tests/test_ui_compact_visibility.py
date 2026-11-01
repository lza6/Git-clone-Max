"""X-4 守卫：主操作区的每个控件在任何支持尺寸下都必须**完整可见**。

**为什么需要这条测试**（真实事故，不是假想）：
`scripts/ui_screenshot_audit.py` 会为 5 种分辨率 × 4 主题出 34~38 张截图，
但它**只存图、不断言**。于是「保存清单 / 载入 / 导入 Star」这一行在
1366×768 笔记本（窗口 clamp 成 1240×729）下被顶部滚动区裁到只剩 **10px**
（`visibleRegion` 86×10 / 86×42），截图里就是 4 个**空框** —— 连续 4 轮 UI 迭代
都没人发现，因为它不影响任何断言，也不影响任何单测。

**判定口径**：`widget.visibleRegion().boundingRect().size() == widget.rect().size()`。
比 `isVisible()` 强得多 —— 后者对「被父级裁掉一半」的控件仍返回 True。

覆盖尺寸（全部来自 `ui/layout.py` 的真实来源，不是随便拍的）：
- 1920×1080 / 1440×900 / 1366×768 / 1280×800 —— 常见桌面
- **1240×729** —— `clamp_window_size(1240,840, 1366,768)` 的实际结果（1366×768 笔记本）
- **1216×684** —— `clamp_window_size(1240,840, 1280,720)` 的实际结果（1080p@150%）
- **1093×614** —— 1366×768@125% 的可用工作区（`layout.py` 注释里声明的目标）
- 1024×768 —— 老式 4:3
- **1020×560** —— `WINDOW_MIN`，声明的最小窗口（极矮，最容易压扁）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 主操作区必须常驻的控件（「点了才知道有没有」的那些）。
#: 新增主操作按钮请加进这里 —— 这条清单本身就是契约。
_MAIN_ACTION_WIDGETS = (
    # 任务清单 / Star 导入（X-4 事故现场）
    "btn_save_list", "combo_load_list", "btn_load_list", "btn_star_import",
    # 主操作
    "btn_start", "btn_cancel", "btn_clear",
    # 工具行
    "btn_open", "btn_failures", "btn_retry_queue", "btn_diag_bundle",
)

#: 见模块 docstring：每个尺寸都有来源，不是随手写的。
_SIZES = (
    (1920, 1080), (1440, 900), (1366, 768), (1280, 800),
    (1240, 729), (1216, 684), (1093, 614), (1024, 768), (1020, 560),
)


class TestMainActionAreaFullyVisible(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        self._tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(self._tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        self.win = MainWindow(data_dir=self._tmp)
        self.win.show()

    def tearDown(self):
        try:
            self.win.close()
        finally:
            self.win.deleteLater()

    def _pump(self, ms: int = 700) -> None:
        from PyQt6.QtTest import QTest
        QTest.qWait(ms)          # resize 有 150ms 去抖 + 布局重排

    def _clipped(self) -> list[str]:
        bad = []
        for name in _MAIN_ACTION_WIDGETS:
            w = getattr(self.win, name, None)
            if w is None:
                bad.append(f"{name}(缺失)")
                continue
            if not w.isVisible():
                bad.append(f"{name}(不可见)")
                continue
            vr = w.visibleRegion().boundingRect()
            if vr.size() != w.rect().size():
                bad.append(f"{name}({vr.width()}x{vr.height()} of {w.width()}x{w.height()})")
        return bad

    def test_no_clipping_at_any_supported_size(self):
        """9 个尺寸逐个断言 —— 这就是 X-4 的回归网。"""
        for size in _SIZES:
            with self.subTest(size=size):
                self.win.resize(*size)
                self._pump()
                bad = self._clipped()
                self.assertEqual(
                    bad, [],
                    f"{size} 下主操作区控件被裁切/缺失：{bad}\n"
                    "主操作区必须常驻可见（顶部可滚动区可以让位，这些不行）")

    def test_the_two_real_laptop_sizes_are_exactly_what_clamp_produces(self):
        """反向自检：被判定的两个「笔记本尺寸」必须是真的由 clamp 算出来的。

        防止有人日后把 1240×729 改成随便一个宽松尺寸，让这条守卫悄悄失去意义。
        """
        from gcm.ui.layout import clamp_window_size
        self.assertEqual(clamp_window_size(1240, 840, 1366, 768), (1240, 729))
        self.assertEqual(clamp_window_size(1240, 840, 1280, 720), (1216, 684))

    def test_guard_would_have_caught_x4(self):
        """反向自检：把清单行塞回顶部滚动区，这条守卫必须红。

        做法不改产品代码 —— 直接把其中一个控件 `setParent` 到滚动区内容里
        （等价于「它又变回滚动区里的最后一行」），矮窗口下它就必然被裁。
        """
        self.win.resize(1240, 729)
        self._pump()
        self.assertEqual(self._clipped(), [], "前提：修复后此处应无裁切")

        target = self.win.btn_star_import
        inner = self.win.download_top_scroll.widget()
        # 追加到滚动区内容底部（模拟旧的「最后一行」布局）
        target.setParent(inner)
        inner.layout().addWidget(target)
        self._pump()
        bad = self._clipped()
        self.assertTrue(
            any("btn_star_import" in b for b in bad),
            f"把控件塞回滚动区后守卫没报出来 —— 这条守卫是假的：{bad}")


if __name__ == "__main__":
    unittest.main()
