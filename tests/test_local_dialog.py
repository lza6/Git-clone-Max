# -*- coding: utf-8 -*-
"""本地仓库导入对话框（local_repos_dialog）+ 扫描后台线程（scan_worker）单元测试。

覆盖 LocalReposDialog 的根目录加载 / 扫描调度 / 完成与失败回填 / 过滤 / 表格渲染 /
勾选汇总 / 导入写库，以及 ScanWorker 的后台扫描信号（真实 git 仓库）与 _dedupe 去重。
不启动真实后台线程：_start_scan 里的 ScanWorker 以 mock 替代，仅验证调度行为。
"""
from __future__ import annotations

import os; os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QCheckBox

from gcm.app.scanner import RepoInfo
from gcm.db.repo_db import Database
from gcm.ui.local_repos_dialog import LocalReposDialog
from gcm.ui.scan_worker import ScanWorker, _dedupe

# 共享 QApplication 实例（离屏）
_app = QApplication.instance() or QApplication(sys.argv)


def _pump():
    """泵事件循环，让跨线程信号（QueuedConnection）投递到主线程。"""
    for _ in range(20):
        _app.processEvents()
        time.sleep(0.01)


def _init_git(path: Path, commit=True):
    """初始化真实 git 仓库（复刻 test_scanner.py 的辅助）。"""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    if commit:
        (path / "a.txt").write_text("v1", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=path,
                       check=True, capture_output=True)


