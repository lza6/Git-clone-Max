# -*- coding: utf-8 -*-
"""B3 删除路径护栏测试：危险路径矩阵 + prune-untouched 集成（含变异自检语义）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.prune import PruneItem, execute_prune
from gcm.util.path_guard import deletion_refusal


class TestDeletionRefusal(unittest.TestCase):
    def test_relative_and_empty_refused(self):
        self.assertIsNotNone(deletion_refusal(""))
        self.assertIsNotNone(deletion_refusal("clones/a__b"))
        self.assertIsNotNone(deletion_refusal("a\x00b"))

    def test_drive_root_refused(self):
        anchor = Path(tempfile.mkdtemp()).anchor  # 当前盘符根（如 C:\ 或 /）
        self.assertIsNotNone(deletion_refusal(anchor), "盘符根必须拒绝")

    def test_home_and_ancestor_refused(self):
        home = Path.home()
        self.assertIsNotNone(deletion_refusal(home), "用户主目录必须拒绝")
        self.assertIsNotNone(deletion_refusal(home.parent), "主目录上级必须拒绝")

    @unittest.skipUnless(os.name == "nt", "Windows 目录别名语义")
    def test_windows_system_components_refused(self):
        for p in (r"C:\Windows\Temp\repo", r"C:\Program Files\App\repo",
                  r"C:\ProgramData\repo", r"C:\$Recycle.Bin\x",
                  r"C:\Windows.old\repo", r"C:\Windows10Upgrade\repo"):
            self.assertIsNotNone(deletion_refusal(p), p)
        # 尾随点别名（Windows 把 `Windows.` 当 `Windows`）
        self.assertIsNotNone(deletion_refusal("C:\\Windows.\\x"))

    def test_deep_same_name_allowed(self):
        """审查修正：只看盘符根下一级 —— 深层同名目录是普通工作目录，不得误拒。"""
        d = Path(tempfile.mkdtemp(prefix="gcm-guard-deep-"))
        for name in ("boot", "recovery", "windows", "system32"):
            self.assertIsNone(deletion_refusal(d / name / "owner__repo"),
                              f"深层 {name} 目录必须放行")

    @unittest.skipUnless(os.name == "nt", "UNC 仅 Windows")
    def test_unc_refused(self):
        self.assertIsNotNone(deletion_refusal(r"\\server\share\repo"))

    @unittest.skipUnless(os.name != "nt", "POSIX 顶层目录")
    def test_posix_toplevel_refused(self):
        self.assertIsNotNone(deletion_refusal("/usr"))
        self.assertIsNotNone(deletion_refusal("/etc"))

    @unittest.skipIf(os.name == "nt",
                     "POSIX 语义：Windows 上 /etc 不是绝对路径，判定在 resolve 之前早退")
    def test_resolved_private_symlink_still_refused(self):
        """macOS 的 /etc、/tmp、/var 指向 /private/*：resolve 后也必须拒绝。

        2026-11-26 CI macOS 实测：只看 resolve 结果时 `/etc → /private/etc`
        变成两级路径，顶层系统目录被放行（test_posix_toplevel_refused 红）。

        按路径选择性 mock resolve（只重定向这两个顶层目录，其余走真实逻辑），
        并断言拒绝原因是「系统目录」——回退修复（只查 resolve 结果）时本用例
        必红（rp=/private/etc 两级、home 判定也不命中 → 返回 None）。
        早先「全局 mock 返回固定值」的写法对回退修复同样绿，是空守卫，已废弃。
        """
        from unittest import mock
        real_resolve = Path.resolve

        def fake_resolve(self_path, *args, **kwargs):
            s = str(self_path)
            if s in ("/etc", "/tmp"):
                return Path("/private" + s)
            return real_resolve(self_path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", fake_resolve):
            for p in ("/etc", "/tmp"):
                reason = deletion_refusal(p)
                self.assertIsNotNone(reason, f"{p} 必须被拒绝")
                self.assertIn("系统目录", reason, f"{p} 应以系统目录拒绝，实际：{reason}")

    def test_normal_clone_dir_allowed(self):
        d = Path(tempfile.mkdtemp(prefix="gcm-guard-"))
        self.assertIsNone(deletion_refusal(d), "普通临时仓库目录必须放行")
        self.assertIsNone(deletion_refusal(d / "owner__repo"))

    def test_home_subfolder_exact_refused_child_allowed(self):
        home = Path.home()
        target = home / "Downloads"
        self.assertIsNotNone(deletion_refusal(target), "受保护用户文件夹必须拒绝")
        self.assertIsNone(deletion_refusal(target / "owner__repo"),
                          "受保护文件夹内的具体仓库放行")


class _FakeDB:
    def __init__(self):
        self.deleted: list[int] = []

    def delete_repo(self, repo_id: int) -> None:
        self.deleted.append(repo_id)


def _item(path: str) -> PruneItem:
    return PruneItem(repo_id=1, folder_name="f", url="https://h/o/r",
                     local_path=path, protected=False, remote_gone=True)


class TestPruneIntegration(unittest.TestCase):
    def test_dangerous_path_skips_both_db_and_dir(self):
        db = _FakeDB()
        counts = execute_prune(db, [_item(str(Path.home()))], "prune-untouched")
        self.assertEqual(counts["skipped_dangerous"], 1)
        self.assertEqual(counts["pruned"], 0, "护栏拒绝时不得删 DB 记录")
        self.assertEqual(db.deleted, [])

    def test_safe_path_still_deleted(self):
        d = Path(tempfile.mkdtemp(prefix="gcm-prune-ok-"))
        (d / "marker.txt").write_text("x", encoding="utf-8")
        db = _FakeDB()
        counts = execute_prune(db, [_item(str(d))], "prune-untouched")
        self.assertEqual(counts["pruned"], 1)
        self.assertEqual(counts["dir_removed"], 1)
        self.assertEqual(db.deleted, [1])
        self.assertFalse(d.exists(), "安全路径的目录应被删除")

    def test_plain_prune_mode_ignores_guard(self):
        """prune 模式不删目录，护栏不该插手（DB 记录照删）。"""
        db = _FakeDB()
        counts = execute_prune(db, [_item(str(Path.home()))], "prune")
        self.assertEqual(counts["pruned"], 1)
        self.assertEqual(counts["skipped_dangerous"], 0)
        self.assertEqual(db.deleted, [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
