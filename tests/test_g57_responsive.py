"""G57 响应式与无障碍测试：窗口语义档 / DPI 字号联动。"""
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from gcm.ui.theme import clamp_scale_for_dpi


class TestDpiScale(unittest.TestCase):
    def test_low_dpi_keeps_scale(self):
        self.assertEqual(clamp_scale_for_dpi(0.8, 100.0), 0.8)

    def test_high_dpi_floor_to_1(self):
        self.assertEqual(clamp_scale_for_dpi(0.8, 150.0), 1.0)
        self.assertEqual(clamp_scale_for_dpi(0.9, 200.0), 1.0)

    def test_high_dpi_keeps_larger(self):
        self.assertEqual(clamp_scale_for_dpi(1.2, 200.0), 1.2)

    def test_unknown_dpi_keeps(self):
        self.assertEqual(clamp_scale_for_dpi(0.8, 0.0), 0.8)

    def test_bad_input(self):
        self.assertEqual(clamp_scale_for_dpi("x", 100.0), 1.0)


class TestWindowLayoutBand(unittest.TestCase):
    def test_layout_applies_without_window(self):
        """_apply_layout_for_width 对无 manage_table 的替身不抛异常（容错守卫）。

        该替身**没有** manage_table 属性，真实契约是 `hasattr` 守卫生效后静默返回，
        而不是抛 AttributeError（真实场景：窗口尚未构建完表格时 resizeEvent 触发）。
        """
        import gcm.ui.main_window as mw
        owner = type("O", (), {})()
        self.assertFalse(hasattr(owner, "manage_table"))
        # 不得抛异常，且不得给替身凭空补上 manage_table
        self.assertIsNone(mw.MainWindow._apply_layout_for_width(owner, 600))
        self.assertIsNone(mw.MainWindow._apply_layout_for_width(owner, 1200))
        self.assertFalse(hasattr(owner, "manage_table"))

    def test_narrow_hides_secondary_columns(self):
        """窄窗（< `layout.NARROW_WIDTH`）隐藏次要列；宽窗全部恢复。

        **Rev13 R13-03 契约变更（有理由，不是为了让测试变绿）**：
        原断言要求隐藏「标签」列，但真实模型 8 列里**没有**「标签」这一列
        （仓库/作者·仓库/本地路径/上次同步/HEAD/历史/健康/大小），
        原实现按表头文本匹配 —— 它匹配的列根本不存在，而且用错了 API
        （`QTableView.horizontalHeaderItem` 不存在），所以这项功能从来没生效过。
        契约收敛为：只隐藏「大小 / 健康」，阈值改用可达的 `NARROW_WIDTH`。
        新增的真实模型回归在 `TestNarrowFoldOnRealModel`。
        """
        import gcm.ui.main_window as mw
        from gcm.ui.layout import NARROW_WIDTH
        from PyQt6.QtWidgets import QTableWidget
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["地址", "大小", "健康", "备注"])
        owner = type("O", (), {})()
        owner.manage_table = table

        mw.MainWindow._apply_layout_for_width(owner, NARROW_WIDTH - 1)
        self.assertTrue(table.isColumnHidden(1), "窄窗应隐藏『大小』")
        self.assertTrue(table.isColumnHidden(2), "窄窗应隐藏『健康』")
        self.assertFalse(table.isColumnHidden(0), "主列『地址』不得隐藏")
        self.assertFalse(table.isColumnHidden(3),
                         "非次要列（备注）不得隐藏 —— 防止又回到按文本乱猜")

        mw.MainWindow._apply_layout_for_width(owner, NARROW_WIDTH + 200)
        for col in range(4):
            self.assertFalse(table.isColumnHidden(col),
                             f"宽窗应恢复显示第 {col} 列")


class TestNarrowFoldOnRealModel(unittest.TestCase):
    """Rev13 R13-03 反向自检：真实模型上折叠**真的**发生（旧实现是死分支）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        import tempfile
        from pathlib import Path
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        self._tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(self._tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        store.save(s)
        self.win = MainWindow(data_dir=self._tmp)

    def tearDown(self):
        try:
            self.win.close()
            self.win.deleteLater()
        except Exception:
            pass

    def test_real_model_hides_size_and_health_when_narrow(self):
        from PyQt6.QtCore import Qt
        from gcm.ui.layout import NARROW_WIDTH
        table = self.win.manage_table
        model = table.model()
        names = [model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())]
        self.assertIn("大小", names)
        self.assertIn("健康", names)
        self.assertNotIn("标签", names,
                         "守卫前提：真实模型没有『标签』列（原实现匹配的就是它）")

        self.win._apply_layout_for_width(NARROW_WIDTH - 1)
        for i, n in enumerate(names):
            if n in ("大小", "健康"):
                self.assertTrue(table.isColumnHidden(i), f"窄窗应隐藏 {n}（第 {i} 列）")
            else:
                self.assertFalse(table.isColumnHidden(i), f"{n}（第 {i} 列）不得隐藏")

        self.win._apply_layout_for_width(NARROW_WIDTH + 200)
        for i in range(model.columnCount()):
            self.assertFalse(table.isColumnHidden(i), f"宽窗应恢复第 {i} 列")


if __name__ == "__main__":
    unittest.main()
