# -*- coding: utf-8 -*-
"""G69-20：盲点扫描抓到的「定义了但用户不可达」接线缺口。

每条用例对应一个真实缺陷（修复前均无生产调用点）：
- G03-1 管理页搜索：`filter_rows` 写了 `_vis`，但**没有任何控件**，视图也从不消费；
- G03-6 过期高亮：`is_stale` 定义了，`data()` 却没有颜色角色；
- 日志关键字高亮：`theme.make_highlighter` 从未安装到日志视图；
- 凭据存储强度提示：`crypto_warning()` 从未被调用；
- G03 收藏/黑名单：`set_favorite`/`set_excluded` 没有直接 UI 写入点；
- G35-10 Star 导入：`gh_api.fetch_starred` 无 UI 入口。
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _row(days_ago: int, name: str = "a__b") -> dict:
    ts = (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime(
        "%Y-%m-%d %H:%M:%S")
    return {
        "folder_name": name, "owner": name.split("__")[0], "repo": name.split("__")[-1],
        "local_path": f"D:/repos/{name}", "last_sync_at": ts, "head_sha": "abcdef12",
        "host": "github.com", "url": f"https://github.com/{name}", "id": 1,
        "tags": "core", "favorite": 0, "excluded": 0,
    }


class TestManageFilterIsConsumed(unittest.TestCase):
    """G03-1：过滤必须真的影响视图，而不只是写 `_vis`。"""

    def test_filter_marks_visibility(self):
        from gcm.ui.manage_model import ManageModel
        m = ManageModel([_row(1, "alpha__one"), _row(1, "beta__two")])
        self.assertEqual(m.filter_rows("alpha"), 1)
        self.assertTrue(m.is_visible(0))
        self.assertFalse(m.is_visible(1))
        # 清空过滤恢复全部
        self.assertEqual(m.filter_rows(""), 2)
        self.assertTrue(m.is_visible(1))

    def test_out_of_range_visible_is_false(self):
        from gcm.ui.manage_model import ManageModel
        m = ManageModel([])
        self.assertFalse(m.is_visible(0))


class TestStaleHighlightRole(unittest.TestCase):
    """G03-6：过期行必须有颜色角色（原先只有布尔函数）。"""

    def test_stale_row_has_background_role(self):
        from PyQt6.QtCore import Qt
        from gcm.ui.manage_model import ManageModel
        m = ManageModel([_row(60), _row(1, "x__y")])
        idx_stale = m.index(0, 0)
        idx_fresh = m.index(1, 0)
        self.assertIsNotNone(m.data(idx_stale, Qt.ItemDataRole.BackgroundRole))
        self.assertIsNone(m.data(idx_fresh, Qt.ItemDataRole.BackgroundRole))
        self.assertIn("未同步", m.data(idx_stale, Qt.ItemDataRole.ToolTipRole))

    def test_stale_days_follows_model_setting(self):
        from gcm.ui.manage_model import ManageModel
        m = ManageModel([_row(10)])
        self.assertFalse(m.is_stale(0))
        m.stale_days = 5
        self.assertTrue(m.is_stale(0))


class _WindowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_wiring_"))
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.models import RepoSpec
        from gcm.ui.main_window import MainWindow

        self.RepoSpec = RepoSpec
        self.store = SettingsStore(self.tmp / "s.json")
        self.win = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                              settings=self.store)
        self.win._check_update_silent = lambda *a, **k: None
        self.win._maybe_show_onboarding = lambda *a, **k: None
        self.win.manage_panel._start_size_loader = lambda: None
        self.addCleanup(self.win.close)

    def _add(self, owner: str, repo: str) -> None:
        self.win.db.upsert_repo(
            self.RepoSpec(owner=owner, repo=repo,
                          url_https=f"https://github.com/{owner}/{repo}.git",
                          folder_name=f"{owner}__{repo}"),
            local_path=str(self.tmp / f"{owner}__{repo}"), host="github.com")


class TestManagePanelSearch(_WindowCase):
    def test_search_box_exists_and_hides_rows(self):
        self._add("alpha", "one")
        self._add("beta", "two")
        self.win._load_db_into_grid()
        self.assertEqual(self.win.manage_model.rowCount(), 2)
        self.assertTrue(hasattr(self.win.manage_panel, "manage_search"),
                        "管理页必须有搜索框（此前完全没有控件）")
        self.win.manage_panel.manage_search.setText("alpha")
        hidden = [self.win.manage_table.isRowHidden(i) for i in range(2)]
        self.assertEqual(hidden.count(True), 1, f"应隐藏 1 行，实际 {hidden}")
        self.assertIn("筛选", self.win.manage_stats.text())

    def test_reload_keeps_active_filter(self):
        self._add("alpha", "one")
        self._add("beta", "two")
        self.win._load_db_into_grid()
        self.win.manage_panel.manage_search.setText("beta")
        self.win._load_db_into_grid()
        hidden = [self.win.manage_table.isRowHidden(i) for i in range(2)]
        self.assertEqual(hidden.count(True), 1,
                         "刷新列表后过滤条件应继续生效")


class TestFavoriteAndBlocklist(_WindowCase):
    def test_toggle_favorite_writes_db(self):
        self._add("alpha", "one")
        self.win._load_db_into_grid()
        self.win.manage_table.selectRow(0)
        n = self.win.manage_panel._toggle_flag("favorite")
        self.assertEqual(n, 1)
        rec = self.win.db.get_repo("alpha", "one", "github.com")
        self.assertEqual(rec["favorite"], 1)

    def test_toggle_without_selection_is_noop(self):
        self._add("gamma", "three")
        self.win._load_db_into_grid()
        self.win.manage_table.clearSelection()
        self.assertEqual(self.win.manage_panel._toggle_flag("favorite"), 0)

    def test_toggle_excluded_is_reversible(self):
        self._add("beta", "two")
        self.win._load_db_into_grid()
        self.win.manage_table.selectRow(0)
        self.win.manage_panel._toggle_flag("excluded")
        self.assertEqual(self.win.db.get_repo("beta", "two", "github.com")["excluded"], 1)
        self.win.manage_table.selectRow(0)
        self.win.manage_panel._toggle_flag("excluded")
        self.assertEqual(self.win.db.get_repo("beta", "two", "github.com")["excluded"], 0)


class TestLogHighlighterInstalled(_WindowCase):
    def test_highlighter_attached_to_document(self):
        hl = getattr(self.win, "log_highlighter", None)
        self.assertIsNotNone(hl, "关键字高亮器必须被安装（此前 make_highlighter 零调用）")
        self.assertIsNotNone(self.win.log_view.document())


class TestCryptoWarningLogged(unittest.TestCase):
    def test_warning_is_written_to_log(self):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        d = Path(tempfile.mkdtemp(prefix="gcm_crypto_"))
        with mock.patch("gcm.db.settings.crypto_warning",
                        return_value="仅可逆混淆，不等于加密"):
            w = MainWindow(data_dir=d, db=Database(d / "t.db"),
                           settings=SettingsStore(d / "s.json"))
        try:
            w._check_update_silent = lambda *a, **k: None
            texts = [e.text for e in w.log.snapshot()]
            self.assertTrue(any("凭据存储" in t for t in texts),
                            f"日志里应有凭据存储提示，实际：{texts[-5:]}")
        finally:
            w.close()


class TestWorkerLineAttribution(unittest.TestCase):
    """P2 修复：`CloneWorker.on_line` 必须真的被 service 使用。

    此前该参数只保存不使用，所有 git 输出都走引擎里固定 index=0 的 lambda。
    """

    def test_external_line_replaces_service_callback(self):
        from gcm.app.worker import CloneWorker, TaskPayload
        from gcm.models import RepoSpec, StreamChunk, SyncResult, SyncStatus

        class _StubSvc:
            def __init__(self):
                self.on_line = None
                self.fetch_depth = 0
                self.unshallow = False
                self.send_progress_detail = None
                self.seen_line = None

            def sync(self, spec):
                self.seen_line = self.on_line
                return SyncResult(spec=spec, status=SyncStatus.SUCCESS)

        svc = _StubSvc()
        seen = []
        spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b.git",
                        folder_name="a__b")
        w = CloneWorker(3, TaskPayload(spec=spec), svc, None, None,
                        on_line=lambda c: seen.append((3, c.text)))
        w.run()
        self.assertIsNotNone(svc.seen_line, "service.on_line 不应再是引擎固定 index=0 的回调")
        svc.seen_line(StreamChunk(3, "hello"))
        self.assertEqual(seen, [(3, "hello")])


class TestAlertScannerReconfigure(unittest.TestCase):
    """G69-20：设置页保存后必须能改到运行中的扫描器（原先构造期固化）。"""

    def test_reconfigure_updates_interval_and_im(self):
        from gcm.app.alerts import AlertScanner
        sc = AlertScanner(lambda: [], interval_sec=3600.0,
                          im_channel="", im_url="", im_token="")
        sc.reconfigure(interval_sec=120.0, im_channel="dingtalk",
                       im_url="https://example.test/hook", im_token="secret")
        self.assertEqual(sc._interval, 120.0)
        self.assertEqual(sc._im_channel, "dingtalk")
        self.assertEqual(sc._im_url, "https://example.test/hook")
        self.assertEqual(sc._im_token, "secret")


class TestSettingsCallbacks(_WindowCase):
    """设置页回调的真实调用（覆盖 9.4.7 新增的 `_apply_alert_config` 与既有保存链路）。"""

    def test_save_callbacks_persist(self):
        sp = self.win.settings_panel
        sp._save_g53_alerts(False)
        sp._save_g53_interval(30)
        sp._save_g53_history(90)
        sp._save_g53_sched(False)
        sp._save_g53_sched_interval(120)
        sp._save_g53_sched_dir()
        sp._save_retry_enabled(True)
        sp._save_retry_max(3)
        sp._save_watchdog_enabled(False)
        sp._save_watchdog_interval(5)
        sp._save_alert_im()
        sp._save_g53_stale_days(30)
        sp._save_g53_disk_min(5)
        sp._save_concurrency(4)
        sp._save_fetch_timeout(300)
        sp._save_retries(2)
        sp._save_proxy()
        sp._save_rate_limit(0)
        sp._save_host_tokens()
        sp._save_mirror()
        sp._save_custom_hosts()
        sp._save_ssh_key()
        sp._save_auto_clear(True)
        sp._save_fetch_unshallow(False)
        sp._save_submodule(False)
        sp._save_clipboard_watch(False)
        sp._save_finish_sound(False)
        sp._save_animations(False)
        sp._save_auto_update(0)
        sp._save_reduced_motion(False)
        sp._save_lfs(False)
        sp._save_start_minimized(False)
        sp._save_notify_enable(False)
        sp._save_notify_webhook()
        sp._save_token()
        sp._save_auth_header_type(1)
        sp._save_auth_header_type(0)
        sp._save_accent_preset(0)
        sp._save_language(0)
        sp._save_theme(0)
        saved = self.store.load()
        self.assertEqual(saved.g53_alerts_interval_min, 30)
        self.assertEqual(saved.retry_max, 3)
        self.assertEqual(saved.concurrency, 4)
        self.assertEqual(saved.g53_alerts_stale_days, 30)

    def test_detect_proxy_both_branches(self):
        from gcm.app import proxy as _proxy
        sp = self.win.settings_panel
        with mock.patch.object(_proxy, "detect_system_proxy", return_value=""):
            sp._detect_proxy_now()
        with mock.patch.object(_proxy, "detect_system_proxy",
                               return_value="http://127.0.0.1:7890"):
            sp._detect_proxy_now()
        self.assertEqual(self.win.settings.proxy, "http://127.0.0.1:7890")

    def test_apply_alert_config_swallows_scanner_error(self):
        class _Boom:
            def reconfigure(self, **kw):
                raise RuntimeError("boom")

        self.win.alert_scanner = _Boom()
        self.win.settings_panel._apply_alert_config()   # 不得抛出

    def test_apply_alert_config_noop_without_scanner(self):
        self.win.alert_scanner = None
        self.win.settings_panel._apply_alert_config()


class TestStarImportThread(_WindowCase):
    def test_import_runs_and_fills_input(self):
        from PyQt6.QtWidgets import QApplication as _QApp
        from gcm.app import gh_api

        with mock.patch.object(gh_api, "fetch_starred",
                               return_value=(["octo/one", "octo/two"], "")):
            with mock.patch("gcm.ui.main_window.QInputDialog.getText",
                            return_value=("octocat", True)):
                self.win.import_starred()
                self.assertTrue(self.win._star_busy)
                # 等待 worker 线程 emit（最多 5s）
                for _ in range(100):
                    _QApp.processEvents()
                    if not self.win._star_busy:
                        break
                    import time as _t
                    _t.sleep(0.05)
        self.assertFalse(self.win._star_busy, "worker 结束后必须复位忙碌标志")
        text = self.win.repo_input.toPlainText()
        self.assertIn("octo/one", text)
        self.assertIn("octo/two", text)

    def test_busy_guard_short_circuits(self):
        self.win._star_busy = True
        with mock.patch("gcm.ui.main_window.QInputDialog.getText") as dlg:
            self.win.import_starred()
            dlg.assert_not_called()


class TestWorkerRebuildKeepsConfig(unittest.TestCase):
    """G69-21：depth 不一致触发 GitService 重建时，不得丢掉凭据/LFS/镜像等配置。"""

    def test_rebuild_carries_credentials_and_mirror(self):
        from gcm.app.worker import CloneWorker, TaskPayload
        from gcm.models import RepoSpec, SyncResult, SyncStatus

        class _StubSvc:
            def __init__(self):
                self.root = "."
                self.on_line = None
                self.cancelled = lambda: False
                self.fetch_timeout = 300
                self.clone_timeout = 600
                self.retries = 2
                self._backoff = (1.0,)
                self.proxy = ""
                self.fetch_depth = 0
                self.unshallow = False
                self.token = "ghp_secret"
                self.host_tokens = {"gitlab.com": "glpat"}
                self.host_auth_types = {"*": "bearer"}
                self._ssh_key = "id"
                self._lfs_enabled = True
                self._submodule = True
                self._post_clone_hook = "echo hi"
                self.rate_limit_kbps = 100
                self.force_ipv4 = True
                self.precheck_remote = True
                self.single_branch = True
                self._mirror = False
                self.mirror_prefix = {"github.com": ("https://m",)}
                self.mirror_use_presets = True
                self.mirror_fallback = True
                self.mirror_probe_timeout = 8.0

            def sync(self, spec):
                return SyncResult(spec=spec, status=SyncStatus.SUCCESS)

        captured = {}

        class _CaptureSvc(_StubSvc):
            def __init__(self, root, **kw):
                super().__init__()
                captured.update(kw)
                self.root = root

        spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b.git",
                        folder_name="a__b")
        with mock.patch("gcm.app.worker.GitService", _CaptureSvc):
            w = CloneWorker(0, TaskPayload(spec=spec), _StubSvc(), None, None,
                            fetch_depth=3)
            w.run()
        self.assertEqual(captured.get("token"), "ghp_secret")
        self.assertEqual(captured.get("host_tokens"), {"gitlab.com": "glpat"})
        self.assertEqual(captured.get("mirror_prefix"), {"github.com": ("https://m",)})
        self.assertTrue(captured.get("lfs_enabled"))
        self.assertEqual(captured.get("rate_limit_kbps"), 100)


class TestStarredImport(_WindowCase):
    def test_dedup_and_append(self):
        self.win.repo_input.setPlainText("https://github.com/a/b")
        self.win._on_starred_fetched(["a/b", "c/d", "c/d"], "")
        text = self.win.repo_input.toPlainText()
        self.assertEqual(text.count("c/d"), 1, "重复项只应写入一次")
        self.assertTrue(text.startswith("https://github.com/a/b"))

    def test_dedup_normalizes_url_forms(self):
        """输入区已有完整 URL 时，Star 返回的 `owner/repo` 不应重复追加。"""
        self.win.repo_input.setPlainText("https://github.com/a/b")
        self.win._on_starred_fetched(["a/b", "A/B.git"], "")
        self.assertEqual(self.win.repo_input.toPlainText().strip(),
                         "https://github.com/a/b")

    def test_error_is_logged_and_no_crash(self):
        self.win._on_starred_fetched([], "HTTP 403")
        texts = [e.text for e in self.win.log.snapshot()]
        self.assertTrue(any("Star 列表获取失败" in t for t in texts))


if __name__ == "__main__":
    unittest.main(verbosity=2)
