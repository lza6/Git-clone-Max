"""G84-10：`Database.backup_to()` 必须是**原子且可校验**的。

历史风险：原来是 `wal_checkpoint(TRUNCATE)` + `shutil.copy2(dest)` ——
拷到一半崩溃/断电/磁盘满，`dest` 就是一个**名字正常但内容残缺**的 db 文件；
用户拿去恢复时才发现坏了（"坏备份比没有备份更危险"）。
"""
from __future__ import annotations

import pathlib
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.repo_db import Database  # noqa: E402
from gcm.models import RepoSpec  # noqa: E402


def _add_repo(db: Database, name: str) -> None:
    spec = RepoSpec("o", name, f"https://example.com/o/{name}.git",
                    folder_name=f"o__{name}")
    db.upsert_repo(spec, f"/tmp/o__{name}", host="example.com")


class TestBackupAtomic(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.db = Database(self.dir / "repos.db")
        self.addCleanup(self.db.close)
        for n in ("a", "b", "c"):
            _add_repo(self.db, n)
        self.dest = self.dir / "backup.db"

    def test_backup_is_readable_and_verified(self):
        n = self.db.backup_to(self.dest)
        self.assertEqual(n, 3)
        conn = sqlite3.connect(str(self.dest))
        try:
            rows = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
            self.assertEqual(rows, 3, "备份里的行数与源库不一致")
            self.assertEqual(conn.execute("PRAGMA quick_check;").fetchone()[0], "ok")
        finally:
            conn.close()

    def test_interrupted_copy_leaves_no_half_file(self):
        """拷贝中途失败 → 目标文件必须保持原状（不存在就仍不存在），不留半个 db。"""
        self.assertFalse(self.dest.exists())
        patcher = mock.patch.object(
            shutil, "copy2", side_effect=OSError("disk full"))
        with patcher, self.assertRaises(OSError):
            self.db.backup_to(self.dest)
        self.assertFalse(self.dest.exists(), "失败后留下了半个/空的备份文件")
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"残留 tmp：{leftovers}")

    def test_interrupted_copy_keeps_previous_good_backup(self):
        """已有旧备份时，新一次失败不能把旧备份破坏掉。"""
        self.db.backup_to(self.dest)
        before = self.dest.read_bytes()
        patcher = mock.patch.object(
            shutil, "copy2", side_effect=OSError("disk full"))
        with patcher, self.assertRaises(OSError):
            self.db.backup_to(self.dest)
        self.assertEqual(self.dest.read_bytes(), before, "旧备份被损坏")

    def test_corrupt_new_backup_does_not_destroy_previous(self):
        """R3（审查发现）：拷贝"成功"但产物损坏时，**上一份好备份必须还在**。

        原实现先 `os.replace` 再 `quick_check`，校验失败时 dest 已被换成坏文件。
        现在校验提前到 replace 之前（校验的是 tmp）。
        """
        self.db.backup_to(self.dest)
        good = self.dest.read_bytes()

        def _corrupt_copy(src, dst, *a, **k):
            pathlib.Path(dst).write_bytes(b"not a sqlite database")

        patcher = mock.patch.object(shutil, "copy2", side_effect=_corrupt_copy)
        with patcher, self.assertRaises(RuntimeError):
            self.db.backup_to(self.dest)
        self.assertEqual(self.dest.read_bytes(), good,
                         "坏备份把上一份好备份顶掉了")
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"残留 tmp：{leftovers}")

    def test_corrupt_backup_is_rejected(self):
        self.dest.write_bytes(b"not a sqlite database at all")
        with self.assertRaises(RuntimeError):
            Database._verify_backup(self.dest)

    def test_verify_can_be_skipped(self):
        self.assertEqual(self.db.backup_to(self.dest, verify=False), 3)


if __name__ == "__main__":
    unittest.main()
