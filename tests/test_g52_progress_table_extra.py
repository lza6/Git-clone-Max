"""G52 进度表补充覆盖：搜索过滤 / 排序优先级 / 暂停 / 复制详情 / 打开目录。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from gcm.ui.progress_table import ProgressTable
from gcm.models import RepoSpec


class _Owner:
    """最小 owner 替身（ProgressTable 依赖的成员）。"""

    def __init__(self, tmp: Path):
        self.data_dir = tmp
        self.target_edit = mock.Mock()
        self.target_edit.text.return_value = str(tmp / "clones")
        self.row_specs = {}
        self.statusBar = mock.Mock()
        self.statusBar.return_value.showMessage = mock.Mock()
        self.engine = mock.Mock()
        self.engine.pause_task = mock.Mock(return_value=True)
        self._emit_log = mock.Mock()


def _make_table(owner) -> ProgressTable:
    return ProgressTable(owner=owner)


class TestProgressTableExtra(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_pt_extra_"))
        self.owner = _Owner(self.tmp)
        self.pt = _make_table(self.owner)
        # 预填 3 行（先 _prepare_table 建立行）
        self.pt._prepare_table(3)
        for i, name in enumerate(["aaa_repo", "bbb_repo", "ccc_repo"]):
            self.pt._add_table_row(i, RepoSpec("o", name, f"https://github.com/o/{name}.git",
                                               folder_name=name))
        self.owner.row_specs = {i: RepoSpec("o", n, f"https://github.com/o/{n}.git",
                                            folder_name=n)
                                for i, n in enumerate(["aaa_repo", "bbb_repo", "ccc_repo"])}

    def test_filter_rows(self):
        visible = self.pt._filter_progress_rows("bbb")
        self.assertEqual(visible, 1)

    def test_filter_empty_shows_all(self):
        visible = self.pt._filter_progress_rows("")
        self.assertEqual(visible, 3)

    def test_schedule_filter_debounce(self):
        self.pt._schedule_progress_filter("aaa")
        self.assertTrue(hasattr(self.pt, "_pf_timer"))

    def test_sort_changed_priority(self):
        self.pt._on_sort_changed(2, 0)
        it = self.pt.table.item(0, 2)
        self.assertIsNotNone(it)

    def test_pause_selected(self):
        self.pt.table.selectRow(1)
        self.pt.pause_selected()
        self.assertTrue(self.owner.engine.pause_task.called)

    def test_pause_selected_none(self):
        self.pt.pause_selected()  # 无选中 → 不崩
        self.assertFalse(self.owner.engine.pause_task.called)

    def test_copy_detail(self):
        from PyQt6.QtWidgets import QApplication as _A
        _A.clipboard().clear()
        if hasattr(self.pt, "_copy_row_detail"):
            self.pt._copy_row_detail(0)  # 行无详情 → 不崩

    def test_open_row_dir_missing(self):
        # 目录不存在 → warning 不崩
        with mock.patch("gcm.ui.progress_table._mw.QMessageBox.warning") as w:
            self.pt._open_row_dir(0)
            w.assert_called()


if __name__ == "__main__":
    unittest.main()
