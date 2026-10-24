"""G80-B4-2 守卫：下载后的安装包校验（sha256 与发布方公布值比对）。

为什么值得一条守卫：更新提示里一直让用户自己 `certutil -hashfile` 手抄比对，
对目标用户等于没做。这里锁住四件事：
1. 分块算法必须与整读一致（否则"文件一样却校验不过"）；
2. 四种结论必须区分（match / mismatch / no_baseline / unreadable），
   **没有基准值时绝不假装成功**；
3. UI 入口必须真的接线（按钮 → 转发方法 → 实现），不是只建了个按钮；
4. 最近一次发布方 sha 必须留存下来（否则校验入口永远拿不到基准值）。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


class TestHashFile(unittest.TestCase):
    def test_chunked_equals_whole_file(self):
        from gcm.util.hashfile import sha256_file

        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "blob.bin"
            payload = bytes(range(256)) * 500  # 128 KB
            f.write_bytes(payload)
            whole = hashlib.sha256(payload).hexdigest()
            self.assertEqual(sha256_file(f), whole)
            # 分块大小不同不得影响结果（这里刻意用 7 字节这种怪值）
            self.assertEqual(sha256_file(f, chunk_size=7), whole)

    def test_verify_match_and_mismatch(self):
        from gcm.util.hashfile import OUTCOME_MATCH, OUTCOME_MISMATCH, verify_by_sha256

        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "a.bin"
            f.write_bytes(b"hello")
            good = hashlib.sha256(b"hello").hexdigest()

            ok, actual, outcome = verify_by_sha256(f, good)
            self.assertTrue(ok)
            self.assertEqual(outcome, OUTCOME_MATCH)
            self.assertEqual(actual, good)

            ok2, _actual2, outcome2 = verify_by_sha256(f, "0" * 64)
            self.assertFalse(ok2)
            self.assertEqual(outcome2, OUTCOME_MISMATCH)

    def test_expected_value_is_normalised(self):
        """允许 `sha256:` 前缀、大写、首尾空格（发布页/Release body 里写法不统一）。"""
        from gcm.util.hashfile import OUTCOME_MATCH, verify_by_sha256

        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "a.bin"
            f.write_bytes(b"hello")
            good = hashlib.sha256(b"hello").hexdigest()
            ok, _actual, outcome = verify_by_sha256(f, "  SHA256:" + good.upper() + " ")
            self.assertTrue(ok)
            self.assertEqual(outcome, OUTCOME_MATCH)

    def test_no_baseline_is_explicit(self):
        from gcm.util.hashfile import OUTCOME_NO_BASELINE, verify_by_sha256

        ok, actual, outcome = verify_by_sha256("/whatever", "")
        self.assertFalse(ok)
        self.assertEqual(actual, "")
        self.assertEqual(outcome, OUTCOME_NO_BASELINE, "没有基准值必须明说，不能返回成功")

    def test_unreadable_file(self):
        from gcm.util.hashfile import OUTCOME_UNREADABLE, verify_by_sha256

        with tempfile.TemporaryDirectory() as td:
            ok, _actual, outcome = verify_by_sha256(Path(td) / "missing.bin", "a" * 64)
        self.assertFalse(ok)
        self.assertEqual(outcome, OUTCOME_UNREADABLE)


class TestVerifyFlowWiring(unittest.TestCase):
    """三段接线：设置页按钮 → MainWindow 转发 → update_flow 实现。"""

    def test_settings_button_exists_and_connected(self):
        src = Path("gcm/ui/settings_groups.py").read_text(encoding="utf-8")
        self.assertIn("btn_verify_file", src)
        self.assertIn("owner.verify_downloaded_file", src)

    def test_main_window_forwarder_exists(self):
        src = Path("gcm/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn("def verify_downloaded_file(self)", src)
        self.assertIn("from .update_flow import verify_downloaded_file", src)

    def test_last_release_sha_is_kept(self):
        """`on_checked` 必须把最近一次发布方 sha 留住（一次性属性仅供提示）。"""
        src = Path("gcm/ui/update_flow.py").read_text(encoding="utf-8")
        self.assertIn("win._last_release_sha = sha", src)

    def test_flow_uses_shared_hash_implementation(self):
        import inspect

        from gcm.ui import update_flow

        src = inspect.getsource(update_flow.verify_downloaded_file)
        self.assertIn("verify_by_sha256", src, "必须复用 util/hashfile 的分块实现")
        self.assertNotIn("hashlib.sha256(", src, "不得在 UI 里另写一份 sha256")


class TestVerifyFlowBehaviour(unittest.TestCase):
    """真调一次流程：给一段文本文件 + 正确 sha，必须弹「校验通过」。"""

    def _run(self, expected: str, path: str):
        from gcm.ui import update_flow

        class _Win:
            _last_release_sha = expected

        shown = []
        fake_mb = mock.Mock()
        fake_mb.information = lambda *a, **k: shown.append(("info", a[2] if len(a) > 2 else ""))
        fake_mb.warning = lambda *a, **k: shown.append(("warn", a[2] if len(a) > 2 else ""))

        with mock.patch("gcm.ui.update_flow._msgbox", return_value=fake_mb), \
                mock.patch("PyQt6.QtWidgets.QFileDialog.getOpenFileName",
                           return_value=(path, "")):
            update_flow.verify_downloaded_file(_Win())
        return shown

    def test_match_path(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "installer.exe"
            f.write_bytes(b"fake installer")
            good = hashlib.sha256(b"fake installer").hexdigest()
            shown = self._run(good, str(f))
        self.assertTrue(shown)
        self.assertEqual(shown[0][0], "info")
        self.assertIn("一致", shown[0][1])

    def test_mismatch_path_warns(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "installer.exe"
            f.write_bytes(b"tampered")
            shown = self._run("0" * 64, str(f))
        self.assertTrue(shown)
        self.assertEqual(shown[0][0], "warn")
        self.assertIn("不一致", shown[0][1])

    def test_no_baseline_path_does_not_claim_success(self):
        shown = self._run("", "/whatever")
        self.assertTrue(shown)
        self.assertEqual(shown[0][0], "info")
        self.assertIn("检查更新", shown[0][1], "没基准值时要告诉用户先点检查更新")


if __name__ == "__main__":
    unittest.main()
