"""G84-4：`scripts/bump_version.py` 的守卫。

守的不是"脚本能跑"，而是**它不会漏载体、不会越改越乱**：
- 载体表必须覆盖当前仓库里所有仍在跟踪、且含当前版本字面量的文件
  （排除项必须写理由，否则以后谁都能加一条排除把守卫糊过去）；
- dry-run 必须真的不写盘；
- 脏工作区必须拒绝执行；
- 改后自证失败必须回滚。
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm import __version__  # noqa: E402


def _load_script():
    path = ROOT / "scripts" / "bump_version.py"
    spec = importlib.util.spec_from_file_location("gcm_bump_version", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Python 3.14 起 dataclass 依赖 cls.__module__ 在 sys.modules 里可查，先注册再执行
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bump = _load_script()


class TestVersionCarrierCoverage(unittest.TestCase):
    """载体表与仓库真实状态必须一一对上。"""

    def _tracked_files_with_version(self) -> set[str]:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, text=True,
                             capture_output=True, encoding="utf-8", errors="replace")
        if out.returncode != 0:
            self.skipTest("不是 git 工作区")
        hits: set[str] = set()
        for rel in out.stdout.splitlines():
            if rel in bump.ALLOWED_HISTORICAL:
                continue
            if any(rel.startswith(p) for p in bump.ALLOWED_PREFIXES):
                continue
            path = ROOT / rel
            try:
                if not path.is_file() or path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if __version__ in text:
                hits.add(rel)
        return hits

    def test_every_file_with_version_literal_is_in_carrier_table(self):
        carrier_files = {rel for rel, _p, _r in bump.CARRIERS}
        actual = self._tracked_files_with_version()
        missed = sorted(actual - carrier_files)
        self.assertEqual(
            missed, [],
            "这些文件含当前版本字面量但不在载体表里（下次 bump 必漏）：\n  "
            + "\n  ".join(missed)
            + "\n要么加进 CARRIERS，要么加进 ALLOWED_HISTORICAL 并写明理由。")

    def test_carrier_table_has_no_dead_entries(self):
        """载体表里的文件必须真实存在，否则是过期模板。"""
        dead = [rel for rel, _p, _r in bump.CARRIERS if not (ROOT / rel).is_file()]
        self.assertEqual(dead, [], f"载体表引用了不存在的文件：{dead}")

    def test_every_carrier_entry_actually_matches(self):
        """每条模板都必须能命中当前版本（模板漂移 = 下次 bump 静默漏改）。"""
        snaps, _changes = bump.plan(__version__, __version__)
        missing: list[str] = []
        for rel, pattern, _repl in bump.CARRIERS:
            rx = bump.re.compile(bump._fmt(pattern, __version__, __version__))
            text = (ROOT / rel).read_text(encoding="utf-8")
            if not rx.search(text):
                missing.append(f"{rel} :: {pattern}")
        self.assertEqual(missing, [], "这些模板在当前文本里找不到匹配：\n  "
                         + "\n  ".join(missing))
        self.assertTrue(snaps)

    def test_historical_exclusions_all_carry_reason(self):
        """排除项必须写理由 —— 防止以后加一条空排除把守卫放宽。"""
        weak = [k for k, v in bump.ALLOWED_HISTORICAL.items() if len(v.strip()) < 6]
        self.assertEqual(weak, [], f"这些排除项没有写清理由：{weak}")


class TestBumpBehaviour(unittest.TestCase):
    def test_compute_next(self):
        self.assertEqual(bump.compute_next("9.17.0", "patch", None), "9.17.1")
        self.assertEqual(bump.compute_next("9.17.9", "minor", None), "9.18.0")
        self.assertEqual(bump.compute_next("9.17.0", "major", None), "10.0.0")
        self.assertEqual(bump.compute_next("9.17.0", None, "10.0.0"), "10.0.0")
        with self.assertRaises(RuntimeError):
            bump.compute_next("9.17.0", None, "10")
        with self.assertRaises(RuntimeError):
            bump.compute_next("9.17.0", None, None)

    def test_dry_run_writes_nothing(self):
        before = {p: p.read_text(encoding="utf-8") for p in
                  (ROOT / "gcm" / "__init__.py", ROOT / "pyproject.toml")}
        rc = bump.main(["--bump", "patch"])
        self.assertEqual(rc, 0)
        after = {p: p.read_text(encoding="utf-8") for p in
                 (ROOT / "gcm" / "__init__.py", ROOT / "pyproject.toml")}
        self.assertEqual(before, after, "dry-run 竟然改了文件")

    def test_refuses_dirty_tree_without_flag(self):
        with mock.patch.object(bump, "is_dirty", return_value=True):
            rc = bump.main(["--bump", "patch", "--write"])
        self.assertEqual(rc, 3, "脏工作区未被拒绝")

    def test_rollback_when_verify_fails(self):
        """自证失败必须把已改文件**逐字节**还原（含行尾），不能留下半改状态。"""
        path = ROOT / "gcm" / "__init__.py"
        original = path.read_bytes()
        with mock.patch.object(bump, "is_dirty", return_value=False), \
                mock.patch.object(bump, "verify", return_value=1):
            rc = bump.main(["--bump", "patch", "--write"])
        self.assertEqual(rc, 1)
        self.assertEqual(path.read_bytes(), original,
                         "自证失败后文件未逐字节回滚（行尾也可能被改写）")

    def test_snapshot_keeps_crlf(self):
        """读快照必须记住 CRLF —— 否则回滚会把 Windows 工作区的行尾改成 LF。"""
        import tempfile
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as fh:
            fh.write(b"a\r\nb\r\n")
            tmp = pathlib.Path(fh.name)
        try:
            snap = bump._read_snapshot(tmp)
            self.assertEqual(snap.newline, "\r\n")
            self.assertEqual(snap.text, "a\nb\n")
            self.assertEqual(snap.raw, b"a\r\nb\r\n")
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
