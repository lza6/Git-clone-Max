"""G78：快捷填充滚动区不得裁切 chip（回归守卫）。

**真实缺陷**：`quick_scroll.setFixedHeight(38)` 写死，而 chip 按钮实测
`sizeHint().height() == 42` → 每颗 chip 底部 4px 被裁（圆角下沿 + 文字基线以下全没了）。
1440×900 标准模式就能看到，与缩放无关（早先 `known-issues.md` 把病因误记为
"仅小屏+125%"，本用例的存在就是为了不再靠肉眼发现它）。

守卫口径：**滚动区高度 ≥ 最高的 chip 高度**（几何可断言，不依赖截图）。
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from PyQt6.QtTest import QTest  # noqa: E402

from gcm.db.repo_db import Database  # noqa: E402
from gcm.db.settings import SettingsStore  # noqa: E402
from gcm.ui.download_layout import sync_quick_scroll_height  # noqa: E402
from gcm.ui.main_window import MainWindow  # noqa: E402


class TestQuickScrollNoClipping(unittest.TestCase):
    def _win(self, size=(1440, 900)):
        self._tmp = tempfile.TemporaryDirectory(prefix="gcm_g78_")
        d = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "s.json"))
        win.resize(*size)
        win.show()
        QTest.qWait(300)          # 等 _apply_layout_mode / 布局定型
        self.addCleanup(win.close)
        self.addCleanup(win.deleteLater)
        return win

    def _assert_not_clipped(self, win):
        chips = list(getattr(win, "_quick_fill_buttons", None) or [])
        self.assertTrue(chips, "快捷填充 chip 未构建（settings.quick_repos 为空？）")
        tallest = max(b.sizeHint().height() for b in chips)
        self.assertGreaterEqual(
            win.quick_scroll.height(), tallest,
            f"滚动区 {win.quick_scroll.height()} < chip {tallest} → chip 被裁切")

    def test_no_clipping_when_visible_and_hidden_in_compact(self):
        """标准/宽屏下不裁切；紧凑模式下**整行隐藏**（G74 的既定行为）。

        注意 1366×768 与 1280×800 是 compact（`layout.COMPACT_HEIGHT = 820`），
        那时快捷填充本就隐藏 —— 别把"按设计隐藏"误判成"被裁切"。
        """
        from gcm.ui.layout import layout_mode
        for size in ((1440, 900), (1366, 768), (1280, 800), (1920, 1080)):
            with self.subTest(size=size):
                win = self._win(size)
                compact = layout_mode(win.width(), win.height()) in ("compact", "unsupported")
                if compact:
                    self.assertTrue(win.quick_scroll.isHidden(),
                                    f"{size} 属紧凑模式，快捷填充应隐藏")
                else:
                    self.assertFalse(win.quick_scroll.isHidden(), f"{size} 非紧凑，应可见")
                    self._assert_not_clipped(win)

    def test_height_follows_chip_after_rebuild(self):
        """增删 chip 后高度必须跟着重算（否则编辑完又裁）。"""
        win = self._win((1440, 900))
        # 加一批 chip 触发重建（走生产路径 rebuild_quick_fill → sync）
        win.settings.quick_repos = [f"owner/repo-{i}" for i in range(12)]
        from gcm.ui.download_layout import rebuild_quick_fill
        n = rebuild_quick_fill(win)
        QTest.qWait(50)
        self.assertEqual(n, 12)
        self._assert_not_clipped(win)

    def test_sync_is_idempotent_and_safe_without_scroll(self):
        win = self._win((1440, 900))
        before = win.quick_scroll.height()
        sync_quick_scroll_height(win)
        sync_quick_scroll_height(win)
        self.assertEqual(win.quick_scroll.height(), before, "重复同步不应改变高度")

        class _Owner:       # 还没挂 scroll 的宿主：不得抛
            _quick_fill_buttons = []
        sync_quick_scroll_height(_Owner())   # 不抛即通过