class TestLocalReposDialog(unittest.TestCase):
    """LocalReposDialog 交互槽测试。

    setUp 统一把模块里的 QTimer（构造时的自动扫描定时器）与 ScanWorker 替换为 mock：
    - QTimer.singleShot → no-op，避免残留定时器在后续事件泵中触发真实扫描/弹窗；
    - ScanWorker → 不启动真实后台线程，仅记录构造与 start() 调用。
    """

    def setUp(self):
        # Arrange
        self.tmp = Path(tempfile.mkdtemp())
        self._qtimer_patch = mock.patch("gcm.ui.local_repos_dialog.QTimer")
        self._worker_patch = mock.patch("gcm.ui.local_repos_dialog.ScanWorker")
        self.m_qtimer = self._qtimer_patch.start()
        self.m_worker = self._worker_patch.start()

    def tearDown(self):
        self._worker_patch.stop()
        self._qtimer_patch.stop()

    # ------------------------------------------------------------ _load_roots
    def test_load_roots_sets_combo_text(self):
        """root_paths 传入 → root_combo 文本为分号分隔。"""
        # Arrange
        d1 = self.tmp / "a"; d1.mkdir()
        d2 = self.tmp / "b"; d2.mkdir()
        dlg = LocalReposDialog(parent=None, root_paths=[str(d1), str(d2)], db=None)
        try:
            # Act / Assert
            self.assertEqual(dlg.root_combo.text(), ";".join([str(d1), str(d2)]))
        finally:
            dlg.close()

    def test_load_roots_filters_missing_dirs(self):
        """root_paths 中不存在的目录被过滤，不进入 root_combo。"""
        # Arrange
        d1 = self.tmp / "a"; d1.mkdir()
        dlg = LocalReposDialog(parent=None,
                               root_paths=[str(d1), str(self.tmp / "missing")], db=None)
        try:
            # Act / Assert
            self.assertEqual(dlg.root_combo.text(), str(d1))
        finally:
            dlg.close()

    # ------------------------------------------------------------ _start_scan
    def test_start_scan_empty_roots_informs(self):
        """扫描目录为空 → 弹「请先填写」提示，不构造 worker。"""
        # Arrange
        dlg = LocalReposDialog(parent=None, root_paths=[], db=None)
        try:
            # Act
            with mock.patch("gcm.ui.local_repos_dialog.QMessageBox") as m_msg:
                dlg._start_scan()
            # Assert
            m_msg.information.assert_called_once()
            self.assertIn("请先填写", m_msg.information.call_args.args[2])
            self.m_worker.assert_not_called()
        finally:
            dlg.close()

    def test_start_scan_sets_worker_and_ignores_second_call(self):
        """有效目录 → 构造 ScanWorker 并 start；已有 worker 时再次调用直接返回。"""
        # Arrange
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            # Act：第一次扫描
            dlg._start_scan()
            # Assert
            self.assertEqual(self.m_worker.call_count, 1)
            self.m_worker.return_value.start.assert_called_once()
            self.assertFalse(dlg.progress.isHidden(), "扫描中进度条不应隐藏")
            self.assertEqual(dlg.status.text(), "扫描中…")
            # Act：worker 仍在运行时再次扫描 → 直接返回
            dlg._start_scan()
            # Assert
            self.assertEqual(self.m_worker.call_count, 1, "已有 worker 时不应再次构造")
            self.m_worker.return_value.start.assert_called_once()
        finally:
            dlg.close()

    # ------------------------------------------------------------ 扫描回填
    def test_on_scan_done_updates_rows_status_table(self):
        """扫描完成 → 行数据、状态文案、表格行数同步更新，扫描态复位。"""
        # Arrange
        infos = [
            RepoInfo(folder_name="o__r", display="o/r", path=str(self.tmp / "o__r"),
                     remote_url="https://github.com/o/r.git", owner="o", repo="r"),
            RepoInfo(folder_name="plain", display="plain", path=str(self.tmp / "plain")),
        ]
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            # Act
            dlg._on_scan_done(infos)
            # Assert
            self.assertEqual(dlg._rows, infos)
            self.assertEqual(dlg.status.text(), "共发现 2 个本地仓库")
            self.assertEqual(dlg.table.rowCount(), 2)
            self.assertFalse(dlg.progress.isVisible(), "扫描结束后进度条应隐藏")
            self.assertIsNone(dlg._worker)
        finally:
            dlg.close()

    def test_on_scan_failed_warns_and_resets(self):
        """扫描失败 → 弹警告、停止扫描态、状态文案含错误、表格恢复可用。"""
        # Arrange
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            # Act
            with mock.patch("gcm.ui.local_repos_dialog.QMessageBox") as m_msg:
                dlg._on_scan_failed("磁盘错误")
            # Assert
            m_msg.warning.assert_called_once()
            self.assertIn("磁盘错误", m_msg.warning.call_args.args[2])
            self.assertIn("扫描失败", dlg.status.text())
            self.assertFalse(dlg.progress.isVisible())
            self.assertIsNone(dlg._worker)
            self.assertTrue(dlg.table.isEnabled(), "失败后表格应恢复可用")
        finally:
            dlg.close()

    # ------------------------------------------------------------ 过滤 / 表格
    def test_apply_filter_match_and_mismatch(self):
        """过滤关键字匹配/不匹配，空串显示全部。"""
        # Arrange
        infos = [
            RepoInfo(folder_name="alpha", display="alpha/repo", path="/x/alpha_repo"),
            RepoInfo(folder_name="beta", display="beta/repo", path="/x/beta_repo"),
        ]
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            dlg._rows = infos
            # Act：setText 触发 textChanged → _apply_filter
            dlg.filter_edit.setText("alpha")
            # Assert
            self.assertEqual(dlg.table.rowCount(), 1)
            self.assertEqual(dlg.table.item(0, 1).text(), "alpha/repo")
            # Act：不匹配
            dlg.filter_edit.setText("zzz")
            self.assertEqual(dlg.table.rowCount(), 0)
            # Act：空串 → 全部
            dlg.filter_edit.setText("")
            self.assertEqual(dlg.table.rowCount(), 2)
        finally:
            dlg.close()

    def test_rebuild_table_columns(self):
        """表格 4 列：勾选框默认选中，仓库/远端/路径文本正确。"""
        # Arrange
        infos = [
            RepoInfo(folder_name="o__r", display="o/r", path="/x/o__r",
                     remote_url="https://github.com/o/r.git", owner="o", repo="r"),
            RepoInfo(folder_name="plain", display="plain", path="/x/plain", remote_url=""),
        ]
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            # Act
            dlg._rows = infos
            dlg._apply_filter("")
            # Assert
            self.assertEqual(dlg.table.rowCount(), 2)
            chk = dlg.table.cellWidget(0, 0)
            self.assertIsInstance(chk, QCheckBox)
            self.assertTrue(chk.isChecked(), "默认应勾选")
            self.assertEqual(dlg.table.item(0, 1).text(), "o/r")
            self.assertEqual(dlg.table.item(0, 2).text(), "https://github.com/o/r.git")
            self.assertEqual(dlg.table.item(1, 2).text(), "（无远端）")
            self.assertEqual(dlg.table.item(0, 3).text(), "/x/o__r")
        finally:
            dlg.close()

    # ------------------------------------------------------------ 勾选汇总
    def test_check_select_all_none(self):
        """勾选状态汇总：默认全选、全不选、全选、手动取消后只算勾选项。"""
        # Arrange
        infos = [
            RepoInfo(folder_name="a", display="a", path="/x/a"),
            RepoInfo(folder_name="b", display="b", path="/x/b"),
        ]
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            dlg._rows = infos
            dlg._apply_filter("")
            # Act / Assert：默认全勾
            self.assertEqual(len(dlg._checked()), 2)
            # Act：全不选
            dlg._select_none()
            self.assertEqual(dlg._checked(), [])
            # Act：全选
            dlg._select_all()
            self.assertEqual(len(dlg._checked()), 2)
            # Act：手动取消第 0 行
            dlg.table.cellWidget(0, 0).setChecked(False)
            self.assertEqual(len(dlg._checked()), 1)
            self.assertEqual(dlg._checked()[0].path, "/x/b")
        finally:
            dlg.close()

    # ------------------------------------------------------------ 导入
    def test_on_import_no_selection_informs_and_keeps_open(self):
        """未勾选 → 提示「请至少勾选」，不 accept。"""
        # Arrange
        infos = [RepoInfo(folder_name="a", display="a", path="/x/a")]
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            dlg._rows = infos
            dlg._apply_filter("")
            dlg._select_none()
            # Act
            with mock.patch("gcm.ui.local_repos_dialog.QMessageBox") as m_msg, \
                    mock.patch.object(dlg, "accept") as m_accept:
                dlg._on_import()
            # Assert
            m_msg.information.assert_called_once()
            self.assertIn("请至少勾选", m_msg.information.call_args.args[2])
            m_accept.assert_not_called()
        finally:
            dlg.close()

    def test_on_import_with_selection_accepts(self):
        """有勾选 → _selected 记录勾选项并 accept。"""
        # Arrange
        infos = [
            RepoInfo(folder_name="a", display="a", path="/x/a"),
            RepoInfo(folder_name="b", display="b", path="/x/b"),
        ]
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            dlg._rows = infos
            dlg._apply_filter("")
            # Act
            with mock.patch.object(dlg, "accept") as m_accept:
                dlg._on_import()
            # Assert
            m_accept.assert_called_once()
            self.assertEqual(len(dlg.selected), 2)
        finally:
            dlg.close()

    def test_import_and_accept_helper(self):
        """_import_and_accept：勾选 → accept；未勾选 → 提示且不 accept。"""
        # Arrange：勾选分支
        infos = [RepoInfo(folder_name="a", display="a", path="/x/a")]
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            dlg._rows = infos
            dlg._apply_filter("")
            # Act
            with mock.patch.object(dlg, "accept") as m_accept:
                dlg._import_and_accept()
            # Assert
            m_accept.assert_called_once()
            self.assertEqual(len(dlg.selected), 1)
        finally:
            dlg.close()
        # Arrange：未勾选分支
        dlg2 = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            dlg2._rows = infos
            dlg2._apply_filter("")
            dlg2._select_none()
            # Act
            with mock.patch("gcm.ui.local_repos_dialog.QMessageBox") as m_msg, \
                    mock.patch.object(dlg2, "accept") as m_accept:
                dlg2._import_and_accept()
            # Assert
            m_msg.information.assert_called_once()
            m_accept.assert_not_called()
        finally:
            dlg2.close()

    def test_import_selected_writes_db(self):
        """真实临时 DB：import_selected 写入并返回导入数，count()==1。"""
        # Arrange
        db = Database(self.tmp / "t.db")
        info = RepoInfo(folder_name="o__r", display="o/r",
                        path=str(self.tmp / "clones" / "o__r"),
                        remote_url="", owner="o", repo="r")
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=db)
        try:
            dlg._selected = [info]
            # Act
            n = dlg.import_selected()
            # Assert
            self.assertEqual(n, 1)
            self.assertEqual(db.count(), 1)
            row = db.list_repos()[0]
            self.assertEqual(row["owner"], "o")
            self.assertEqual(row["repo"], "r")
        finally:
            dlg.close()
            db.close()

    def test_import_selected_noop_without_db_or_selection(self):
        """无 DB 或未勾选 → 返回 0，不写库。"""
        # Arrange：无 DB
        db = Database(self.tmp / "t.db")
        info = RepoInfo(folder_name="o__r", display="o/r", path="/x/o__r", owner="o", repo="r")
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=None)
        try:
            dlg._selected = [info]
            # Act / Assert
            self.assertEqual(dlg.import_selected(), 0, "无 DB → 0")
        finally:
            dlg.close()
        # Arrange：未勾选
        dlg2 = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=db)
        try:
            # Act / Assert
            self.assertEqual(dlg2.import_selected(), 0, "未勾选 → 0")
            self.assertEqual(db.count(), 0)
        finally:
            dlg2.close()
            db.close()

    # ------------------------------------------------------------ 基本构建
    def test_construction_and_close(self):
        """基本构建不崩，close 正常，selected 初始为空。"""
        # Arrange
        db = Database(self.tmp / "t.db")
        dlg = LocalReposDialog(parent=None, root_paths=[str(self.tmp)], db=db)
        # Act / Assert
        self.assertEqual(dlg.windowTitle(), "导入本地已有仓库")
        self.assertEqual(dlg.root_combo.text(), str(self.tmp))
        self.assertEqual(dlg.selected, [])
        self.assertEqual(dlg.table.columnCount(), 4)
        # Act：close
        dlg.close()
        db.close()


