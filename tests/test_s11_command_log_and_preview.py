"""Rev10 G-01/G-02（git 命令环形缓冲 + 查看入口）与 P-02（预览差异表）守卫。"""
import tempfile
import unittest
from pathlib import Path

from gcm.app import git_command_log as gcl


class TestGitCommandLog(unittest.TestCase):
    """G-01：环形缓冲。"""

    def setUp(self):
        gcl.clear_for_tests()
        self.addCleanup(gcl.clear_for_tests)

    def test_record_and_recent(self):
        gcl.record("o__r", ["git", "clone", "https://github.com/o/r"], 0,
                   1200, ["Cloning into...", "done."])
        recs = gcl.recent()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].folder_name, "o__r")
        self.assertEqual(recs[0].returncode, 0)
        self.assertEqual(recs[0].tail[-1], "done.")

    def test_tail_truncated_to_50(self):
        gcl.record("o__r", ["git", "fetch"], 0, 10,
                   [f"line{i}" for i in range(120)])
        self.assertEqual(len(gcl.recent()[0].tail), 50)
        self.assertEqual(gcl.recent()[0].tail[-1], "line119")

    def test_ring_capacity(self):
        for i in range(600):
            gcl.record(f"r{i}", ["git", "status"], 0, 1, [])
        self.assertEqual(len(gcl.recent(1000)), 500, "容量上限 500")
        self.assertEqual(gcl.recent(1)[0].folder_name, "r599", "新的在前")

    def test_for_repo_filter(self):
        gcl.record("a", ["git", "clone"], 0, 1, [])
        gcl.record("b", ["git", "clone"], 1, 1, [])
        self.assertEqual([r.folder_name for r in gcl.for_repo("a")], ["a"])

    def test_redaction_at_boundary(self):
        """URL 内嵌凭据必须打码（G-02 面向 UI 回看，等同出站）。"""
        gcl.record("o__r", ["git", "clone",
                            "https://user:secret@github.com/o/r"],
                   0, 1, ["https://user:secret@github.com/o/r"])
        rec = gcl.recent()[0]
        self.assertNotIn("secret", rec.argv)
        self.assertNotIn("secret", " ".join(rec.tail))

    def test_record_never_raises(self):
        gcl.record(None, None, "x", None, None)  # 垃圾输入
        self.assertEqual(len(gcl.recent()), 0, "观测失败静默，不影响主流程")


class TestRunGitUiHook(unittest.TestCase):
    """G-01 接线：run_git_ui 结束后命令必须进缓冲。"""

    def setUp(self):
        gcl.clear_for_tests()
        self.addCleanup(gcl.clear_for_tests)

    def test_real_command_recorded(self):
        from gcm.git.service import run_git_ui
        tmp = Path(tempfile.mkdtemp(prefix="g01_"))
        self.addCleanup(self._rm, tmp)
        rc, _ = run_git_ui(["git", "status"], str(tmp), lambda c: None)
        recs = gcl.recent()
        self.assertEqual(len(recs), 1, "run_git_ui 结束必须记录一条")
        self.assertEqual(recs[0].returncode, rc)
        self.assertIn("git status", recs[0].argv)

    @staticmethod
    def _rm(p: Path):
        import shutil
        shutil.rmtree(p, ignore_errors=True)


class TestPreviewDialog(unittest.TestCase):
    """P-02：预览差异表与 CLI/MCP 同源（plan_batch）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_rows_match_plan_batch(self):
        from gcm.app.batch_plan import plan_batch
        from gcm.ui.batch_preview_dialog import BatchPreviewDialog
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            QApplication([])
        text = ("https://github.com/octocat/Hello-World\n"
                "https://github.com/octocat/Hello-World\nnot a url")
        plan = plan_batch(text, "C:/tmp/preview")
        dlg = BatchPreviewDialog(text, "C:/tmp/preview")
        self.addCleanup(dlg.close)
        self.assertEqual(dlg.table.rowCount(), len(plan.rows))
        # 重复 + 无效行必须给警告色动作（Rev17 T3 后动作列从 0 移到 1，第 0 列是勾选列）
        actions = [dlg.table.item(i, 1).text() for i in range(dlg.table.rowCount())]
        self.assertIn("重复", actions)
        self.assertIn("无效", actions)


if __name__ == "__main__":
    unittest.main()
