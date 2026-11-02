"""Rev8 P1-1 守卫：半成品判定单一来源 + 两级分类 + 修复动作。

三条不变量：
1. 判定的**唯一事实源**是 `gcm/app/halfclone.py`；`scanner._looks_halfcloned` 与
   `ui/halfclone_actions.looks_halfcloned` 必须都是它的别名/委托（不允许两份等价逻辑）。
2. 分类必须区分两级：`unborn_head`（HEAD 坏 → 只能重克隆）与
   `not_checked_out`（HEAD 正常但未检出 → `git checkout -f` 秒级零网络修复）。
   语料实测（1,729 仓）：93 个 unborn vs 7 个 not_checked_out，修复成本差两个数量级。
3. `repair_halfclone()` 只对 not_checked_out 动手，修复后工作树非空且 HEAD 不变。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _git(*args: str, cwd: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    if r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return (r.stdout or "").strip()


def _make_source(td: Path) -> Path:
    """建一个含 1 提交的真实源仓库。"""
    src = td / "_src"
    src.mkdir()
    _git("init", "-q", "-b", "main", cwd=str(src))
    (src / "a.txt").write_text("hello", encoding="utf-8")
    _git("add", ".", cwd=str(src))
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=str(src))
    return src


class TestSingleSource(unittest.TestCase):
    """不变量 1：单一事实源。"""

    def test_scanner_delegates_to_app_module(self):
        import gcm.app.halfclone as hc
        import gcm.app.scanner as scanner
        self.assertIs(scanner._looks_halfcloned, hc.is_halfcloned)

    def test_ui_delegates_to_app_module(self):
        import gcm.app.halfclone as hc
        import gcm.ui.halfclone_actions as ui_hc
        self.assertIs(ui_hc.looks_halfcloned, hc.is_halfcloned)

    def test_no_duplicate_logic(self):
        """scanner/ui 不得再有 `any(... != ".git" ...)` 式的本地重复判定。"""
        import ast
        for mod in ("gcm/app/scanner.py", "gcm/ui/halfclone_actions.py"):
            src = (Path(__file__).resolve().parent.parent / mod).read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.GeneratorExp):
                    txt = ast.dump(node)
                    self.assertNotIn('Constant(value=\'.git\')', txt,
                                     f"{mod} 里仍有本地半成品判定逻辑")


class TestClassifyTwoLevels(unittest.TestCase):
    """不变量 2：两级分类。"""

    def test_healthy_repo(self):
        from gcm.app.halfclone import classify_repo
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            src = _make_source(td_path)
            dst = td_path / "clone"
            _git("clone", "-q", str(src), str(dst), cwd=str(td_path))
            self.assertEqual(classify_repo(dst), "healthy")

    def test_not_checked_out_is_repairable(self):
        """克隆后删掉工作树文件（模拟「取回未检出」）→ not_checked_out。"""
        from gcm.app.halfclone import classify_repo
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            src = _make_source(td_path)
            dst = td_path / "clone"
            _git("clone", "-q", str(src), str(dst), cwd=str(td_path))
            (dst / "a.txt").unlink()
            for p in dst.iterdir():
                if p.name == ".git":
                    continue
                if p.is_dir():
                    subprocess.run(["rmdir", "/s", "/q", str(p)], shell=True)
                else:
                    p.unlink()
            self.assertEqual(classify_repo(dst), "not_checked_out")

    def test_unborn_head(self):
        """git init 但无提交 → unborn_head（不可 checkout 修复）。"""
        from gcm.app.halfclone import classify_repo
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "repo"
            d.mkdir()
            _git("init", "-q", "-b", "main", cwd=str(d))
            self.assertEqual(classify_repo(d), "unborn_head")

    def test_empty_dir_is_not_repo(self):
        from gcm.app.halfclone import classify_repo
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "plain"
            d.mkdir()
            self.assertEqual(classify_repo(d), "not_repo")

    def test_bare_repo_not_classified_as_halfclone(self):
        from gcm.app.halfclone import classify_repo
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            src = _make_source(td_path)
            bare = td_path / "bare.git"
            _git("clone", "-q", "--mirror", str(src), str(bare), cwd=str(td_path))
            self.assertEqual(classify_repo(bare), "not_repo")


class TestRepair(unittest.TestCase):
    """不变量 3：修复动作。"""

    def test_repair_not_checked_out(self):
        from gcm.app.halfclone import classify_repo, repair_halfclone
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            src = _make_source(td_path)
            dst = td_path / "clone"
            _git("clone", "-q", str(src), str(dst), cwd=str(td_path))
            for p in list(dst.iterdir()):
                if p.name != ".git":
                    if p.is_dir():
                        subprocess.run(["rmdir", "/s", "/q", str(p)], shell=True)
                    else:
                        p.unlink()
            self.assertEqual(classify_repo(dst), "not_checked_out")
            ok, msg = repair_halfclone(dst)
            self.assertTrue(ok, msg)
            self.assertEqual(classify_repo(dst), "healthy")
            self.assertTrue((dst / "a.txt").exists())

    def test_repair_refuses_unborn_head(self):
        from gcm.app.halfclone import repair_halfclone
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "repo"
            d.mkdir()
            _git("init", "-q", "-b", "main", cwd=str(d))
            ok, msg = repair_halfclone(d)
            self.assertFalse(ok)
            self.assertIn("重新克隆", msg)

    def test_repair_refuses_healthy(self):
        from gcm.app.halfclone import repair_halfclone
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "plain"
            d.mkdir()
            ok, msg = repair_halfclone(d)
            self.assertFalse(ok)

    def test_repair_preserves_head(self):
        """修复不得改 HEAD 指向（`checkout -f` 不动引用）。"""
        from gcm.app.halfclone import repair_halfclone
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            src = _make_source(td_path)
            dst = td_path / "clone"
            _git("clone", "-q", str(src), str(dst), cwd=str(td_path))
            sha_before = _git("rev-parse", "HEAD", cwd=str(dst))
            for p in list(dst.iterdir()):
                if p.name != ".git":
                    if p.is_dir():
                        subprocess.run(["rmdir", "/s", "/q", str(p)], shell=True)
                    else:
                        p.unlink()
            repair_halfclone(dst)
            self.assertEqual(_git("rev-parse", "HEAD", cwd=str(dst)), sha_before)


class TestWiring(unittest.TestCase):
    """接线：扫描结果带上分类；UI 有修复入口。"""

    def test_repo_info_carries_classification(self):
        from gcm.app.scanner import scan_git_dirs
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            src = _make_source(td_path)
            dst = td_path / "owner__repo"
            _git("clone", "-q", str(src), str(dst), cwd=str(td_path))
            for p in list(dst.iterdir()):
                if p.name != ".git":
                    if p.is_dir():
                        subprocess.run(["rmdir", "/s", "/q", str(p)], shell=True)
                    else:
                        p.unlink()
            rows = scan_git_dirs(str(td_path), max_depth=2)
        target = [r for r in rows if r.folder_name == "owner__repo"]
        self.assertTrue(target)
        self.assertEqual(getattr(target[0], "halfclone_kind", ""), "not_checked_out")

    def test_progress_menu_has_repair_action(self):
        """进度表右键菜单在 not_checked_out 行提供「检出修复」。"""
        import ast
        src = (Path(__file__).resolve().parent.parent / "gcm/ui/progress_menu.py"
               ).read_text(encoding="utf-8")
        self.assertIn("repair_halfclone", src)
        self.assertIn("classify_repo", src)


if __name__ == "__main__":
    unittest.main()
