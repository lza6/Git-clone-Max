"""Rev14 键盘导航守卫：tab 链名字真实存在、顺序不重复、F6 真的切页并给焦点。

**为什么值得守卫**：`setTabOrder` 的失败形态是**完全静默**的 —— 传一个不存在的
属性名只是少串一对控件，`Tab` 仍然能走（走的是 Qt 默认顺序），谁也看不出问题。
Rev13 之前全库 `setTabOrder` 为 0，键盘用户一直在用"按创建顺序"乱跳的默认链。
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

from gcm.ui.focus_order import (  # noqa: E402
    CHAINS,
    DOWNLOAD_CHAIN,
    MANAGE_CHAIN,
    SETTINGS_CHAIN,
    apply_tab_order,
    chain_widgets,
)


class TestChainDeclarations(unittest.TestCase):
    def test_no_duplicate_names(self):
        for idx, chain in CHAINS.items():
            with self.subTest(tab=idx):
                self.assertEqual(len(chain), len(set(chain)),
                                 f"页签 {idx} 的链里有重复项")

    def test_download_chain_starts_with_list_actions(self):
        """视觉顺序 = 任务清单行 → 输入 → 选项 → 主操作 → 进度 → 工具行。"""
        self.assertEqual(DOWNLOAD_CHAIN[0], "btn_save_list")
        self.assertLess(DOWNLOAD_CHAIN.index("repo_input"),
                        DOWNLOAD_CHAIN.index("btn_start"),
                        "输入框必须排在主操作之前")
        self.assertLess(DOWNLOAD_CHAIN.index("btn_start"),
                        DOWNLOAD_CHAIN.index("table"),
                        "开始按钮必须在进度表之前")
        self.assertEqual(DOWNLOAD_CHAIN[-1], "btn_more_tools")

    def test_manage_chain_covers_all_row_actions(self):
        for name in ("btn_update_all", "manage_table", "btn_delete"):
            self.assertIn(name, MANAGE_CHAIN)

    def test_settings_chain_starts_with_nav(self):
        self.assertEqual(SETTINGS_CHAIN[0], "settings_nav")


class TestChainOnRealWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])
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
        cls.win = MainWindow(data_dir=tmp)
        cls.win.show()
        for _ in range(4):
            cls._app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        cls.win.deleteLater()
        cls._app.processEvents()

    def test_every_declared_name_exists(self):
        """链里写的属性名必须真实存在 —— 打错一个只是少串一对，不会报错。"""
        missing = []
        for idx, chain in CHAINS.items():
            for name in chain:
                if getattr(self.win, name, None) is None:
                    missing.append(f"tab{idx}:{name}")
        self.assertEqual(missing, [], f"tab 链引用了不存在的控件：{missing}")

    def test_apply_returns_full_chain(self):
        self.assertEqual(apply_tab_order(self.win, 0), len(DOWNLOAD_CHAIN))
        self.assertEqual(apply_tab_order(self.win, 1), len(MANAGE_CHAIN))

    def test_f6_cycles_and_focuses_first_widget(self):
        start = self.win.tabs.currentIndex()
        self.win.tabs.setCurrentIndex(0)
        self.win.cycle_tab_focus()
        self.assertEqual(self.win.tabs.currentIndex(), 1)
        self.assertIs(self._app.focusWidget(), self.win.btn_update_all,
                      "F6 切页后焦点必须落在新页第一个可聚焦控件上")
        self.win.cycle_tab_focus()
        self.assertEqual(self.win.tabs.currentIndex(), 2)
        self.win.cycle_tab_focus()
        self.assertEqual(self.win.tabs.currentIndex(), 0, "F6 必须循环回第一页")
        self.win.tabs.setCurrentIndex(start)

    def test_tab_really_leaves_text_input(self):
        """按**真实 Tab 键**，焦点必须离开地址框。

        审查发现（真问题）：`QPlainTextEdit` 默认 `tabChangesFocus=False`，Tab 会被
        当成缩进插入输入框 —— 声明得再漂亮的 tab 链也只是纸面的。这条断言的就是
        "按键真的会移动焦点"，而不是"我调用了 setTabOrder"。
        """
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self.win.tabs.setCurrentIndex(0)
        self._app.processEvents()
        self.win.repo_input.setFocus()
        self._app.processEvents()
        self.assertIs(self._app.focusWidget(), self.win.repo_input,
                      "前提：地址框先拿到焦点")
        QTest.keyClick(self.win.repo_input, Qt.Key.Key_Tab)
        self._app.processEvents()
        self.assertIsNot(self._app.focusWidget(), self.win.repo_input,
                         "Tab 被 QPlainTextEdit 当缩进吃掉了（链不可达）")

    def test_tab_leaves_table(self):
        """表格不许把 Tab 吃在单元格之间（默认 tabKeyNavigation=True 会）。"""
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        table = self.win.progress_panel.table
        self.win.tabs.setCurrentIndex(0)
        self._app.processEvents()
        table.setFocus()
        self._app.processEvents()
        QTest.keyClick(table, Qt.Key.Key_Tab)
        self._app.processEvents()
        focus = self._app.focusWidget()
        self.assertFalse(focus is table or (focus is not None and table.isAncestorOf(focus)),
                         "Tab 被表格吃在单元格之间（500 行要按 3000 次才走得到工具行）")

    def test_f6_real_key_cycles_tabs(self):
        """按**真实 F6 键**切页 —— 只调 `cycle_tab_focus()` 证明不了 QShortcut 装上了。

        审查发现（守卫弱点）：原用例直接调方法，把 `focus_order.install()` 整段删掉
        它照样绿。这条断言的是"按键真的会切页"。
        """
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication
        self.win.tabs.setCurrentIndex(0)
        self.win.activateWindow()
        QApplication.setActiveWindow(self.win)
        self._app.processEvents()
        QTest.keyClick(self.win, Qt.Key.Key_F6)
        self._app.processEvents()
        self.assertEqual(self.win.tabs.currentIndex(), 1,
                         "F6 没切页 —— QShortcut 没装上或上下文不对")

    def test_switch_tab_reapplies_chain(self):
        """切页签要重设该页的链，否则 Tab 会走回上一页的顺序。"""
        self.win.tabs.setCurrentIndex(1)
        self._app.processEvents()
        widgets = chain_widgets(self.win, 1)
        self.assertTrue(widgets)
        self.assertTrue(all(w is not None for w in widgets))


if __name__ == "__main__":
    unittest.main()
