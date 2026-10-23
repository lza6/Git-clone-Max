"""G76 接线收口守卫：**「有读侧、没写侧」和「零调用点」的能力必须真的可达**。

## 为什么需要这个文件
`docs/计划书/下一步改进指南.md` 的问题清单里有一类反复出现的缺陷：
**后端/引擎早就在读某个设置或信号，但设置页没有写入点、或函数零生产调用** ——
用户永远够不到，文档却写着能做。历史上已经栽过好几次：

- 2026-10-16 独立复验推翻「台账全部闭环」：9 个设置字段只读不写；
- 2026-10-19 G69-18 又抓出 6 个同类字段（本文件第 1 组用例就是它们）。

判定口径（`.agent/skills/plan-doc-audit.md` §2）：**控件 → 保存回调 → 消费方** 三段都要通。
所以下面每条用例都按「三段」断言，而不是只断言控件存在：

1. 控件存在且回填了当前配置值（读侧）；
2. 触发保存后 `settings` 变化**且落盘**（用新的 `SettingsStore` 重读，防「只改内存」）；
3. 消费方确实读这个字段（用 grep 式的源码断言或行为断言）。

## 反向自检（守卫必须能失败）
- 删掉任一保存回调的 `settings_store.save(...)` → `test_*_persists` 必红；
- 把 `edit_post_hook` 的确认逻辑删掉 → `test_post_clone_hook_requires_confirmation` 必红；
- 把 `ScanWorker.progress` 的 connect 删掉 → `test_scan_progress_becomes_determinate` 必红；
- 把 `manage_model.rowsChanged` 的 connect 删掉 → `test_manage_stats_follow_model_signals` 必红。
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database  # noqa: E402
from gcm.db.settings import SettingsStore  # noqa: E402
from gcm.ui.local_repos_dialog import LocalReposDialog  # noqa: E402
from gcm.ui.main_window import MainWindow  # noqa: E402

# G76 必须在设置页可达的控件（删掉即红）
G76_WIDGETS = (
    "ck_minimize_to_tray", "lbl_data_dir", "btn_migrate_data_dir",
    "spin_clone_timeout", "edit_post_hook",
    "ck_watchdog_silent", "combo_sched_format",
)
# G76 必须在下载中心可达的控件
G76_DOWNLOAD_WIDGETS = ("btn_edit_quick",)


class _Win(unittest.TestCase):
    """每个用例一个干净窗口 + 独立数据目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gcm_g76_")
        self.dir = pathlib.Path(self._tmp.name)
        self.win = MainWindow(data_dir=self.dir, db=Database(self.dir / "t.db"),
                              settings=SettingsStore(self.dir / "settings.json"))
        self.win._can_show_modal = lambda: True  # 离屏下不弹真模态

    def tearDown(self):
        try:
            # 必须先把 busy 复位：`closeEvent` 在 busy=True 时会弹「确认退出」模态，
            # 离屏窗口下 `QMessageBox.question` 会永久阻塞（G68-4 的教训）。
            self.win.busy = False
            self.win.close()
            self.win.deleteLater()
        finally:
            self._tmp.cleanup()

    def reload(self):
        """重新读盘上的设置（验证"真的落盘"而不是只改内存）。"""
        return SettingsStore(self.dir / "settings.json")


class TestSettingsWidgetsExist(_Win):
    def test_g76_widgets_are_reachable(self):
        missing = [n for n in G76_WIDGETS if not hasattr(self.win, n)]
        self.assertEqual(missing, [], f"设置页缺少 G76 控件：{missing}")

    def test_quick_edit_button_is_reachable(self):
        missing = [n for n in G76_DOWNLOAD_WIDGETS if not hasattr(self.win, n)]
        self.assertEqual(missing, [], f"下载中心缺少快捷填充编辑入口：{missing}")

    def test_widgets_reflect_configured_values(self):
        """非默认值必须显示在控件上（默认值无法区分"读了"和"没读"）。"""
        s = self.win.settings
        s.clone_timeout = 1234
        s.minimize_to_tray = False
        s.post_clone_hook = "echo hi"
        s.watchdog_silent = True
        s.sched_report_format = "md"
        self.win.settings_store.save(s)
        # 重建窗口：新窗口必须回填
        win2 = MainWindow(data_dir=self.dir, db=Database(self.dir / "t2.db"),
                          settings=SettingsStore(self.dir / "settings.json"))
        win2._can_show_modal = lambda: True
        try:
            self.assertEqual(win2.spin_clone_timeout.value(), 1234)
            self.assertFalse(win2.ck_minimize_to_tray.isChecked())
            self.assertEqual(win2.edit_post_hook.text(), "echo hi")
            self.assertTrue(win2.ck_watchdog_silent.isChecked())
            self.assertEqual(win2.combo_sched_format.currentData(), "md")
        finally:
            win2.close()
            win2.deleteLater()


