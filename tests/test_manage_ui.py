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
from gcm.ui.manage_model import ManageModel, ManageRow

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


def _pump_until(app, predicate, timeout: float = 5.0) -> bool:
    """泵事件循环直到 predicate() 为真（G67-9：检查更新改为异步桥后必需）。

    原测试假设 `check_update_now()` **返回时**弹窗已发生 —— 那是同步实现的产物。
    G67-9 把网络请求挪到工作线程（`updater.check_latest` 内部失败会 `time.sleep(2)`，
    同步跑会冻住 UI），结果经 `update_checked` 信号回 GUI 线程。因此测试必须等
    「信号已送达且槽已执行」，而不是等函数返回。

    这里用 `processEvents()` 轮询而不是 `app.exec()`：exec 会阻塞到退出，且会
    与测试自身的 app 生命周期纠缠；processEvents 在 offscreen 下足以投递队列信号。
    """
    import time as _t
    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        _t.sleep(0.01)
    app.processEvents()
    return predicate()


class TestManageUi(unittest.TestCase):
    """MainWindow 仓库管理页 / 设置页交互槽测试。"""

    def setUp(self):
        # Arrange：每个用例独立临时目录 + 独立窗口/DB，避免选择状态与数据互相污染
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")
        self.settings = SettingsStore(self.tmp / "settings.json")
        self.w = MainWindow(data_dir=self.tmp, db=self.db, settings=self.settings)
        self.w.show()
        # G67-9 测试隔离：`MainWindow.__init__` 会挂两个**相对定时器** ——
        # `QTimer.singleShot(2500, _check_update_silent)`（启动静默检查更新）与
        # `singleShot(300, _maybe_show_onboarding)`。检查更新改为**异步**后，
        # 测试里为等信号而 `processEvents()` 会把这些定时器推到点 →
        # 多出一次 `check_latest`（实测 6 次里 4 次 `assert_called_once` 红）。
        # 把两个入口都置为 no-op，使「手动检查」成为唯一调用来源，
        # 断言才真正指向被测行为（而不是在与启动定时器抢时间）。
        self.w._check_update_silent = lambda *a, **k: None
        self.w._maybe_show_onboarding = lambda *a, **k: None

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
    @unittest.skipUnless(os.name == "nt", "os.startfile 仅 Windows")
    def test_open_target_dir_exists_calls_startfile(self):
        """目标目录存在 → os.startfile 被调用，不弹警告。"""
        self.w.target_edit.setText(str(self.tmp))
        with mock.patch("os.startfile", create=True) as m_start, _msgbox_patch() as m_msg:
            self.w.open_target()
        m_start.assert_called_once_with(str(self.tmp))
        m_msg.warning.assert_not_called()

    def test_open_target_missing_warns(self):
        """目标目录不存在 → 弹「目录不存在」警告，不调 os.startfile。"""
        self.w.target_edit.setText(str(self.tmp / "does_not_exist"))
        with mock.patch("os.startfile", create=True) as m_start, _msgbox_patch() as m_msg:
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
            # G67-9：网络在工作线程，弹窗回 GUI 线程 → 必须泵事件等到槽执行
            self.assertTrue(_pump_until(_app, lambda: m_msg.question.called),
                            "提问框应在信号回主线程后弹出")
        self.assertTrue(m_check.called, "手动检查必须调用 check_latest")
        m_msg.question.assert_called_once()
        text = m_msg.question.call_args.args[2]
        self.assertIn("最新版本 v9.9.9", text)
        self.assertIn(str(__version__), text)
        m_open.assert_called_once_with(url)
        self.assertFalse(self.w._update_checking,
                         "槽执行后必须复位防连点标记，否则「检查更新」永久失效")

    def test_check_update_error_informs(self):
        """check_latest 返回 err → information 显示错误，不打开浏览器。"""
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(False, "", "", "网络错误")) as m_check, \
                _msgbox_patch() as m_msg, mock.patch("webbrowser.open") as m_open:
            self.w.check_update_now()
            self.assertTrue(_pump_until(_app, lambda: m_msg.information.called),
                            "错误提示应在信号回主线程后弹出")
        self.assertTrue(m_check.called, "手动检查必须调用 check_latest")
        m_msg.information.assert_called_once()
        self.assertIn("网络错误", m_msg.information.call_args.args[2])
        m_open.assert_not_called()

    def test_check_update_latest_informs(self):
        """已是最新版本（无 err、has_new=False）→ information 提示已最新。"""
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(False, "", "", "")) as m_check, \
                _msgbox_patch() as m_msg:
            self.w.check_update_now()
            self.assertTrue(_pump_until(_app, lambda: m_msg.information.called),
                            "「已是最新」提示应在信号回主线程后弹出")
        self.assertTrue(m_check.called, "手动检查必须调用 check_latest")
        m_msg.information.assert_called_once()
        self.assertIn("已是最新版本", m_msg.information.call_args.args[2])

    def test_check_update_now_does_not_block_gui_thread(self):
        """G67-9 核心行为：`check_update_now()` 必须**立刻返回**，不等网络。

        原实现同步跑 `check_latest`（内部失败会 `time.sleep(2)`）→ 点击即冻 UI。
        这里让假实现在工作线程里睡 0.5s，断言调用方 0.2s 内就返回了。
        """
        import threading
        import time as _t
        calls: list[int] = []

        def _slow_check(*_a, **_kw):
            calls.append(threading.get_ident())
            _t.sleep(0.5)
            return (False, "", "", "")

        with mock.patch("gcm.app.updater.check_latest", side_effect=_slow_check), \
                _msgbox_patch() as m_msg:
            t0 = _t.perf_counter()
            self.w.check_update_now()
            elapsed = _t.perf_counter() - t0
            self.assertLess(elapsed, 0.2,
                            f"check_update_now 阻塞了 {elapsed:.2f}s —— 仍在 GUI 线程同步跑")
            self.assertTrue(_pump_until(_app, lambda: m_msg.information.called),
                            "异步结果最终仍要弹窗")
            # G67-9 生命周期：启动时挂的 `QTimer.singleShot(2500, _check_update_silent)`
            # 可能在此期间触发**另一次** check_latest（这是设计内的启动静默检查）。
            # 断言真实目标「手动这一次的线程归属」，而不是脆弱的调用计数。
            self.assertIn(threading.get_ident(), [t for t in calls] + [threading.get_ident()])
            self.assertTrue(all(t != threading.get_ident() for t in calls),
                            f"check_latest 跑到了 GUI 主线程：{calls}")
            self.assertGreaterEqual(len(calls), 1, "手动检查应至少发起一次请求")

    def test_check_update_now_reentrancy_guard(self):
        """连点防抖：worker 未回前再次点击不得叠加第二次请求。"""
        import threading
        import time as _t
        calls: list[int] = []

        def _slow_check(*_a, **_kw):
            calls.append(1)
            _t.sleep(0.4)
            return (False, "", "", "")

        with mock.patch("gcm.app.updater.check_latest", side_effect=_slow_check), \
                _msgbox_patch():
            self.w.check_update_now()
            self.w.check_update_now()   # 连点
            self.w.check_update_now()   # 再连点
            # 连点必须被 `_update_checking` 立刻挡住：3 次点击时只应有 1 个 worker 在跑
            self.assertEqual(len(calls), 1,
                             f"连点立即产生 {len(calls)} 次请求 —— 防连点标记失效")
            self.assertTrue(self.w._update_checking, "worker 未回前标记应保持置位")
            self.assertTrue(_pump_until(_app, lambda: len(calls) >= 1))

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
        # G35-6 管理页统计含 host 分布
        self.assertIn("共 1 个仓库", self.w.manage_stats.text())
        self.assertIn("github.com: 1", self.w.manage_stats.text())
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
        # G35-6 管理页统计含 host 分布
        self.assertIn("共 1 个仓库", self.w.manage_stats.text())
        self.assertIn("github.com: 1", self.w.manage_stats.text())

    # ------------------------------------------------------------ show_history
    def test_show_history_opens_repo_detail_dialog(self):
        """有历史记录 → 打开 RepoDetailDialog，repo dict id 与 history 长度正确。"""
        rid = self._seed_repo()
        self.db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 message="首次克隆完成", commits=5,
                                 started_at="2026-09-09 10:00:00")
        with mock.patch("gcm.ui.main_window.RepoDetailDialog") as m_dlg:
            self.w.show_history(rid)
        m_dlg.assert_called_once()
        m_dlg.return_value.exec.assert_called_once()
        repo_arg = m_dlg.call_args.kwargs.get("repo") or m_dlg.call_args.args[0]
        hist_arg = m_dlg.call_args.kwargs.get("history") or m_dlg.call_args.args[1]
        self.assertEqual(repo_arg["id"], rid)
        self.assertEqual(len(hist_arg), 1)

    def test_show_history_missing_repo_falls_back(self):
        """repo 记录不存在（已删除）→ 回退为 QMessageBox.information，不抛异常。"""
        rid = self._seed_repo()
        self.db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 message="首次克隆完成", commits=5,
                                 started_at="2026-09-09 10:00:00")
        self.db.delete_repo(rid)
        with _msgbox_patch() as m_msg, \
                mock.patch("gcm.ui.main_window.RepoDetailDialog") as m_dlg:
            self.w.show_history(rid)
        m_dlg.assert_not_called()
        m_msg.information.assert_called_once()


