"""G54 小白体验测试：环境体检 / 排错地图 / 统计中心 G53 集成。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gcm.app.diag import (
    copy_paste_report, probe_data_dir, probe_disk, probe_git, probe_python,
    run_env_diagnostics,
)
from gcm.ui.help_dialog import TROUBLESHOOT_SECTIONS, help_html


class TestDiagEnv(unittest.TestCase):
    def test_probe_data_dir_writable(self):
        with tempfile.TemporaryDirectory() as td:
            r = probe_data_dir(Path(td))
            self.assertEqual(r["status"], "ok")

    def test_probe_data_dir_unwritable(self):
        with mock.patch("pathlib.Path.write_text", side_effect=PermissionError("denied")):
            r = probe_data_dir(Path(tempfile.mkdtemp()))
            self.assertEqual(r["status"], "err")

    def test_probe_disk(self):
        with tempfile.TemporaryDirectory() as td:
            r = probe_disk(Path(td), min_free_gb=0.0001)
            self.assertIn(r["status"], ("ok", "warn"))

    def test_probe_python(self):
        r = probe_python()
        self.assertEqual(r["status"], "ok")
        self.assertIn("Python", r["detail"])

    def test_run_env_diagnostics_includes_env(self):
        with tempfile.TemporaryDirectory() as td:
            reports = run_env_diagnostics(Path(td))
            names = [r["name"] for r in reports]
            self.assertIn("数据目录", names)
            self.assertIn("磁盘空间", names)
            self.assertIn("运行环境", names)

    def test_copy_paste_report_redacts(self):
        reports = [{"name": "git", "status": "ok", "detail": "https://user:pass@x/y"}]
        text = copy_paste_report(reports, app_version="8.1.0")
        self.assertIn("Git-clone-Max 诊断", text)
        self.assertNotIn("user:pass", text)


class TestHelpTroubleshoot(unittest.TestCase):
    def test_sections_exist(self):
        self.assertGreaterEqual(len(TROUBLESHOOT_SECTIONS), 4)
        titles = [s[0] for s in TROUBLESHOOT_SECTIONS]
        self.assertIn("认证失败 / token 无效", titles)

    def test_html_contains_troubleshoot(self):
        html = help_html()
        self.assertIn("排错地图", html)
        self.assertIn("认证失败", html)


if __name__ == "__main__":
    unittest.main()