class TestSettingsWritesPersist(_Win):
    def test_clone_timeout_persists(self):
        self.win.spin_clone_timeout.setValue(900)
        self.assertEqual(self.win.settings.clone_timeout, 900)
        self.assertEqual(self.reload().load().clone_timeout, 900)

    def test_minimize_to_tray_persists(self):
        self.win.ck_minimize_to_tray.setChecked(False)
        self.assertFalse(self.win.settings.minimize_to_tray)
        self.assertFalse(self.reload().load().minimize_to_tray)

    def test_watchdog_silent_persists(self):
        self.win.ck_watchdog_silent.setChecked(True)
        self.assertTrue(self.win.settings.watchdog_silent)
        self.assertTrue(self.reload().load().watchdog_silent)

    def test_sched_report_format_persists(self):
        combo = self.win.combo_sched_format
        idx = combo.findData("md")
        self.assertGreaterEqual(idx, 0, "报表格式下拉必须含 md")
        combo.setCurrentIndex(idx)
        self.assertEqual(self.win.settings.sched_report_format, "md")
        self.assertEqual(self.reload().load().sched_report_format, "md")


class TestPostCloneHookSecurity(_Win):
    def test_post_clone_hook_requires_confirmation(self):
        """钩子 = 任意命令执行面：确认被拒绝时必须**不写盘且恢复旧值**。"""
        edit = self.win.edit_post_hook
        edit.setText("rm -rf %PATH%")
        with mock.patch("PyQt6.QtWidgets.QMessageBox.warning",
                        return_value=__import__("PyQt6.QtWidgets",
                                                fromlist=["QMessageBox"])
                        .QMessageBox.StandardButton.No):
            self.win.settings_panel.wiring.save_post_clone_hook()
        self.assertEqual(self.win.settings.post_clone_hook, "")
        self.assertEqual(edit.text(), "", "被拒绝后输入框必须回滚到旧值")
        self.assertEqual(self.reload().load().post_clone_hook, "")

    def test_post_clone_hook_saves_after_confirm(self):
        edit = self.win.edit_post_hook
        edit.setText("npm install")
        with mock.patch("PyQt6.QtWidgets.QMessageBox.warning",
                        return_value=__import__("PyQt6.QtWidgets",
                                                fromlist=["QMessageBox"])
                        .QMessageBox.StandardButton.Yes):
            self.win.settings_panel.wiring.save_post_clone_hook()
        self.assertEqual(self.win.settings.post_clone_hook, "npm install")
        self.assertEqual(self.reload().load().post_clone_hook, "npm install")

    def test_post_clone_hook_clearing_needs_no_confirm(self):
        """清空是"变安全"的方向，不该再弹确认（否则用户被自己的安全操作烦到）。"""
        self.win.settings.post_clone_hook = "npm install"
        self.win.edit_post_hook.setText("")
        with mock.patch("PyQt6.QtWidgets.QMessageBox.warning") as warn:
            self.win.settings_panel.wiring.save_post_clone_hook()
        warn.assert_not_called()
        self.assertEqual(self.win.settings.post_clone_hook, "")


class TestQuickReposEditable(_Win):
    def test_parse_splits_and_dedupes(self):
        from gcm.ui.download_layout import parse_quick_repos
        got = parse_quick_repos("a/b, c/d;e/f，g/h\n a/b  ")
        self.assertEqual(got, ("a/b", "c/d", "e/f", "g/h"))

    def test_edit_rebuilds_buttons_and_persists(self):
        with mock.patch("PyQt6.QtWidgets.QInputDialog.getText",
                        return_value=("x/one, y/two", True)):
            n = self.win._edit_quick_repos()
        self.assertEqual(n, 2, "编辑后按钮数必须等于列表长度")
        self.assertEqual(len(self.win._quick_fill_buttons), 2)
        self.assertEqual(len(self.win.settings.quick_repos), 2)
        self.assertEqual(tuple(self.reload().load().quick_repos), ("x/one", "y/two"))

    def test_cancel_keeps_existing_buttons(self):
        before = len(self.win._quick_fill_buttons)
        with mock.patch("PyQt6.QtWidgets.QInputDialog.getText",
                        return_value=("zzz/zzz", False)):
            n = self.win._edit_quick_repos()
        self.assertEqual(n, before, "取消编辑不该改变按钮数")
        self.assertNotIn("zzz/zzz", tuple(self.win.settings.quick_repos))


