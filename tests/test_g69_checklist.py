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


if __name__ == "__main__":
    unittest.main(verbosity=2)
