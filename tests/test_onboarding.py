"""G36-1 首次运行向导（OnboardingDialog）单元测试（离屏 Qt）。

覆盖：
- 初始显示步骤 1；上一步禁用
- 下一步 2 → 3；步骤 3 按钮变「完成」；「完成」触发 accept
- selected_dir 默认 = default_dir；改目录后 selected_dir 更新（mock QFileDialog）
- selected_mode 随 combo 变化
- skip() 关闭且 skipped=True
- 浏览按钮走模块内 QFileDialog（不 import main_window）
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402  # 离屏前缀在 Qt 导入前设置

from gcm.ui.onboarding import OnboardingDialog  # noqa: E402  # ROOT 需先入 sys.path

_app = QApplication.instance() or QApplication(sys.argv)


class TestOnboardingDialog(unittest.TestCase):
    """OnboardingDialog 三步向导交互。"""

    def setUp(self):
        self.default_dir = r"D:\repos\default"
        self.dlg = OnboardingDialog(parent=None, default_dir=self.default_dir)

    def tearDown(self):
        try:
            self.dlg.close()
        except Exception:
            pass

    # ------------------------------------------------------------ 初始状态
    def test_initial_step1_and_back_disabled(self):
        """初始显示步骤 1，上一步禁用，跳过按钮可见。"""
        self.assertEqual(self.dlg.stack.currentIndex(), 0)
        self.assertFalse(self.dlg.btn_back.isEnabled())
        self.assertFalse(self.dlg.btn_skip.isHidden())  # 对话框未 show 时用 isHidden 判「始终可见」
        self.assertEqual(self.dlg.windowTitle(), "欢迎使用 Git-clone-Max")

    def test_initial_selected_dir_equals_default(self):
        """未改动时 selected_dir 回退 default_dir。"""
        self.assertEqual(self.dlg.selected_dir, self.default_dir)

    def test_initial_selected_mode_full(self):
        """默认模式为满量克隆（combo 第一项）。"""
        self.assertEqual(self.dlg.mode_combo.currentIndex(), 0)
        self.assertEqual(self.dlg.selected_mode, "满量克隆")

    # ------------------------------------------------------------ 向导推进
    def test_next_advances_to_step2(self):
        """下一步 → 步骤 2，上一步恢复可用。"""
        self.dlg.btn_next.click()
        self.assertEqual(self.dlg.stack.currentIndex(), 1)
        self.assertTrue(self.dlg.btn_back.isEnabled())

    def test_next_to_step3_button_becomes_done(self):
        """步进到步骤 3 → 按钮文本变「完成」。"""
        self.dlg.btn_next.click()
        self.dlg.btn_next.click()
        self.assertEqual(self.dlg.stack.currentIndex(), 2)
        self.assertEqual(self.dlg.btn_next.text(), "完成")

    def test_done_accepts_dialog(self):
        """步骤 3 点「完成」→ 触发 accept（QDialog.Accepted）。"""
        self.dlg.btn_next.click()
        self.dlg.btn_next.click()
        self.dlg.btn_next.click()
        self.assertEqual(self.dlg.result(), QDialog.DialogCode.Accepted)

    def test_back_from_step2_to_step1(self):
        """步骤 2 上一步 → 步骤 1，上一步重新禁用。"""
        self.dlg.btn_next.click()
        self.dlg.btn_back.click()
        self.assertEqual(self.dlg.stack.currentIndex(), 0)
        self.assertFalse(self.dlg.btn_back.isEnabled())

    # ------------------------------------------------------------ selected_dir / 浏览
    def test_selected_dir_updates_after_browse(self):
        """mock 模块内 QFileDialog → selected_dir 更新为新路径。"""
        new_dir = r"D:\repos\new"
        with mock.patch("gcm.ui.onboarding.QFileDialog") as m_fd:
            m_fd.getExistingDirectory.return_value = new_dir
            self.dlg.btn_browse.click()
        self.assertEqual(self.dlg.dir_edit.text(), new_dir)
        self.assertEqual(self.dlg.selected_dir, new_dir)

    def test_browse_empty_keeps_default(self):
        """浏览取消（返回空）→ selected_dir 仍回退 default_dir。"""
        with mock.patch("gcm.ui.onboarding.QFileDialog") as m_fd:
            m_fd.getExistingDirectory.return_value = ""
            self.dlg.btn_browse.click()
        self.assertEqual(self.dlg.selected_dir, self.default_dir)

    # ------------------------------------------------------------ selected_mode
    def test_selected_mode_follows_combo(self):
        """combo 切到浅克隆 → selected_mode 同步。"""
        self.dlg.mode_combo.setCurrentIndex(1)
        self.assertEqual(self.dlg.selected_mode, "浅克隆")

    # ------------------------------------------------------------ skip
    def test_skip_closes_and_flags(self):
        """skip() → skipped=True 且对话框关闭（result != Rejected 默认）。"""
        self.dlg.skip()
        self.assertTrue(self.dlg.skipped)
        self.assertFalse(self.dlg.isVisible())

    # ------------------------------------------------------------ 纯 UI 边界
    def test_no_settings_import(self):
        """onboarding 不依赖 settings 持久化：无 first_run 写入字段。"""
        self.assertFalse(hasattr(self.dlg, "first_run_done"))
        self.assertIsNone(self.dlg.accepted_dir() if hasattr(self.dlg, "accepted_dir") else None)

    def test_no_main_window_import(self):
        """模块源码不 import main_window（字符串静态检查）。"""
        src = Path(ROOT, "gcm", "ui", "onboarding.py").read_text(encoding="utf-8")
        self.assertNotIn("import main_window", src)
        self.assertNotIn("from main_window", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
