# -*- coding: utf-8 -*-
"""G21-3 更新检查单次重试 + 静默失败留痕。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app import applog, updater

_RELEASE = '{"tag_name": "v999.0.0", "assets": [], "html_url": "https://x"}'


class TestUpdaterRetry(unittest.TestCase):
    def test_retry_once_after_transient_failure(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("500 transient")
            return _RELEASE

        with patch("gcm.app.updater.time.sleep") as ms:
            has_new, ver, url, err = updater.check_latest(fetcher=flaky)
        self.assertEqual(calls["n"], 2, "应重试一次")
        ms.assert_called_once()
        self.assertTrue(has_new)
        self.assertEqual(ver, "v999.0.0")
        self.assertEqual(err, "")

    def test_double_failure_reports_both(self):
        def bad():
            raise RuntimeError("down")

        with patch("gcm.app.updater.time.sleep"):
            has_new, ver, url, err = updater.check_latest(fetcher=bad)
        self.assertFalse(has_new)
        self.assertIn("检查更新失败", err)
        self.assertIn("首次", err)

    def test_silent_check_failure_logged(self):
        """_check_update_silent 失败必须写 app.log（G21-3 留痕）。"""
        from gcm.ui.main_window import MainWindow
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        applog.reset_for_tests()
        d = Path(tempfile.mkdtemp())
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        try:
            with patch("gcm.app.updater.check_latest",
                       return_value=(False, "", "", "检查更新失败：net down")), \
                 patch("gcm.app.applog.warn") as mock_warn:
                win._check_update_silent()
            mock_warn.assert_called_once()
            self.assertIn("check_update", str(mock_warn.call_args))
        finally:
            win.close()
            applog.reset_for_tests()


if __name__ == "__main__":
    unittest.main(verbosity=2)
