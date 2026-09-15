# -*- coding: utf-8 -*-
"""设置页搜索定位测试（G36-7，离屏 Qt）。

覆盖：顶部搜索框存在；关键字过滤分组（分组标题 / 行内 QLabel 标签双层匹配）；
清空搜索恢复全部可见；无匹配词全部隐藏。
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

from PyQt6.QtWidgets import QApplication, QGroupBox, QLineEdit

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


class TestSettingsSearch(unittest.TestCase):
    """G36-7：设置页顶部搜索框按关键字过滤分组可见性。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = SettingsStore(self.tmp / "settings.json")
        self.w = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                            settings=self.store)
        self.w.show()
        # settings_groups 索引：0=启动与后台运行 g1 / 1=并行与网络 g3 /
        # 2=外观 g5 / 3=关于与更新 g4
        self.g1, self.g3, self.g5, self.g4 = self.w.settings_groups

    def tearDown(self):
        try:
            if hasattr(self.w, "_log_batch_timer") and self.w._log_batch_timer.isActive():
                self.w._log_batch_timer.stop()
            if hasattr(self.w, "_log_batch"):
                self.w._log_batch.clear()
                self.w._flush_log_batch()
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    # 断言工具：isHidden() 反映显式 setVisible(False)，离屏下最可靠
    def assert_visible(self, group: QGroupBox, msg: str = ""):
        self.assertFalse(group.isHidden(), f"{group.title()} 应可见（未显式隐藏）{msg}")

    def assert_hidden(self, group: QGroupBox, msg: str = ""):
        self.assertTrue(group.isHidden(), f"{group.title()} 应被隐藏{msg}")

    # ------------------------------------------------------------ 搜索框存在
    def test_search_box_exists(self):
        """owner.settings_search 为 QLineEdit 且 textChanged 已连接生效。"""
        self.assertTrue(hasattr(self.w, "settings_search"), "应存在 settings_search")
        self.assertIsInstance(self.w.settings_search, QLineEdit)

    # ------------------------------------------------------------ 关键字过滤
    def test_search_proxy_keeps_network_group(self):
        """搜索「代理」→ g3 并行与网络可见，g1 启动 / g5 外观 / g4 关于 隐藏。"""
        self.w.settings_search.setText("代理")
        self.assert_visible(self.g3, "标题「…代理…」与行标签「HTTP 代理：」匹配")
        self.assert_hidden(self.g1)
        self.assert_hidden(self.g5)
        self.assert_hidden(self.g4)

    def test_clear_search_shows_all_groups(self):
        """先过滤再清空 → 全部分组恢复可见。"""
        self.w.settings_search.setText("代理")
        self.assert_hidden(self.g1, "前置：过滤后 g1 应隐藏")
        self.w.settings_search.setText("")
        for g in (self.g1, self.g3, self.g5, self.g4):
            self.assert_visible(g, "清空搜索应恢复全部可见")

    def test_search_theme_shows_appearance_group(self):
        """搜索「主题」→ g5 外观可见（标题 + 行标签均匹配）。"""
        self.w.settings_search.setText("主题")
        self.assert_visible(self.g5, "标题「外观（主题）」匹配")
        self.assert_hidden(self.g1)
        self.assert_hidden(self.g3)
        self.assert_hidden(self.g4)

    def test_search_label_text_matches(self):
        """搜索「fetch」→ g3 因行标签「fetch 超时（秒）：」匹配而可见（标题不含）。"""
        self.w.settings_search.setText("fetch")
        self.assert_visible(self.g3, "行标签「fetch 超时（秒）：」匹配")
        self.assert_hidden(self.g1)
        self.assert_hidden(self.g5)

    def test_search_no_match_hides_all_groups(self):
        """搜索无匹配词 → 全部分组隐藏。"""
        self.w.settings_search.setText("不存在的关键字xyz")
        for g in (self.g1, self.g3, self.g5, self.g4):
            self.assert_hidden(g)


if __name__ == "__main__":
    unittest.main(verbosity=2)
