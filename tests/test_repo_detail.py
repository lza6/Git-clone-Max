# -*- coding: utf-8 -*-
"""RepoDetailDialog 离屏单元测试。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox

from gcm.ui.repo_detail_dialog import RepoDetailDialog

_app = QApplication.instance() or QApplication(sys.argv)


def _repo(tmp_dir: Path) -> dict:
    return {
        "owner": "octocat",
        "repo": "hello-world",
        "folder_name": "octocat__hello-world",
        "local_path": str(tmp_dir),
        "head_sha": "0123456789abcdef",
        "last_sync_at": "2026-09-09 12:00:00",
        "host": "github.com",
        "url": "https://github.com/octocat/hello-world.git",
        "default_branch": "main",
    }


def _history() -> list:
    return [
        {
            "started_at": "2026-09-09 12:00:00",
            "action": "cloned",
            "status": "success",
            "commits": 1,
            "message": "首次克隆完成",
            "detail": "",
            "duration_ms": 1200,
        },
        {
            "started_at": "2026-09-09 13:00:00",
            "action": "updated",
            "status": "success",
            "commits": 3,
            "message": "增量更新",
            "detail": "",
            "duration_ms": 800,
        },
        {
            "started_at": "2026-09-09 14:00:00",
            "action": "failed",
            "status": "failed",
            "commits": 0,
            "message": "网络超时",
            "detail": "",
            "duration_ms": 500,
        },
    ]


class TestRepoDetailDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.dlg = RepoDetailDialog(_repo(cls.tmp), _history())
        cls.dlg.show()

    @classmethod
    def tearDownClass(cls):
        cls.dlg.close()

    def test_dialog_builds_with_rows(self):
        self.assertEqual(self.dlg.table.rowCount(), 3)
        self.assertIn("octocat", self.dlg.lbl_title.text())
        self.assertIn("hello-world", self.dlg.lbl_title.text())
        # 元信息区含 local_path 文本（QLabel 渲染）
        texts = [w.text() for w in self.dlg.findChildren(QLabel)]
        self.assertTrue(any(str(self.tmp) in t for t in texts), "元信息区应含 local_path")

    def test_copy_path_clipboard(self):
        self.dlg.btn_copy_path.click()
        self.assertEqual(QApplication.clipboard().text(), str(self.tmp))

    def test_open_dir_missing_warns(self):
        dlg = RepoDetailDialog(_repo(Path(tempfile.mkdtemp()) / "no_such_dir"), [])
        dlg.show()
        with mock.patch("os.startfile", create=True) as m_start, \
                mock.patch("gcm.ui.repo_detail_dialog.QMessageBox.warning") as m_warn:
            dlg.btn_open_dir.click()  # 不抛异常
        m_start.assert_not_called()
        m_warn.assert_called_once()
        dlg.close()

    def test_open_dir_exists_calls_startfile(self):
        dlg = RepoDetailDialog(_repo(self.tmp), [])
        dlg.show()
        if os.name == "nt":
            with mock.patch("os.startfile", create=True) as m_start:
                dlg.btn_open_dir.click()
            m_start.assert_called_once()
        else:
            with mock.patch("subprocess.Popen") as m_pop, \
                    mock.patch("shutil.which", return_value="/usr/bin/open"):
                dlg.btn_open_dir.click()
            m_pop.assert_called_once()
        dlg.close()

    # ------------------------------------------------------------ G35-5 复制克隆命令
    def test_copy_command_no_ref(self):
        """repo 带 url 且无 ref → 剪贴板 == git clone <url>。"""
        self.dlg.btn_copy_command.click()
        self.assertEqual(
            QApplication.clipboard().text(),
            "git clone https://github.com/octocat/hello-world.git",
        )

    def test_copy_command_with_ref(self):
        """repo 带 url 且 ref=v1.2.0 → git clone -b v1.2.0 <url>。"""
        dlg = RepoDetailDialog(_repo(self.tmp) | {"ref": "v1.2.0"}, [])
        dlg.show()
        dlg.btn_copy_command.click()
        self.assertEqual(
            QApplication.clipboard().text(),
            "git clone -b v1.2.0 https://github.com/octocat/hello-world.git",
        )
        dlg.close()

    def test_copy_command_empty_url_no_crash(self):
        """repo url 为空 → 按钮存在，命令尝试不崩。"""
        dlg = RepoDetailDialog(_repo(self.tmp) | {"url": ""}, [])
        dlg.show()
        dlg.btn_copy_command.click()  # 不抛异常
        self.assertIsNotNone(dlg.btn_copy_command)
        dlg.close()

    def test_copy_command_missing_ref_key(self):
        """repo dict 无 ref 键（向后兼容）→ 按无 ref 处理。"""
        dlg = RepoDetailDialog({"owner": "o", "repo": "r", "url": "https://github.com/o/r"}, [])
        dlg.show()
        dlg.btn_copy_command.click()
        self.assertEqual(
            QApplication.clipboard().text(),
            "git clone https://github.com/o/r",
        )
        dlg.close()

    # ------------------------------------------------------------ G37-2 检查远端
    @staticmethod
    def _remote_out(sha: str) -> subprocess.CompletedProcess:
        """构造 git ls-remote <url> HEAD 的 stdout。"""
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"{sha}\tHEAD\n", stderr="",
        )

    @staticmethod
    def _pump_until(cond, timeout_ms: int = 3000) -> bool:
        """轮询 Qt 事件循环直到条件成立（让 singleShot 回主线程执行）。"""
        from PyQt6.QtCore import QElapsedTimer

        t = QElapsedTimer()
        t.start()
        while t.elapsed() < timeout_ms:
            QApplication.processEvents()
            if cond():
                return True
        return False

    def test_check_remote_up_to_date(self):
        """remote HEAD == 本地 head_sha → lbl_remote 显示「已最新」。"""
        dlg = RepoDetailDialog(_repo(self.tmp), [])
        dlg.show()
        with mock.patch(
            "gcm.ui.repo_detail_dialog.subprocess.run",
            return_value=self._remote_out("0123456789abcdef"),
        ):
            dlg.btn_check_remote.click()
            self.assertTrue(
                self._pump_until(lambda: "已最新" in dlg.lbl_remote.text()),
                f"lbl_remote 应为已最新，实际: {dlg.lbl_remote.text()!r}",
            )
        dlg.close()

    def test_check_remote_behind(self):
        """remote HEAD != 本地 head_sha → lbl_remote 显示「远端有新提交」。"""
        dlg = RepoDetailDialog(_repo(self.tmp), [])
        dlg.show()
        with mock.patch(
            "gcm.ui.repo_detail_dialog.subprocess.run",
            return_value=self._remote_out("fedcba9876543210"),
        ):
            dlg.btn_check_remote.click()
            self.assertTrue(
                self._pump_until(lambda: "远端有新提交" in dlg.lbl_remote.text()),
                f"lbl_remote 应为远端有新提交，实际: {dlg.lbl_remote.text()!r}",
            )
        dlg.close()

    def test_check_remote_no_local_head(self):
        """本地 head_sha 为空 → lbl_remote 显示「无法比较」。"""
        dlg = RepoDetailDialog(_repo(self.tmp) | {"head_sha": ""}, [])
        dlg.show()
        with mock.patch(
            "gcm.ui.repo_detail_dialog.subprocess.run",
            return_value=self._remote_out("fedcba9876543210"),
        ):
            dlg.btn_check_remote.click()
            self.assertTrue(
                self._pump_until(lambda: "无法比较" in dlg.lbl_remote.text()),
                f"lbl_remote 应为无法比较，实际: {dlg.lbl_remote.text()!r}",
            )
        dlg.close()

    def test_check_remote_command_failure(self):
        """ls-remote 抛异常 → lbl_remote 显示「无法检查远端」。"""
        dlg = RepoDetailDialog(_repo(self.tmp), [])
        dlg.show()
        with mock.patch(
            "gcm.ui.repo_detail_dialog.subprocess.run", side_effect=OSError("boom"),
        ):
            dlg.btn_check_remote.click()
            self.assertTrue(
                self._pump_until(lambda: "无法检查远端" in dlg.lbl_remote.text()),
                f"lbl_remote 应为无法检查远端，实际: {dlg.lbl_remote.text()!r}",
            )
        dlg.close()

    def test_check_remote_no_url(self):
        """url 为空 → 同步直接显示「无法检查远端」，不起线程。"""
        dlg = RepoDetailDialog(_repo(self.tmp) | {"url": ""}, [])
        dlg.show()
        dlg.btn_check_remote.click()
        self.assertIn("无法检查远端", dlg.lbl_remote.text())
        dlg.close()

    def test_check_remote_non_blocking(self):
        """点击立即返回（异步）+ 完成后按钮恢复可用，不冻结 UI。"""
        dlg = RepoDetailDialog(_repo(self.tmp), [])
        dlg.show()
        with mock.patch(
            "gcm.ui.repo_detail_dialog.subprocess.run",
            return_value=self._remote_out("fedcba9876543210"),
        ):
            dlg.btn_check_remote.click()
            # 未 pump 事件循环前，后台线程尚未回主线程 → 标签停留在「检查中…」
            self.assertIn("检查中", dlg.lbl_remote.text())
            self.assertTrue(
                self._pump_until(lambda: dlg.btn_check_remote.isEnabled()),
                "检查完成后按钮应恢复可用",
            )
            self.assertIn("远端有新提交", dlg.lbl_remote.text())
        dlg.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