class TestScanProgressDeterminate(_Win):
    def test_scan_progress_becomes_determinate(self):
        """`ScanWorker.progress` 此前只 emit 无人 connect，进度条永远 setRange(0,0)。"""
        dlg = LocalReposDialog(self.win, db=self.win.db)
        try:
            dlg.progress.setRange(0, 0)
            dlg._on_scan_progress(50, 200)
            self.assertEqual(dlg.progress.maximum(), 200, "进度上限必须等于候选总数")
            self.assertEqual(dlg.progress.value(), 50)
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_scan_progress_unknown_total_stays_indeterminate(self):
        dlg = LocalReposDialog(self.win, db=self.win.db)
        try:
            dlg.progress.setRange(0, 10)
            dlg.progress.setValue(5)
            dlg._on_scan_progress(3, 0)
            self.assertEqual(dlg.progress.minimum(), 0)
            self.assertEqual(dlg.progress.maximum(), 0, "总数未知时应回到不确定态而不是假装有进度")
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_restart_scan_resets_bar(self):
        dlg = LocalReposDialog(self.win, db=self.win.db)
        try:
            dlg.progress.setRange(0, 100)
            dlg.progress.setValue(100)  # 上一次扫描残留满格
            dlg._set_scanning(True)
            self.assertEqual(dlg.progress.maximum(), 0, "新扫描开始必须清掉上一次的满格")
            self.assertEqual(dlg.progress.value(), 0)
        finally:
            dlg.close()
            dlg.deleteLater()


class TestUpdateShowsSha256(_Win):
    def test_prompt_contains_sha256(self):
        """`check_latest_with_sha` 此前零生产调用 → 用户无法在应用内核对哈希。"""
        sha = "a" * 64
        self.win._update_sha256 = sha
        captured = {}

        def _fake_question(parent, title, text, *a, **kw):
            captured["text"] = text
            from PyQt6.QtWidgets import QMessageBox
            return QMessageBox.StandardButton.No

        with mock.patch("PyQt6.QtWidgets.QMessageBox.question", _fake_question):
            self.win._on_update_checked(True, "9.9.9", "https://example.invalid/x", "")
        self.assertIn(sha, captured.get("text", ""), "更新提示必须展示发布方公布的 SHA256")
        self.assertIn("certutil", captured.get("text", ""), "应给出校验方法")
        self.assertEqual(self.win._update_sha256, "", "sha 必须一次性消费，不能污染下次提示")

    def test_prompt_without_sha_has_no_check_line(self):
        self.win._update_sha256 = ""
        captured = {}

        def _fake_question(parent, title, text, *a, **kw):
            captured["text"] = text
            from PyQt6.QtWidgets import QMessageBox
            return QMessageBox.StandardButton.No

        with mock.patch("PyQt6.QtWidgets.QMessageBox.question", _fake_question):
            self.win._on_update_checked(True, "9.9.9", "https://example.invalid/x", "")
        self.assertNotIn("SHA256", captured.get("text", ""), "没有哈希时不该显示空的校验行")


class TestManageStatsFollowModelSignals(_Win):
    def test_manage_stats_follow_model_signals(self):
        """`manage_model.rowsChanged/visChanged` 此前只 emit 不 connect（死信号）。"""
        panel = self.win.manage_panel
        self.assertGreater(panel.manage_model.receivers(
            panel.manage_model.rowsChanged), 0, "rowsChanged 必须有接收者")
        self.assertGreater(panel.manage_model.receivers(
            panel.manage_model.visChanged), 0, "visChanged 必须有接收者")

    def test_stats_text_updates_on_rows_change(self):
        panel = self.win.manage_panel
        panel._update_manage_stats()
        before = panel.manage_stats.text()
        panel.manage_model.rowsChanged.emit()
        self.assertEqual(panel.manage_stats.text(), before,
                         "无数据时统计文本应稳定可预期")
        self.assertIn("共", panel.manage_stats.text(), "统计行必须说明总条数")


