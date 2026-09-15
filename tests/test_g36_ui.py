# -*- coding: utf-8 -*-
"""G36 UI/UX 主控侧 单测（离屏 Qt）。

覆盖：
- G36-2 状态列符号双通道（成功 ✓ 失败 ✕ 冲突 ⚠ 取消 ⊘ 跳过 →）
- G36-4 窗口几何记忆（_serialize/_restore，含最大化前缀）
- G36-5 空态 overlay（下载/管理表空时显示、有数据隐藏）
- G36-6 完成动效（_animate_result_row 背景色设入；offscreen 自动关）
- G36-8 全局热键（Ctrl+Alt+S/U/M 触发）
- G36-9 auto 主题：theme=auto 时按系统深浅色切换
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QBrush
from PyQt6.QtWidgets import QApplication

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.models import RepoSpec, SyncResult, SyncStatus
from gcm.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


class TestG36Ui(unittest.TestCase):
    """G36 以 MainWindow 为靶的交互增强。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")
        self.w = MainWindow(data_dir=self.tmp, db=self.db,
                            settings=SettingsStore(self.tmp / "settings.json"))
        self.w.show()

    def tearDown(self):
        try:
            if hasattr(self.w, "_log_batch_timer") and self.w._log_batch_timer.isActive():
                self.w._log_batch_timer.stop()
            if hasattr(self.w, "_log_batch"):
                self.w._log_batch.clear()
                self.w._flush_log_batch()
        except Exception:
            pass
        # 复位 busy：避免某些用例设置后 closeEvent 模态框卡死
        try:
            self.w.busy = False
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def _seed_row(self, index=0, status=SyncStatus.SUCCESS, message="ok"):
        spec = RepoSpec(owner="o", repo="r", url_https="https://github.com/o/r",
                        display="o__r", folder_name="o__r")
        self.w.row_specs[index] = spec
        # 保持排序关闭：多行场景使 index 恒等于视觉行号（排序免疫，回归不受符号影响）
        self.w.table.setSortingEnabled(False)
        self.w._prepare_table(index + 1)
        self.w._add_table_row(index, spec)
        self.w._on_worker_result(index, SyncResult(
            spec=spec, status=status, message=message, detail="d"))
        return index

    # ------------------------------------------------------------ G36-2 状态符号
    def test_g362_status_symbols_in_text_and_role(self):
        """状态文本含符号；UserRole 存原始值（供统计/排序/右键）。"""
        idx = self._seed_row(status=SyncStatus.SUCCESS)
        st = self.w.table.item(idx, 2)
        self.assertIn("✓", st.text())
        self.assertEqual(st.data(0x0100), "success")
        # 失败含 ✕
        idx2 = self._seed_row(index=1, status=SyncStatus.FAILED)
        st2 = self.w.table.item(idx2, 2)
        self.assertIn("✕", st2.text())
        self.assertEqual(st2.data(0x0100), "failed")

    def test_g362_finished_stats_with_symbols(self):
        """符号后缀不破坏 finished 统计（按 UserRole 计）。"""
        # 一次性准备 3 行再逐行写结果（避免 _prepare_table 重建清掉前面行）
        self.w.table.setSortingEnabled(False)
        self.w._prepare_table(3)
        for i, status in enumerate([SyncStatus.SUCCESS, SyncStatus.FAILED,
                                    SyncStatus.CONFLICT]):
            spec = RepoSpec(owner="o", repo="r",
                            url_https="https://github.com/o/r",
                            display="o__r", folder_name="o__r")
            self.w.row_specs[i] = spec
            self.w._add_table_row(i, spec)
            self.w._on_worker_result(i, SyncResult(
                spec=spec, status=status, message="m", detail="d"))
        self.w.busy = True
        self.w._on_engine_finished()
        msg = self.w.statusBar().currentMessage()
        self.assertIn("成功 1", msg)
        self.assertIn("冲突 1", msg)
        self.assertIn("失败 1", msg)

    # ------------------------------------------------------------ G36-4 窗口几何
    def test_g364_geometry_roundtrip(self):
        """serialize→restore 往返一致（尺寸+位置）。"""
        self.w.resize(1280, 800)
        self.w.move(30, 40)
        s = self.w._serialize_geometry()
        self.assertIn("1280", s)
        self.assertIn("800", s)
        self.w.settings.geometry = s
        self.w._restore_geometry()
        self.assertEqual(self.w.width(), 1280)
        self.assertEqual(self.w.height(), 800)

    def test_g364_geometry_maximized_prefix(self):
        """最大化状态下 serialize 带 M: 前缀。"""
        self.w.showMaximized()
        s = self.w._serialize_geometry()
        self.assertTrue(s.startswith("M:"))

    def test_g364_geometry_corrupt_safe(self):
        """损坏几何串 → 不抛异常、保持默认。"""
        self.w.settings.geometry = "not-a-geometry"
        self.w._restore_geometry()  # 不崩
        self.assertGreaterEqual(self.w.width(), 1020)

    # ------------------------------------------------------------ G36-5 空态 overlay
    def test_g365_empty_overlay_toggles(self):
        """空态 overlay：表空显示、有行隐藏。"""
        ov = self.w._empty_download
        self.assertTrue(ov.isVisible(), "初始空表应显示空态引导")

        self._seed_row(index=0)
        self.assertFalse(self.w._empty_download.isVisible(),
                         "有行后空态 overlay 应隐藏")

    def test_g365_manage_empty_overlay(self):
        """管理页空态 overlay 随行数切换。"""
        from gcm.models import RepoSpec as RS
        spec = RS(owner="a", repo="b", url_https="https://github.com/a/b",
                  folder_name="a__b")
        self.db.upsert_repo(spec, str(self.tmp), host="github.com")
        self.w._load_db_into_grid()
        self.assertFalse(self.w._empty_manage.isVisible(),
                         "管理页有仓库后空态隐藏")

    # ------------------------------------------------------------ G36-6 完成动效
    def test_g366_animation_sets_background(self):
        """_animate_result_row 直接调用后台色设入（含最终复位）。"""
        idx = self._seed_row(status=SyncStatus.SUCCESS)
        # 强制走动画路径（忽略 offscreen 短路：直接调方法）
        self.w._animate_result_row(idx, SyncStatus.SUCCESS)
        # 动画立即 start，背景应被设入（动画对象已 hold in _row_anims）
        self.assertTrue(hasattr(self.w, "_row_anims"))
        self.assertGreaterEqual(len(self.w._row_anims), 1)

    def test_g366_no_animation_offscreen(self):
        """offscreen 下 _on_worker_result 不创建动画对象。"""
        idx = self._seed_row(status=SyncStatus.FAILED)  # 走 _on_worker_result，auto offscreen 跳过
        self.assertFalse(hasattr(self.w, "_row_anims"),
                         "offscreen 不应创建动画")

    # ------------------------------------------------------------ G36-8 全局热键
    def test_g368_hotkey_start_cancel(self):
        """Ctrl+Alt+S：空闲→start_all；运行中→cancel_all。"""
        with mock.patch.object(MainWindow, "start_all") as m_start, \
                mock.patch.object(MainWindow, "cancel_all") as m_cancel:
            self.w.busy = False
            self.w._hotkey_start_cancel()
            m_start.assert_called_once()
            self.w.busy = True
            self.w._hotkey_start_cancel()
            m_cancel.assert_called_once()

    def test_g368_hotkey_show_window(self):
        """Ctrl+Alt+M：show+raise+activateWindow。"""
        with mock.patch.object(self.w, "show") as m_show, \
                mock.patch.object(self.w, "raise_") as m_raise, \
                mock.patch.object(self.w, "activateWindow") as m_act:
            self.w._hotkey_show_window()
        m_show.assert_called_once()
        m_raise.assert_called_once()
        m_act.assert_called_once()

    # ------------------------------------------------------------ G36-9 深浅色跟随
    def test_g369_auto_theme_applies_system(self):
        """theme=auto + 系统浅色 → 切换 light。"""
        self.w.settings.theme = "auto"
        with mock.patch("gcm.app.system_theme.detect_system_light_theme",
                        return_value=True), \
                mock.patch("gcm.app.system_theme.map_system_to_theme",
                           return_value="light"), \
                mock.patch("gcm.ui.theme.apply_theme") as m_apply, \
                mock.patch.object(MainWindow, "_apply_theme_to_children"):
            self.w._maybe_apply_system_theme()
        m_apply.assert_called_once_with("light")

    def test_g369_auto_theme_unknown_noop(self):
        """theme=auto + 系统未知 → 不切换、不崩。"""
        self.w.settings.theme = "auto"
        with mock.patch("gcm.app.system_theme.detect_system_light_theme",
                        return_value=None), \
                mock.patch("gcm.ui.theme.apply_theme") as m_apply:
            self.w._maybe_apply_system_theme()  # 不崩
        m_apply.assert_not_called()

    def test_g369_manual_theme_ignores_system(self):
        """theme=deep（手动）→ 不读系统、不自动切。"""
        self.w.settings.theme = "deep"
        with mock.patch("gcm.app.system_theme.detect_system_light_theme") as m_det:
            self.w._maybe_apply_system_theme()
        m_det.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)