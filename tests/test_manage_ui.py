# -*- coding: utf-8 -*-
"""仓库管理页交互槽的离屏 Qt 测试：补覆盖 main_window.py 管理页/设置页未覆盖槽。

覆盖：choose_target / open_target / check_update_now / save_log / delete_selected /
import_local_repos / _on_manage_double_clicked / _on_hist_btn / _refresh_manage /
show_history。
"""
from __future__ import annotations

import os; os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest import mock

from PyQt6.QtWidgets import QApplication, QMessageBox

from gcm import __version__
from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.models import RepoSpec, SyncAction, SyncStatus
from gcm.ui.main_window import MainWindow

# 共享 QApplication 实例（离屏）
_app = QApplication.instance() or QApplication(sys.argv)


@contextlib.contextmanager
def _msgbox_patch(question_return=QMessageBox.StandardButton.No):
    """把 gcm.ui.main_window.QMessageBox 整体替换为 Mock，并保留真实枚举值。

    由于 main_window 通过 `from PyQt6.QtWidgets import QMessageBox` 引用，
    必须替换模块级名字（不能直接对 Qt C++ 类打补丁）。
    question 默认返回 No（模拟用户拒绝，避免意外启动真实流程）。
    """
    with mock.patch("gcm.ui.main_window.QMessageBox") as m:
        m.StandardButton.Yes = QMessageBox.StandardButton.Yes
        m.StandardButton.No = QMessageBox.StandardButton.No
        m.question.return_value = question_return
        yield m


