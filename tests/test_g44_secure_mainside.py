# -*- coding: utf-8 -*-
"""M2 安全纵深 G44-2/G44-6 专项测试：敏感目录校验 + 数据目录私有性/迁移。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestSensitiveDir(unittest.TestCase):
    """G44-2：download_tools._is_sensitive_dir / _classify_mkdir_error。"""

    def _mod(self):
        from gcm.ui import download_tools as m
        return m

    def test_sensitive_windows_root(self):
        m = self._mod()
        self.assertTrue(m._is_sensitive_dir(r"C:\Windows\System32"))
        self.assertTrue(m._is_sensitive_dir(r"C:\Windows"))
        self.assertTrue(m._is_sensitive_dir(r"C:\Program Files\Foo"))

    def test_sensitive_case_insensitive(self):
        m = self._mod()
        self.assertTrue(m._is_sensitive_dir("c:\\windows\\temp"))

    def test_non_sensitive(self):
        m = self._mod()
        self.assertFalse(m._is_sensitive_dir("D:\\repos"))
        self.assertFalse(m._is_sensitive_dir("C:\\Users\\me\\repos"))
        self.assertFalse(m._is_sensitive_dir(""))

    def test_classify_permission(self):
        m = self._mod()
        import errno
        msg = m._classify_mkdir_error(OSError(errno.EACCES, "denied"))
        self.assertIn("权限不足", msg)

    def test_classify_notexist(self):
        m = self._mod()
        import errno
        msg = m._classify_mkdir_error(OSError(errno.ENOENT, "missing"))
        self.assertIn("不存在", msg)

    def test_classify_generic(self):
        m = self._mod()
        msg = m._classify_mkdir_error(RuntimeError("weird"))
        self.assertIn("目录创建失败", msg)

    def test_choose_target_sensitive_warns(self):
        """G44-2：选择敏感目录 → 弹确认；点 No 不写入。"""
        m = self._mod()
        owner = mock.Mock()
        owner.target_edit = mock.Mock()
        owner.target_edit.text.return_value = ""
        owner.settings = mock.Mock()
        owner.settings_store = mock.Mock()
        m_msg = mock.Mock()
        m_msg.StandardButton.Yes = mock.Mock("Yes")
        m_msg.StandardButton.No = mock.Mock("No")
        m_msg.warning.return_value = m_msg.StandardButton.No
        m_fd = mock.Mock()
        m_fd.getExistingDirectory.return_value = r"C:\Windows\System32"
        with mock.patch("gcm.ui.download_tools._msgbox", return_value=m_msg), \
             mock.patch("gcm.ui.download_tools._fd", return_value=m_fd):
            m.choose_target(owner)
        m_msg.warning.assert_called_once()
        owner.target_edit.setText.assert_not_called()

    def test_choose_target_sensitive_yes_proceeds(self):
        m = self._mod()
        owner = mock.Mock()
        owner.target_edit = mock.Mock()
        owner.target_edit.text.return_value = ""
        owner.settings = mock.Mock()
        owner.settings_store = mock.Mock()
        m_msg = mock.Mock()
        m_msg.StandardButton.Yes = mock.Mock("Yes")
        m_msg.warning.return_value = m_msg.StandardButton.Yes
        m_fd = mock.Mock()
        m_fd.getExistingDirectory.return_value = r"C:\Windows\Temp"
        with mock.patch("gcm.ui.download_tools._msgbox", return_value=m_msg), \
             mock.patch("gcm.ui.download_tools._fd", return_value=m_fd):
            m.choose_target(owner)
        owner.target_edit.setText.assert_called_once_with(r"C:\Windows\Temp")


class TestDataDirPrivacy(unittest.TestCase):
    """G44-6：settings.is_data_dir_private / migrate_data_dir。"""

    def _mod(self):
        from gcm.db.settings import is_data_dir_private, migrate_data_dir
        return is_data_dir_private, migrate_data_dir

    def test_private_under_home(self):
        isp, _ = self._mod()
        home = Path(tempfile.mkdtemp(prefix="home_"))
        d = home / "AppData" / "Git-clone-Max"
        self.assertTrue(isp(d, home=str(home)))

    def test_public_not_under_home(self):
        isp, _ = self._mod()
        home = Path(tempfile.mkdtemp(prefix="home_"))
        public = Path(tempfile.mkdtemp(prefix="pub_")) / "data"
        self.assertFalse(isp(public, home=str(home)))

    def test_migrate_moves_content(self):
        _, mig = self._mod()
        src = Path(tempfile.mkdtemp(prefix="src_"))
        dst = Path(tempfile.mkdtemp(prefix="dst_")) / "data"
        (src / "settings.json").write_text("{}", encoding="utf-8")
        (src / "clones").mkdir()
        ok, msg = mig(src, dst)
        self.assertTrue(ok, msg)
        self.assertTrue((dst / "settings.json").exists())
        self.assertTrue((dst / "clones").is_dir())
        self.assertFalse((src / "settings.json").exists())

    def test_migrate_skips_existing(self):
        _, mig = self._mod()
        src = Path(tempfile.mkdtemp(prefix="src_"))
        dst = Path(tempfile.mkdtemp(prefix="dst_")) / "data"
        dst.mkdir(parents=True)
        (src / "a.txt").write_text("1", encoding="utf-8")
        (dst / "a.txt").write_text("2", encoding="utf-8")  # 目标已存在
        ok, _ = mig(src, dst)
        self.assertTrue(ok)
        self.assertEqual((dst / "a.txt").read_text(encoding="utf-8"), "2", "不覆盖目标")


if __name__ == "__main__":
    unittest.main(verbosity=2)
