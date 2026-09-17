# -*- coding: utf-8 -*-
"""G38-1~7 设置页保存回调 单测（离屏 Qt）。

覆盖 MainWindow 缺失的 6 个保存回调（settings_panel.py:217-251 connect 目标）：
- _save_host_tokens：多行 "host=token" → settings.host_tokens（空值=删除条目）
- _save_mirror：多行 "host=prefix" → settings.mirror_prefix
- _save_custom_hosts：逗号/换行分隔 → settings.custom_hosts（小写归一）
- _save_precheck / _save_single_branch / _save_force_ipv4：checkbox → bool 字段
- 全部经 SettingsStore 加密往返持久化一致
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

from PyQt6.QtWidgets import QApplication

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _mk_window(tmp: Path) -> MainWindow:
    db = Database(tmp / "t.db")
    return MainWindow(data_dir=tmp, db=db,
                      settings=SettingsStore(tmp / "settings.json"))


class TestSaveHostTokens(unittest.TestCase):
    """G38-1 UI 保存回调：host_tokens 多行解析。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.w = _mk_window(self.tmp)
        self.w.show()

    def tearDown(self):
        try:
            self.w.busy = False
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def test_parses_host_token_lines(self):
        """多行 host=token → settings.host_tokens dict。"""
        self.w.edit_host_tokens.setText("gitlab.com=glpat-123\ngithub.com=ghp_abc")
        self.w._save_host_tokens()
        self.assertEqual(self.w.settings.host_tokens,
                         {"gitlab.com": "glpat-123", "github.com": "ghp_abc"})

    def test_empty_value_removes_entry(self):
        """空值行（host=）→ 删除该 host 条目。"""
        self.w.settings.host_tokens = {"github.com": "ghp_x", "gitlab.com": "glpat-y"}
        self.w.edit_host_tokens.setText("gitlab.com=")
        self.w._save_host_tokens()
        self.assertEqual(self.w.settings.host_tokens, {})

    def test_skips_malformed_lines(self):
        """无 = 的行、空行 → 忽略不崩。"""
        self.w.edit_host_tokens.setText("github.com=ghp_1\nmalformed-line\n\n  \ngitlab.com=glpat_2")
        self.w._save_host_tokens()
        self.assertEqual(self.w.settings.host_tokens,
                         {"github.com": "ghp_1", "gitlab.com": "glpat_2"})

    def test_roundtrip_encrypted_via_store(self):
        """保存→store.load 读回：host_tokens 逐条目加密落盘且读回解密一致。"""
        self.w.edit_host_tokens.setText("gitlab.com=glpat-secret")
        self.w._save_host_tokens()
        loaded = SettingsStore(self.tmp / "settings.json").load()
        self.assertEqual(loaded.host_tokens, {"gitlab.com": "glpat-secret"})
        raw = (self.tmp / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("glpat-secret", raw)  # 落盘非明文
        self.assertIn("enc:v1:", raw)


class TestSaveMirror(unittest.TestCase):
    """G38-2 UI 保存回调：mirror_prefix 多行解析。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.w = _mk_window(self.tmp)
        self.w.show()

    def tearDown(self):
        try:
            self.w.busy = False
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def test_parses_mirror_lines(self):
        """多行 host=prefix → settings.mirror_prefix dict。"""
        self.w.edit_mirror.setText("github.com=https://ghproxy.com\ngitee.com=https://gitee.com")
        self.w._save_mirror()
        self.assertEqual(self.w.settings.mirror_prefix,
                         {"github.com": "https://ghproxy.com", "gitee.com": "https://gitee.com"})

    def test_empty_input_clears(self):
        """清空文本 → mirror_prefix 空 dict。"""
        self.w.settings.mirror_prefix = {"github.com": "https://ghproxy.com"}
        self.w.edit_mirror.setText("")
        self.w._save_mirror()
        self.assertEqual(self.w.settings.mirror_prefix, {})


class TestSaveCustomHosts(unittest.TestCase):
    """G38-7 UI 保存回调：custom_hosts 解析。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.w = _mk_window(self.tmp)
        self.w.show()

    def tearDown(self):
        try:
            self.w.busy = False
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def test_parses_comma_and_newline(self):
        """逗号/换行/空格混合分隔 → tuple，小写归一，去空。"""
        self.w.edit_custom_hosts.setText("mygit.example.com, Git.Example.net\n\n  \nthird.example.org")
        self.w._save_custom_hosts()
        self.assertEqual(self.w.settings.custom_hosts,
                         ("mygit.example.com", "git.example.net", "third.example.org"))

    def test_empty_clears(self):
        """清空 → 空 tuple。"""
        self.w.settings.custom_hosts = ("mygit.example.com",)
        self.w.edit_custom_hosts.setText("")
        self.w._save_custom_hosts()
        self.assertEqual(self.w.settings.custom_hosts, ())


class TestSaveSwitches(unittest.TestCase):
    """G38-3/4/6 UI 保存回调：三个网络开关。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.w = _mk_window(self.tmp)
        self.w.show()

    def tearDown(self):
        try:
            self.w.busy = False
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def test_precheck_switch(self):
        """ck_precheck → settings.precheck_remote。"""
        self.w.ck_precheck.setChecked(True)
        self.w._save_precheck(True)
        self.assertTrue(self.w.settings.precheck_remote)
        self.w._save_precheck(False)
        self.assertFalse(self.w.settings.precheck_remote)

    def test_single_branch_switch(self):
        """ck_single_branch → settings.single_branch。"""
        self.w.ck_single_branch.setChecked(True)
        self.w._save_single_branch(True)
        self.assertTrue(self.w.settings.single_branch)
        self.w._save_single_branch(False)
        self.assertFalse(self.w.settings.single_branch)

    def test_force_ipv4_switch(self):
        """ck_force_ipv4 → settings.force_ipv4。"""
        self.w.ck_force_ipv4.setChecked(True)
        self.w._save_force_ipv4(True)
        self.assertTrue(self.w.settings.force_ipv4)
        self.w._save_force_ipv4(False)
        self.assertFalse(self.w.settings.force_ipv4)

    def test_all_switches_roundtrip(self):
        """三开关 → store.load 持久化一致。"""
        self.w.ck_precheck.setChecked(True)
        self.w._save_precheck(True)
        self.w.ck_single_branch.setChecked(True)
        self.w._save_single_branch(True)
        self.w.ck_force_ipv4.setChecked(True)
        self.w._save_force_ipv4(True)
        loaded = SettingsStore(self.tmp / "settings.json").load()
        self.assertTrue(loaded.precheck_remote)
        self.assertTrue(loaded.single_branch)
        self.assertTrue(loaded.force_ipv4)


if __name__ == "__main__":
    unittest.main()


class TestG384SingleBranchPropagation(unittest.TestCase):
    """G38-4 回归：设置页 single_branch 开关必须传播到 _launch 重建的 engine。

    缺陷记录：_launch 原来只按 mode_combo.currentIndex()==2 推断 single_branch，
    设置页开关开启时对后续批次无效。修复后 = 显式参数 or 设置开关 or UI 模式。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.w = _mk_window(self.tmp)
        self.w.show()

    def tearDown(self):
        try:
            self.w.busy = False
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def test_settings_switch_applies_to_next_batch(self):
        """设置页 single_branch=True 且 mode_combo 非模式2 → _launch 后 engine.single_branch True。"""
        self.w.settings.single_branch = True
        self.w.ck_single_branch.setChecked(True)
        self.w._save_single_branch(True)
        self.w.mode_combo.setCurrentIndex(0)  # 满量模式（非单分支 UI 模式）
        self.w._launch([], target_root=self.w.data_dir / "clones",
                       shallow=False, depth=0)
        self.assertTrue(self.w.engine.single_branch,
                        "设置页开关必须在 _launch 重建 engine 时生效")

    def test_ui_mode_overrides_when_off(self):
        """UI 模式 2（单分支浅克隆）→ engine.single_branch True（即使设置关）。"""
        self.w.settings.single_branch = False
        self.w.mode_combo.setCurrentIndex(2)
        self.w._launch([], target_root=self.w.data_dir / "clones",
                       shallow=True, depth=1)
        self.assertTrue(self.w.engine.single_branch)