class TestDataDirEntry(_Win):
    def test_private_flag_shown(self):
        """`is_data_dir_private` 此前零生产调用：用户看不到目录是否私有。"""
        text = self.win.lbl_data_dir.text()
        self.assertIn("数据目录", text)

    def test_migrate_refused_while_busy(self):
        """不可逆操作必须拒绝在任务运行时执行。"""
        self.win.busy = True
        with mock.patch("PyQt6.QtWidgets.QMessageBox.warning") as warn, \
                mock.patch("gcm.db.settings.migrate_data_dir") as mig:
            self.win.settings_panel.wiring.migrate_data_dir()
        mig.assert_not_called()
        warn.assert_called_once()

    def test_migrate_refuses_without_private_target(self):
        """目标目录算不出来时必须拒绝，而不是瞎猜一个位置搬数据（CI 的 POSIX 覆盖点）。"""
        self.win.busy = False
        with mock.patch("gcm.ui.settings_actions._private_target_dir",
                        return_value=""), \
                mock.patch("PyQt6.QtWidgets.QMessageBox.warning") as warn, \
                mock.patch("gcm.db.settings.migrate_data_dir") as mig:
            self.win.settings_panel.wiring.migrate_data_dir()
        mig.assert_not_called()
        warn.assert_called_once()

    def test_migrate_calls_existing_helper_after_confirm(self):
        """`migrate_data_dir` 此前零生产调用；确认后必须调用既有实现，不另写一套。

        注意：`warning` 也要 patch —— 目标目录算不出来时走 warning 分支，
        离屏下真模态会把用例挂死（这正是 CI 上 macos/ubuntu 红过一次的原因：
        APPDATA 在 POSIX 不存在，早期实现直接放弃迁移）。
        """
        self.win.busy = False
        with mock.patch("PyQt6.QtWidgets.QMessageBox.question",
                        return_value=__import__("PyQt6.QtWidgets",
                                                fromlist=["QMessageBox"])
                        .QMessageBox.StandardButton.Yes), \
                mock.patch("PyQt6.QtWidgets.QMessageBox.warning") as warn, \
                mock.patch("PyQt6.QtWidgets.QMessageBox.information") as info, \
                mock.patch("gcm.db.settings.migrate_data_dir",
                           return_value=(True, "已迁移 3 项")) as mig:
            self.win.settings_panel.wiring.migrate_data_dir()
        self.assertEqual(mig.call_count, 1, "必须调用既有 migrate_data_dir")
        warn.assert_not_called()
        info.assert_called()
        src, dst = mig.call_args[0]
        self.assertEqual(str(src), str(self.win.data_dir))
        self.assertTrue(str(dst).endswith("Git-clone-Max"))


class TestDataDirMigrationRealFs(_Win):
    """真的搬文件（不 mock migrate_data_dir）：这是「不可逆操作」的正向证据。

    这条用例的价值：其它用例只断言「调用了既有实现」，一旦 `migrate_data_dir`
    本身有 bug（例如用 fix 覆盖、搬完删源、把 sqlite 搬坏）它们全绿。这里用临时目录
    真的搬一次，验证 ①文件到位 ②内容不变 ③源目录保留 ④同名不覆盖。
    """

    def test_real_migration_moves_files_without_losing_them(self):
        import sqlite3

        from gcm.db.settings import migrate_data_dir

        src = self.dir / "public_data"
        dst = self.dir / "private_data"
        src.mkdir()
        (src / "settings.json").write_text('{"theme": "deep"}', encoding="utf-8")
        (src / "app.log").write_text("line1" + chr(10) + "line2", encoding="utf-8")
        con = sqlite3.connect(src / "repos.db")
        con.execute("CREATE TABLE repos(id INTEGER PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO repos(name) VALUES ('octocat/Hello-World')")
        con.commit()
        con.close()

        # 目标里放一个同名文件：必须**不被覆盖**（migrate_data_dir 的既有语义）
        dst.mkdir()
        (dst / "app.log").write_text("KEEP-ME", encoding="utf-8")

        ok, msg = migrate_data_dir(str(src), str(dst))
        self.assertTrue(ok, f"迁移应成功，实际：{msg}")
        self.assertTrue((dst / "settings.json").exists(), "settings.json 必须搬到目标")
        self.assertEqual((dst / "app.log").read_text(encoding="utf-8"), "KEEP-ME",
                         "已存在的同名文件不得被覆盖")
        self.assertTrue((src / "app.log").exists(), "被跳过的文件应留在源目录")
        con = sqlite3.connect(dst / "repos.db")
        rows = con.execute("SELECT name FROM repos").fetchall()
        con.close()
        self.assertEqual(rows, [("octocat/Hello-World",)], "sqlite 内容必须完好")

    def test_flow_reports_success_and_never_deletes_source(self):
        """端到端：走 wiring.migrate_data_dir → 确认 → 真搬 → 提示成功。"""
        src = self.dir / "public2"
        dst = self.dir / "private2"
        src.mkdir()
        (src / "settings.json").write_text('{"theme": "light"}', encoding="utf-8")
        self.win.data_dir = src
        self.win.busy = False
        with mock.patch("gcm.ui.settings_actions._private_target_dir",
                        return_value=str(dst)),                 mock.patch("PyQt6.QtWidgets.QMessageBox.question",
                           return_value=__import__("PyQt6.QtWidgets",
                                                   fromlist=["QMessageBox"])
                           .QMessageBox.StandardButton.Yes),                 mock.patch("PyQt6.QtWidgets.QMessageBox.warning") as warn,                 mock.patch("PyQt6.QtWidgets.QMessageBox.information") as info:
            self.win.settings_panel.wiring.migrate_data_dir()
        warn.assert_not_called()
        info.assert_called_once()
        self.assertTrue((dst / "settings.json").exists())
        self.assertTrue(src.exists(), "源目录必须保留（失败可回退）")


if __name__ == "__main__":
    unittest.main()
