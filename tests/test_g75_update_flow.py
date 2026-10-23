"""更新检查尾部链路（G75 收口轮补测）：弹窗 → 打开浏览器 / SHA 一次性。

**为什么单独成文件**：`tests/test_g66_patch.py` 已 1400+ 行且是历史聚合文件；
本组用例需要一个模块级 `QApplication`（项目铁律：构造 `MainWindow` 的测试必须先建 app），
放在旧文件里会和它的既有 import 顺序打架（实测会把进程挂住）。

只断言**调用行为**，不真的开浏览器、不弹真模态：
- 点「是」→ `webbrowser.open(url)` 恰好一次（否则"发现新版本"是死按钮）；
- 点「否」→ 不打开；
- err 分支 → 只提示错误、不碰浏览器；
- `_update_sha256` 用后必须清空（否则下次检查带着上次哈希误导用户）。
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from gcm.db.repo_db import Database  # noqa: E402
from gcm.db.settings import SettingsStore  # noqa: E402
from gcm.ui import update_flow  # noqa: E402
from gcm.ui.main_window import MainWindow  # noqa: E402

URL = "https://example.invalid/dl"


def _stub(answer: QMessageBox.StandardButton):
    """替身 QMessageBox：记录调用、不改真实弹窗行为。"""

    class _MB:
        StandardButton = QMessageBox.StandardButton
        seen: list[str] = []

        @staticmethod
        def information(*_a, **_k):
            _MB.seen.append("information")

        @staticmethod
        def question(*_a, **_k):
            _MB.seen.append("question")
            return answer

    return _MB


class TestUpdateFlowTail(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gcm_updtail_")
        d = pathlib.Path(self._tmp.name)
        self.win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                              settings=SettingsStore(d / "s.json"))
        self.win._can_show_modal = lambda: True   # 离屏窗口不弹真模态

    def tearDown(self):
        try:
            self.win.busy = False
            self.win.close()
            self.win.deleteLater()
        finally:
            self._tmp.cleanup()

    def _run(self, *, has_new, ver="9.9.9", url=URL, err="", answer, sha=""):
        stub = _stub(answer)
        with mock.patch.object(update_flow, "_msgbox", return_value=stub), \
                mock.patch("webbrowser.open") as opened:
            update_flow.on_checked(self.win, has_new, ver, url, err, sha)
        return stub, opened

    def test_yes_opens_browser_with_release_url(self):
        stub, opened = self._run(has_new=True, answer=QMessageBox.StandardButton.Yes)
        opened.assert_called_once_with(URL)
        self.assertEqual(stub.seen, ["question"])

    def test_no_does_not_open_browser(self):
        _stub_mb, opened = self._run(has_new=True, answer=QMessageBox.StandardButton.No)
        opened.assert_not_called()

    def test_error_branch_never_opens_browser(self):
        stub, opened = self._run(has_new=False, ver="", url="", err="网络错误",
                                 answer=QMessageBox.StandardButton.Yes)
        opened.assert_not_called()
        self.assertEqual(stub.seen, ["information"])

    def test_sha_is_one_shot(self):
        self.win._update_sha256 = "deadbeef" * 8
        self._run(has_new=True, answer=QMessageBox.StandardButton.No)
        self.assertEqual(self.win._update_sha256, "",
                         "SHA 用后未清空（会污染下一次提示）")

    def test_hidden_window_does_not_pop_modal(self):
        """不可见窗口下不得弹模态（否则 offscreen/CI 会挂死）。"""
        self.win._can_show_modal = lambda: False
        stub, opened = self._run(has_new=True, answer=QMessageBox.StandardButton.Yes)
        self.assertEqual(stub.seen, [], "不可见窗口仍弹了模态")
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