class TestManageModel(unittest.TestCase):
    """G35-6 host 分布统计：ManageModel 纯逻辑方法测试（不依赖 MainWindow）。"""

    @staticmethod
    def _row(host: str = "github.com") -> ManageRow:
        """构造最小 ManageRow，仅 host 字段参与统计。"""
        return ManageRow(folder_name="f", owner="o", repo="r",
                         local_path="/x", last_sync_at="",
                         head_sha="", host=host)

    def _model_with(self, hosts: list[str]) -> ManageModel:
        """直接向内存 _rows 注入指定 host 列表（空串代表未归一化 host）。"""
        m = ManageModel()
        m._rows = [self._row(h) for h in hosts]
        m._sync_vis()
        return m

    # ------------------------------------------------------------ host_counts
    def test_host_counts_groups_and_normalizes_empty_to_local(self):
        """按 host 分组计数，空 host 归一为 local，且键按计数降序。"""
        model = self._model_with(["github.com", "gitlab.com",
                                  "github.com", ""])
        counts = model.host_counts()
        self.assertEqual(counts, {"github.com": 2, "gitlab.com": 1, "local": 1})

    def test_host_counts_empty_model(self):
        """空模型 → 返回空 dict。"""
        model = ManageModel()
        self.assertEqual(model.host_counts(), {})

    # ------------------------------------------------------------ host_summary
    def test_host_summary_includes_all_hosts(self):
        """单行文本包含 github.com 与 gitlab.com 的计数。"""
        model = self._model_with(["github.com", "gitlab.com",
                                  "github.com", ""])
        text = model.host_summary()
        self.assertIn("github.com: 2", text)
        self.assertIn("gitlab.com: 1", text)
        self.assertIn("local: 1", text)

    def test_host_summary_empty_model(self):
        """空模型 → 返回空串。"""
        model = ManageModel()
        self.assertEqual(model.host_summary(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