class TestManageUi(unittest.TestCase):
    """MainWindow 仓库管理页 / 设置页交互槽测试。"""

    def setUp(self):
        # Arrange：每个用例独立临时目录 + 独立窗口/DB，避免选择状态与数据互相污染
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")
        self.settings = SettingsStore(self.tmp / "settings.json")
        self.w = MainWindow(data_dir=self.tmp, db=self.db, settings=self.settings)
        self.w.show()

    def tearDown(self):
        # 先停掉日志节流定时器并 flush，再关闭窗口（closeEvent 会收尾引擎/DB）
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

    # ------------------------------------------------------------ 工具
    def _seed_repo(self, owner="octocat", repo="hello-world") -> int:
        """插入一条仓库记录并刷新管理页模型，返回 repo_id。"""
        spec = RepoSpec(owner=owner, repo=repo,
                        url_https=f"https://github.com/{owner}/{repo}.git")
        rid = self.db.upsert_repo(spec, str(self.tmp / "clones"), host="github.com")
        self.w._refresh_manage()
        return rid

    # ------------------------------------------------------------ choose_target
    def test_choose_target_updates_edit(self):
        """选择目录成功 → target_edit 文本更新为新目录。"""
        chosen = str(self.tmp / "chosen_dir")
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd:
            m_fd.getExistingDirectory.return_value = chosen
            self.w.choose_target()
        m_fd.getExistingDirectory.assert_called_once()
        self.assertEqual(self.w.target_edit.text(), chosen)

    def test_choose_target_cancel_keeps_text(self):
        """用户取消选择（返回空）→ target_edit 文本保持原值。"""
        original = self.w.target_edit.text()
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd:
            m_fd.getExistingDirectory.return_value = ""
            self.w.choose_target()
        self.assertEqual(self.w.target_edit.text(), original)

    # ------------------------------------------------------------ open_target
    def test_open_target_dir_exists_calls_startfile(self):
        """目标目录存在 → os.startfile 被调用，不弹警告。"""
        self.w.target_edit.setText(str(self.tmp))
        with mock.patch("os.startfile") as m_start, _msgbox_patch() as m_msg:
            self.w.open_target()
        m_start.assert_called_once_with(str(self.tmp))
        m_msg.warning.assert_not_called()

    def test_open_target_missing_warns(self):
        """目标目录不存在 → 弹「目录不存在」警告，不调 os.startfile。"""
        self.w.target_edit.setText(str(self.tmp / "does_not_exist"))
        with mock.patch("os.startfile") as m_start, _msgbox_patch() as m_msg:
            self.w.open_target()
        m_start.assert_not_called()
        m_msg.warning.assert_called_once()
        self.assertIn("目录不存在", m_msg.warning.call_args.args[1])

    # ------------------------------------------------------------ check_update_now
    def test_check_update_new_version_asks_and_opens_browser(self):
        """发现新版本（has_new=True, url 非空）→ 弹确认框且确认后打开下载页。"""
        url = "https://github.com/lza6/Git-clone-Max/releases/tag/v9.9.9"
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(True, "v9.9.9", url, "")) as m_check, \
                _msgbox_patch(QMessageBox.StandardButton.Yes) as m_msg, \
                mock.patch("webbrowser.open") as m_open:
            self.w.check_update_now()
        m_check.assert_called_once()
        m_msg.question.assert_called_once()
        text = m_msg.question.call_args.args[2]
        self.assertIn("最新版本 v9.9.9", text)
        self.assertIn(str(__version__), text)
        m_open.assert_called_once_with(url)

    def test_check_update_error_informs(self):
        """check_latest 返回 err → information 显示错误，不打开浏览器。"""
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(False, "", "", "网络错误")) as m_check, \
                _msgbox_patch() as m_msg, mock.patch("webbrowser.open") as m_open:
            self.w.check_update_now()
        m_msg.information.assert_called_once()
        self.assertIn("网络错误", m_msg.information.call_args.args[2])
        m_open.assert_not_called()

    def test_check_update_latest_informs(self):
        """已是最新版本（无 err、has_new=False）→ information 提示已最新。"""
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(False, "", "", "")) as m_check, \
                _msgbox_patch() as m_msg:
            self.w.check_update_now()
        m_msg.information.assert_called_once()
        self.assertIn("已是最新版本", m_msg.information.call_args.args[2])

    # ------------------------------------------------------------ save_log
    def test_save_log_writes_file(self):
        """选择导出路径 → 文件写入日志快照，且日志模型追加导出记录。"""
        out = self.tmp / "export.txt"
        before = self.w.log.to_plain_text()
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd:
            m_fd.getSaveFileName.return_value = (str(out), "文本文件 (*.txt)")
            self.w.save_log()
        self.assertTrue(out.exists(), "导出文件应被创建")
        self.assertEqual(out.read_text(encoding="utf-8"), before,
                         "文件内容应为导出时的日志快照")
        self.assertIn("日志已导出", self.w.log.to_plain_text())

    def test_save_log_cancel_no_write(self):
        """用户取消（返回空路径）→ 直接返回，不写任何文件、不追加日志。"""
        self.w.log.clear()
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd:
            m_fd.getSaveFileName.return_value = ("", "文本文件 (*.txt)")
            self.w.save_log()
        self.assertFalse((self.tmp / "clone_log.txt").exists(), "取消后不应生成默认文件")
        self.assertNotIn("日志已导出", self.w.log.to_plain_text())

    # ------------------------------------------------------------ delete_selected
    def test_delete_selected_confirmed_deletes_rows(self):
        """选中行 + 确认 → DB 计数与模型行数同步减少，弹确认框。"""
        self._seed_repo("a", "repo1")
        self._seed_repo("b", "repo2")
        self.assertEqual(self.db.count(), 2)
        self.w.manage_table.selectRow(0)
        with _msgbox_patch(QMessageBox.StandardButton.Yes) as m_msg:
            self.w.delete_selected()
        m_msg.question.assert_called_once()
        self.assertEqual(self.db.count(), 1, "确认后应从 DB 删除一条记录")
        self.assertEqual(self.w.manage_model.rowCount(), 1)
        self.assertEqual(self.w.manage_stats.text(), "共 1 个仓库")
        self.assertIn("已删除 1 条数据库记录", self.w.log.to_plain_text())

    def test_delete_selected_no_selection_prompts(self):
        """未选中任何行 → 弹「请先选择要删除的记录」，不删任何记录。"""
        self._seed_repo()
        with _msgbox_patch() as m_msg:
            self.w.delete_selected()
        m_msg.information.assert_called_once()
        self.assertIn("请先选择要删除的记录", m_msg.information.call_args.args[2])
        m_msg.question.assert_not_called()
        self.assertEqual(self.db.count(), 1)

    def test_delete_selected_cancel_keeps_rows(self):
        """选中行但确认框点「否」→ 不删除，计数不变。"""
        self._seed_repo()
        self.w.manage_table.selectRow(0)
        with _msgbox_patch(QMessageBox.StandardButton.No) as m_msg:
            self.w.delete_selected()
        m_msg.question.assert_called_once()
        self.assertEqual(self.db.count(), 1, "取消确认后不应删除记录")

    # ------------------------------------------------------------ import_local_repos
    def test_import_local_empty_selection_no_import(self):
        """对话框 selected 为空列表 → 不调用 import_selected，DB 计数不变。"""
        with mock.patch("gcm.ui.local_repos_dialog.LocalReposDialog") as m_dlg:
            m_dlg.return_value.selected = []
            self.w.import_local_repos()
        m_dlg.return_value.exec.assert_called_once()
        m_dlg.return_value.import_selected.assert_not_called()
        self.assertEqual(self.db.count(), 0)

    def test_import_local_selected_zero_no_crash(self):
        """对话框 selected 非空但 import_selected 返回 0 → 不崩、DB 计数不增加。"""
        self._seed_repo()
        with mock.patch("gcm.ui.local_repos_dialog.LocalReposDialog") as m_dlg:
            m_dlg.return_value.selected = [object()]
            m_dlg.return_value.import_selected.return_value = 0
            self.w.import_local_repos()
        m_dlg.return_value.exec.assert_called_once()
        m_dlg.return_value.import_selected.assert_called_once()
        self.assertEqual(self.db.count(), 1, "导入 0 个不应新增记录")
        self.assertEqual(self.w.manage_model.rowCount(), 1)

    # ------------------------------------------------------------ _on_manage_double_clicked
    def test_on_manage_double_clicked_calls_history(self):
        """双击有效行 → show_history 被调用且参数为该行 repo_id。"""
        rid = self._seed_repo()
        idx = self.w.manage_model.index(0, 0)
        self.assertTrue(idx.isValid())
        with mock.patch("gcm.ui.main_window.MainWindow.show_history") as m_sh:
            self.w._on_manage_double_clicked(idx)
        m_sh.assert_called_once()
        self.assertEqual(m_sh.call_args.args[0], rid)

    def test_on_manage_double_clicked_invalid_row_no_call(self):
        """双击无效行（越界）→ row_at 返回 None，不调 show_history。"""
        self._seed_repo()
        idx = self.w.manage_model.index(999, 0)
        self.assertFalse(idx.isValid())
        with mock.patch("gcm.ui.main_window.MainWindow.show_history") as m_sh:
            self.w._on_manage_double_clicked(idx)
        m_sh.assert_not_called()

    # ------------------------------------------------------------ _on_hist_btn
    def test_on_hist_btn_no_selection_no_crash(self):
        """无选中行 → 直接返回，不崩、不调 show_history。"""
        self._seed_repo()
        with mock.patch("gcm.ui.main_window.MainWindow.show_history") as m_sh:
            self.w._on_hist_btn()
        m_sh.assert_not_called()

    def test_on_hist_btn_selected_calls_history(self):
        """有选中行 → 对该行 show_history，参数为 repo_id。"""
        rid = self._seed_repo()
        self.w.manage_table.selectRow(0)
        with mock.patch("gcm.ui.main_window.MainWindow.show_history") as m_sh:
            self.w._on_hist_btn()
        m_sh.assert_called_once()
        self.assertEqual(m_sh.call_args.args[0], rid)

    # ------------------------------------------------------------ _refresh_manage
    def test_refresh_manage_syncs_model_rows(self):
        """DB 新增记录后调用刷新 → 模型行数与统计文案同步。"""
        spec = RepoSpec(owner="octocat", repo="hello-world",
                        url_https="https://github.com/octocat/hello-world.git")
        self.db.upsert_repo(spec, str(self.tmp / "clones"), host="github.com")
        self.assertEqual(self.w.manage_model.rowCount(), 0, "未刷新前模型应为空")
        self.w._refresh_manage()
        self.assertEqual(self.w.manage_model.rowCount(), 1)
        self.assertEqual(self.w.manage_model.row_at(0).repo, "hello-world")
        self.assertEqual(self.w.manage_stats.text(), "共 1 个仓库")

    # ------------------------------------------------------------ show_history
    def test_show_history_information_contains_lines(self):
        """有历史记录 → QMessageBox.information 文本包含格式化记录行。"""
        rid = self._seed_repo()
        self.db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 message="首次克隆完成", commits=5,
                                 started_at="2026-09-09 10:00:00")
        with _msgbox_patch() as m_msg:
            self.w.show_history(rid)
        m_msg.information.assert_called_once()
        text = m_msg.information.call_args.args[2]
        self.assertIn("2026-09-09 10:00:00", text)
        self.assertIn("cloned", text)
        self.assertIn("+5", text)
        self.assertIn("首次克隆完成", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
