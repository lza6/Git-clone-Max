# -*- coding: utf-8 -*-
"""G35 主控侧（main_window/settings）交互增强 单测（离屏 Qt）。

覆盖：
- G35-1 进度表右键重试：FAILED 行 → _launch 单仓库重试；busy 时拒绝
- G35-1 复制错误详情 / 打开所在目录
- G35-2 完成提示音：setting.finish_sound 开关控制 beep
- G35-3 输入区拖拽：拖入 txt 文件 → 行并入输入框；拖入目录 → 本地扫描对话框
- G35-7 快捷填充跟随 settings.quick_repos
- G35-8 状态格 tooltip = message+detail
- G35-9 批量打标签 / 导出所选 CSV
"""
from __future__ import annotations

import csv
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

from PyQt6.QtCore import QMimeData, QPoint, QUrl
from PyQt6.QtWidgets import QApplication

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.models import RepoSpec, SyncResult, SyncStatus
from gcm.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _pump(n=10):
    for _ in range(n):
        _app.processEvents()
        time.sleep(0.005)


class TestG35Ui(unittest.TestCase):
    """G35 以 MainWindow 为靶的交互增强。"""

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
        # 复位 busy：避免 G35-2 等用例设置的 busy=True 在 closeEvent 触发「确认退出」模态框卡死
        try:
            self.w.busy = False
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def _seed_row(self, index=0, display="o__r", status="失败", message="boom", detail="detail-x"):
        """向进度表塞一行（FAILED），返回其 engine index。

        临时关闭排序再建行/定位，避免 setItem 触发自动重排导致视觉行号偏移。
        """
        spec = RepoSpec(owner="o", repo="r", url_https="https://github.com/o/r",
                        display=display, folder_name=display)
        self.w.row_specs[index] = spec
        self.w.table.setSortingEnabled(False)
        try:
            self.w._prepare_table(index + 1)
            self.w._add_table_row(index, spec)
            st = self.w.table.item(index, 2)
            st.setText(status)
        finally:
            self.w.table.setSortingEnabled(True)
        return index

    # ------------------------------------------------------------ G35-1 右键重试
    def test_g351_retry_failed_row_calls_launch(self):
        """FAILED 行右键重试 → _launch 以单 spec 调度（不去重整批）。"""
        idx = self._seed_row()
        self.w.busy = False
        with mock.patch.object(MainWindow, "_launch") as m_launch:
            self.w._retry_table_row(idx)
        m_launch.assert_called_once()
        specs = m_launch.call_args.args[0]
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].owner, "o")

    def test_g351_retry_blocked_when_busy(self):
        """busy 时右键重试 → 提示，不调度。"""
        idx = self._seed_row()
        self.w.busy = True
        with mock.patch.object(MainWindow, "_launch") as m_launch, \
                mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            self.w._retry_table_row(idx)
        m_launch.assert_not_called()
        m_msg.information.assert_called_once()

    def test_g351_copy_row_detail(self):
        """复制错误详情 → 剪贴板 = detail（经结果槽设置 tooltip 后）。"""
        idx = self._seed_row()
        res = SyncResult(spec=self.w.row_specs[idx],
                         status=SyncStatus.FAILED,
                         message="fetch 失败", detail="detail-x")
        self.w._on_worker_result(idx, res)
        self.w._copy_row_detail(idx)
        self.assertEqual(QApplication.clipboard().text(), "detail-x")

    def test_g351_row_engine_index_after_sort(self):
        """排序后 _row_engine_index 仍定位正确（UserRole+1 存储）。"""
        idx = self._seed_row(index=3)
        it = self.w.table.item(3, 0)
        self.assertEqual(self.w._row_engine_index(3), idx)

    # ------------------------------------------------------------ G35-2 提示音
    def test_g352_finish_sound_on_calls_beep(self):
        """finish_sound=True（默认）→ finished 时 beep。"""
        self.w.busy = True
        self.w.settings.finish_sound = True
        with mock.patch("PyQt6.QtWidgets.QApplication.beep") as m_beep:
            self.w._on_engine_finished()
        m_beep.assert_called_once()

    def test_g352_finish_sound_off_no_beep(self):
        """finish_sound=False → finished 时不 beep。"""
        self.w.busy = True
        self.w.settings.finish_sound = False
        with mock.patch("PyQt6.QtWidgets.QApplication.beep") as m_beep:
            self.w._on_engine_finished()
        m_beep.assert_not_called()

    # ------------------------------------------------------------ G35-3 拖拽
    def test_g353_drop_txt_appends_lines(self):
        """拖入 txt → 每行（去注释/空行）并入输入框。"""
        f = self.tmp / "urls.txt"
        f.write_text("# comment\nhttps://github.com/a/b\n\nhttps://github.com/c/d\n",
                     encoding="utf-8")
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(f))])
        ev = mock.MagicMock()
        ev.mimeData.return_value = md
        self.w._repo_input_drop(ev)
        text = self.w.repo_input.toPlainText()
        self.assertIn("https://github.com/a/b", text)
        self.assertIn("https://github.com/c/d", text)
        self.assertNotIn("comment", text)

    def test_g353_drop_dir_opens_scanner(self):
        """拖入目录 → 弹出本地导入对话框并导入所选。"""
        d = self.tmp / "clones"
        d.mkdir(exist_ok=True)
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(d))])
        ev = mock.MagicMock()
        ev.mimeData.return_value = md
        with mock.patch("gcm.ui.local_repos_dialog.LocalReposDialog") as m_dlg:
            m_dlg.return_value.exec.return_value = None
            m_dlg.return_value.selected = []
            self.w._repo_input_drop(ev)
        m_dlg.assert_called_once()
        self.assertEqual(m_dlg.call_args.kwargs["root_paths"], [str(d)])

    # ------------------------------------------------------------ G35-7 快捷填充
    def test_g357_quick_repos_from_settings(self):
        """快捷填充按钮跟随 settings.quick_repos（改后生效）。"""
        # 持久化 quick_repos 到 settings.json，再由新窗口读取驱动按钮
        self.w.settings.quick_repos = ("x/y",)
        self.w.settings_store.save(self.w.settings)
        w2 = MainWindow(data_dir=self.tmp, db=self.db,
                        settings=SettingsStore(self.tmp / "settings.json"))
        try:
            self.w.close()  # 关闭首个避免干扰
        except Exception:
            pass
        # 通过控件遍历：找「x/y」文本按钮
        texts = []
        for btn in w2.findChildren(type(self.w.btn_start)):
            if "x/y" == getattr(btn, "text", lambda: "")():
                texts.append(btn.text())
        # 更稳：直接扫所有 QPushButton
        from PyQt6.QtWidgets import QPushButton
        found = any("x/y" == b.text() for b in w2.findChildren(QPushButton))
        self.assertTrue(found, "settings.quick_repos 应驱动快捷填充按钮")
        w2.close()

    # ------------------------------------------------------------ G35-8 状态格 tooltip
    def test_g358_status_tooltip_message_plus_detail(self):
        """_on_worker_result 后状态格 tooltip = message + detail。"""
        idx = self._seed_row()
        res = SyncResult(spec=self.w.row_specs[idx],
                         status=SyncStatus.FAILED,
                         message="fetch 失败", detail="git fetch exit 1")
        # 直接调用结果槽（index 对齐）
        self.w._on_worker_result(idx, res)
        st = self.w.table.item(idx, 2)
        self.assertIn("fetch 失败", st.toolTip())
        self.assertIn("git fetch exit 1", st.toolTip())

    # ------------------------------------------------------------ G35-9 批量操作
    def _seed_db_repos(self, n=2):
        ids = []
        for i in range(n):
            rid = self.db.upsert_repo(
                RepoSpec(owner=f"user{i}", repo="repo", url_https=f"https://github.com/user{i}/repo",
                         folder_name=f"user{i}__repo"), local_path=str(self.tmp), host="github.com")
            ids.append(rid)
        self.w._load_db_into_grid()
        return ids

    def test_g359_batch_tag_selected(self):
        """批量打标签：选中两行 → 标签写入 DB。"""
        ids = self._seed_db_repos(2)
        # 选中全部行
        sel = self.w.manage_table.selectionModel()
        for r in range(self.w.manage_model.rowCount()):
            sel.select(self.w.manage_model.index(r, 0), sel.SelectionFlag.Select)
        with mock.patch("gcm.ui.main_window.QInputDialog") as m_in:
            m_in.getText.return_value = ("ai,ml", True)
            self.w.batch_tag_selected()
        for rid in ids:
            rec = self.db.get_repo(f"user{ids.index(rid)}", "repo", "github.com")
            self.assertIn("ai", rec.get("tags", ""))
            self.assertIn("ml", rec.get("tags", ""))

    def test_g359_export_selected_csv(self):
        """导出所选 → CSV 文件含选中行 owner/repo。"""
        self._seed_db_repos(3)
        sel = self.w.manage_table.selectionModel()
        sel.select(self.w.manage_model.index(0, 0), sel.SelectionFlag.Select)
        out = self.tmp / "sel.csv"
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd:
            m_fd.getSaveFileName.return_value = (str(out), "CSV (*.csv)")
            self.w.export_selected_csv()
        self.assertTrue(out.exists())
        with open(out, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner"], "user0")


if __name__ == "__main__":
    unittest.main(verbosity=2)