class TestScanWorker(unittest.TestCase):
    """scan_worker：_dedupe 纯函数 + start 跨线程信号（真实 git 仓库）。"""

    def setUp(self):
        # Arrange
        self.tmp = Path(tempfile.mkdtemp())

    def test_dedupe_keeps_unique_keys_sorted(self):
        """重复 key（owner/repo 相同）去重、路径 key 区分、按路径排序。"""
        # Arrange
        a = RepoInfo(folder_name="o__r", display="o/r", path="/z/o__r",
                     remote_url="https://github.com/o/r.git", owner="o", repo="r")
        b = RepoInfo(folder_name="o__r", display="o/r", path="/a/o__r",
                     remote_url="https://github.com/o/r.git", owner="o", repo="r")  # 同 key
        c = RepoInfo(folder_name="plain", display="plain", path="/b/plain")    # 路径 key
        d = RepoInfo(folder_name="plain2", display="plain2", path="/a/plain2")  # 路径 key
        # Act
        out = _dedupe([a, b, c, d])
        # Assert
        self.assertEqual(len(out), 3)
        self.assertEqual([i.path for i in out], ["/a/plain2", "/b/plain", "/z/o__r"])
        self.assertEqual(out[-1].owner, "o", "o/r 去重后保留首个出现")

    def test_start_emits_finished_with_real_repo(self):
        """真实临时 git 仓库 → finished 信号收到扫描结果。"""
        # Arrange
        base = self.tmp / "clones"
        _init_git(base / "owner__repo")
        w = ScanWorker([base], max_depth=2)
        results, errors = [], []
        w.finished.connect(results.append)
        w.failed.connect(errors.append)
        # Act
        w.start()
        deadline = time.time() + 15
        while not results and not errors and time.time() < deadline:
            _pump()
        # Assert
        self.assertTrue(results, "应收到 finished 信号")
        self.assertFalse(errors, "不应收到 failed")
        found = results[0]
        names = {i.folder_name for i in found}
        self.assertIn("owner__repo", names)
        self.assertTrue(all(i.head_sha for i in found), "真实 git 仓库应有 HEAD")

    def test_start_missing_dir_emits_finished_empty(self):
        """目录不存在 → scan_git_dirs 返回空 → finished 收到空列表。"""
        # Arrange
        w = ScanWorker([self.tmp / "not_exist"], max_depth=2)
        results, errors = [], []
        w.finished.connect(results.append)
        w.failed.connect(errors.append)
        # Act
        w.start()
        deadline = time.time() + 15
        while not results and not errors and time.time() < deadline:
            _pump()
        # Assert
        self.assertTrue(results, "应收到 finished 信号")
        self.assertEqual(results[0], [])
        self.assertFalse(errors)

    def test_start_emits_failed_on_scan_error(self):
        """scan_git_dirs 抛异常 → failed 信号收到错误信息。"""
        # Arrange
        w = ScanWorker([self.tmp], max_depth=2)
        results, errors = [], []
        w.finished.connect(results.append)
        w.failed.connect(errors.append)
        # Act：让扫描函数抛异常验证 failed 分支
        with mock.patch("gcm.ui.scan_worker.scan_git_dirs",
                        side_effect=RuntimeError("boom")):
            w.start()
            deadline = time.time() + 15
            while not results and not errors and time.time() < deadline:
                _pump()
        # Assert
        self.assertTrue(errors)
        self.assertIn("boom", errors[0])
        self.assertFalse(results)


if __name__ == "__main__":
    unittest.main(verbosity=2)
