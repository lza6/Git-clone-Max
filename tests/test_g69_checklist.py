# -*- coding: utf-8 -*-
"""G54-4 首次使用引导清单：状态推导 + 打勾持久化 + UI 不阻塞。

设计要点（也是本文件的验收点）：
- 能推导的项（下载目录 / 令牌 / 已入库仓库）**实时读**现有设置与数据库，
  不重复存状态；只有 network / help 两项记在 `settings.checklist_done`。
- 清单是提示不是门禁：不可见窗口时不得弹模态（否则永久阻塞进程）。
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

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.db.settings import Settings  # noqa: E402
from gcm.ui.checklist import ITEMS, ChecklistDialog, compute_status  # noqa: E402


class TestComputeStatus(unittest.TestCase):
    def test_empty_settings_reports_nothing_done(self):
        st = compute_status(Settings(), repos_count=0)
        self.assertFalse(any(st.values()), f"全新安装不应有已完成项：{st}")

    def test_derived_items_follow_real_state(self):
        s = Settings()
        s.download_dir = "D:/repos"
        s.token = "ghp_x"
        got = compute_status(s, repos_count=3)
        self.assertTrue(got["download_dir"])
        self.assertTrue(got["token"])
        self.assertTrue(got["import"], "库里有仓库即视为导入完成")
        self.assertFalse(got["network"], "network 不可推导，需显式打勾")
        self.assertFalse(got["help"])

    def test_explicit_flags(self):
        s = Settings()
        s.checklist_done = {"network": True, "help": False}
        got = compute_status(s, repos_count=0)
        self.assertTrue(got["network"])
        self.assertFalse(got["help"])

    def test_covers_every_declared_item(self):
        st = compute_status(Settings(), 0)
        self.assertEqual(set(st), {k for k, _t, _d, _a in ITEMS})


class TestDialog(unittest.TestCase):
    def test_dialog_shows_progress_count(self):
        st = {k: False for k, *_ in ITEMS}
        st["download_dir"] = True
        st["token"] = True
        d = ChecklistDialog(st)
        self.addCleanup(d.close)
        from PyQt6.QtWidgets import QCheckBox, QLabel
        joined = " ".join(lbl.text() for lbl in d.findChildren(QLabel))
        titles = " ".join(cb.text() for cb in d.findChildren(QCheckBox))
        self.assertIn("2/5", joined, f"应显示 2/5，实际：{joined[:120]}")
        self.assertIn(ITEMS[0][1], titles, "每一项的标题应出现在复选框上")
        # 已完成的项应被勾上（状态是回填的，不允许在此修改）
        checked = {cb.text() for cb in d.findChildren(QCheckBox) if cb.isChecked()}
        self.assertEqual(len(checked), 2)


class TestMainWindowIntegration(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_checklist_"))
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        self.store = SettingsStore(self.tmp / "s.json")
        self.win = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                              settings=self.store)
        self.win._check_update_silent = lambda *a, **k: None
        self.win._maybe_show_onboarding = lambda *a, **k: None
        self.addCleanup(self.win.close)

    def test_mark_checklist_persists(self):
        self.assertFalse(self.win._checklist_status()["network"])
        self.win._mark_checklist("network")
        self.assertTrue(self.win._checklist_status()["network"])
        # 落盘后重新读回仍然为真
        self.assertTrue(self.store.load().checklist_done.get("network"))

    def test_mark_is_idempotent(self):
        self.win._mark_checklist("help")
        self.win._mark_checklist("help")
        self.assertTrue(self.win._checklist_status()["help"])

    def test_show_checklist_does_not_block_when_hidden(self):
        """不可见窗口下 `_show_checklist()` 必须立即返回（不弹模态）。"""
        self.win.hide()
        _app.processEvents()
        self.win._show_checklist()      # 不得阻塞
        self.assertTrue(True)


class TestChecklistPromptsOnce(unittest.TestCase):
    """G69-18 审查修复：清单**只在首次**自动提示。

    原实现没有「已提示」判断 —— 每次启动 600ms 后都弹一个模态清单挡住用户。
    """

    def setUp(self):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        import tempfile
        from pathlib import Path as _P

        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        self.tmp = _P(tempfile.mkdtemp(prefix="gcm_prompt_"))
        self.store = SettingsStore(self.tmp / "s.json")
        self.win = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                              settings=self.store)
        self.win._check_update_silent = lambda *a, **k: None
        self.win._maybe_show_onboarding = lambda *a, **k: None
        self.addCleanup(self.win.close)

    def test_second_prompt_is_suppressed(self):
        """模拟「可见窗口」环境，验证**只提示一次**的判定逻辑。

        `_maybe_show_checklist` 在 offscreen 下会主动跳过（避免测试里弹模态），
        所以这里显式把平台环境与 `_can_show_modal()` 都置为「可见」，
        测的是真正的「是否已提示过」分支，而不是那个环境守卫。
        """
        from unittest import mock

        shown = []
        self.win._show_checklist = lambda: shown.append(1)
        self.win._can_show_modal = lambda: True
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "windows"}):
            self.win._maybe_show_checklist()
            self.assertEqual(len(shown), 1, "首次应当提示")
            self.assertTrue(self.store.load().checklist_prompted, "提示后应持久化标记")
            # 第二次：不应再提示
            self.win._maybe_show_checklist()
            self.assertEqual(len(shown), 1, "清单每次启动都弹 = 打扰用户")

    def test_offscreen_never_prompts(self):
        """offscreen（CI/测试）下永不弹模态，也不写标记。"""
        shown = []
        self.win._show_checklist = lambda: shown.append(1)
        self.win._maybe_show_checklist()
        self.assertEqual(shown, [])
        self.assertFalse(self.store.load().checklist_prompted)

    def test_import_item_reflects_real_db_rows(self):
        """G69-18 审查修复：`import` 项必须真的读数据库。

        原实现调 `db.count_repos()` —— Database 没有这个方法，
        `hasattr` 把拼错静默降级成 0，于是导入 1000 个仓库后该项仍然未完成。
        """
        from gcm.models import RepoSpec

        self.assertFalse(self.win._checklist_status()["import"])
        self.win.db.upsert_repo(
            RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b.git",
                     folder_name="a__b"),
            local_path=str(self.tmp / "a__b"), host="github.com")
        self.assertTrue(self.win._checklist_status()["import"],
                        "有仓库后「导入仓库清单」应为已完成")


if __name__ == "__main__":
    unittest.main(verbosity=2)
