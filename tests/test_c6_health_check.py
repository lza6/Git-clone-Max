"""Rev8 C6-2 守卫：仓库详情「健康体检」（git fsck，用户显式点击才跑）。

三条不变量：
1. 详情对话框有「健康体检」按钮，点击后后台线程跑
   `git fsck --connectivity-only` + `git count-objects -vH`；
2. 结果回主线程更新（跨线程不直改 widget —— 与 check_remote 同范式）；
3. **不进自动批次**：fsck 只由用户点击触发（AST 断言引擎/扫描器不引用 fsck）。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


def _make_repo(td: Path) -> Path:
    src = td / "repo"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    (src / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "i"], check=True)
    return src


class TestHealthCheckButton(unittest.TestCase):
    def _dialog(self, repo_path: Path):
        from gcm.ui.repo_detail_dialog import RepoDetailDialog
        repo = {"local_path": str(repo_path), "owner": "o", "repo": "r",
                "url": "", "head_sha": ""}
        dlg = RepoDetailDialog(repo, [], parent=None)
        return dlg

    def test_button_exists_and_wired(self):
        with tempfile.TemporaryDirectory() as td:
            dlg = self._dialog(_make_repo(Path(td)))
            self.assertTrue(hasattr(dlg, "btn_health"))
            self.assertTrue(hasattr(dlg, "run_health_check"))

    def test_health_check_reports_ok(self):
        with tempfile.TemporaryDirectory() as td:
            dlg = self._dialog(_make_repo(Path(td)))
            # 直接跑同步计算函数（工作线程内的逻辑），不起线程
            text = dlg._compute_health_text()
            self.assertIn("健康", text)

    def test_health_check_reports_broken(self):
        """破坏 commit 对象 → fsck 报错（真实坏仓检测）。

        必须破坏 **commit/tree** 而不是 blob：`fsck --connectivity-only`
        把 blob 当不透明对象跳过解析（新版 git 在 ubuntu CI 上因此测不出
        blob 损坏，历史偶发假绿）；commit 任何版本都会被解析。
        """
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))
            import stat
            import subprocess as _sp
            corrupted = False
            for obj in (repo / ".git/objects").glob("[0-9a-f][0-9a-f]/*"):
                oid = obj.parent.name + obj.name
                t = _sp.run(["git", "-C", str(repo), "cat-file", "-t", oid],
                            capture_output=True, text=True).stdout.strip()
                if t == "commit":
                    obj.chmod(stat.S_IWRITE | stat.S_IREAD)
                    obj.write_bytes(b"corrupted")
                    corrupted = True
                    break
            self.assertTrue(corrupted, "测试仓里必须存在 commit 对象")
            dlg = self._dialog(repo)
            text = dlg._compute_health_text()
            self.assertIn("异常", text)

    def test_no_auto_fsck_in_engine(self):
        """fsck 不进自动批次：引擎/扫描器源码不引用 fsck。"""
        for mod in ("gcm/app/engine.py", "gcm/app/scanner.py"):
            src = (ROOT / mod).read_text(encoding="utf-8")
            self.assertNotIn("fsck", src, f"{mod} 出现 fsck —— 体检必须是显式动作")


if __name__ == "__main__":
    unittest.main()
