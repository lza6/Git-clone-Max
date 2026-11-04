"""G84-12：批次预览 `app/batch_plan.py` 的守卫。

守三件事：
1. **判定与实际行为对齐**（clone / update / blocked / duplicate / invalid 五档）；
2. **纯函数无副作用**（不建目录、不写库、可重复调用）；
3. **与执行器同源**（解析与目录命名必须复用 `url_lib.parse_urls` / `folder_name`，
   不能自己再写一套 —— 预览与真实行为错位比没有预览更有害）。
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.batch_plan import plan_batch  # noqa: E402

URL_A = "https://github.com/owner/alpha"
URL_B = "https://github.com/owner/beta"


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._td.name) / "clones"
        self.root.mkdir()
        self.addCleanup(self._td.cleanup)


class TestActions(_Base):
    def test_fresh_target_is_clone(self):
        plan = plan_batch(URL_A, self.root)
        self.assertEqual(plan.counts["clone"], 1)
        self.assertEqual(plan.writable, 1)
        self.assertEqual(plan.rows[0].action, "clone")

    def test_existing_git_repo_is_update(self):
        target = self.root / "owner__alpha"
        (target / ".git").mkdir(parents=True)
        plan = plan_batch(URL_A, self.root)
        self.assertEqual(plan.rows[0].action, "update")

    def test_existing_plain_dir_is_replace_not_blocked(self):
        """**审查抓出的真问题**：非 git 目录会被执行器 rmtree 后重克隆。

        旧实现把它说成 `blocked` + "不会覆盖" —— 预览在最危险的那一档给了**反的信息**：
        用户/agent 看到"不会覆盖"就放心执行，结果目录被删掉重建。
        """
        (self.root / "owner__alpha").mkdir()
        (self.root / "owner__alpha" / "keep.txt").write_text("data", encoding="utf-8")
        plan = plan_batch(URL_A, self.root)
        self.assertEqual(plan.rows[0].action, "replace")
        self.assertIn("删除", plan.rows[0].reason)

    def test_blocked_only_for_imported_local_path(self):
        """只有 `local_path` 非空（导入的元数据）才是真正的"绝不删除"。"""
        from gcm.app.batch_plan import classify_target
        from gcm.models import RepoSpec

        target = self.root / "owner__alpha"
        target.mkdir()
        imported = RepoSpec("owner", "alpha", "https://github.com/owner/alpha.git",
                            folder_name="owner__alpha", local_path=str(target))
        action, reason = classify_target(imported, target)
        self.assertEqual(action, "blocked")
        self.assertIn("不会删除", reason)

        plain = RepoSpec("owner", "alpha", "https://github.com/owner/alpha.git",
                         folder_name="owner__alpha")
        self.assertEqual(classify_target(plain, target)[0], "replace")

    def test_executor_still_deletes_non_git_dir_for_plain_specs(self):
        """锚住执行器那一行：预览的 `replace` 结论依赖「local_path 为空就 rmtree」。"""
        src = (ROOT / "gcm" / "git" / "service.py").read_text(encoding="utf-8")
        self.assertIn("if not spec.local_path:", src,
                      "执行器的半成品清理分支变了，batch_plan 的 replace 判定需要同步")
        self.assertIn("shutil.rmtree(repo_dir)", src)

    def test_invalid_line_reported(self):
        plan = plan_batch("这不是一个地址\n", self.root)
        self.assertEqual(plan.counts["invalid"], 1)

    def test_duplicate_target_detected(self):
        plan = plan_batch(f"{URL_A}\n{URL_B}", self.root)
        self.assertEqual(plan.counts["clone"], 2)
        plan2 = plan_batch(f"{URL_A}\nhttps://github.com/owner/alpha.git", self.root)
        self.assertEqual(plan2.counts["duplicate"], 1,
                         "同一目标目录出现两次时应标 duplicate（执行器会跳过）")

    def test_tag_syntax_gets_own_folder(self):
        """`@tag` 目录必须与执行器一致（同一仓库不同版本落到不同目录）。"""
        plan = plan_batch("https://github.com/owner/alpha@v1.2.0", self.root)
        self.assertIn("@", plan.rows[0].folder)


class TestPurity(_Base):
    def test_no_side_effects_and_repeatable(self):
        before = sorted(p.name for p in self.root.iterdir())
        first = plan_batch(f"{URL_A}\n{URL_B}", self.root).to_dict()
        second = plan_batch(f"{URL_A}\n{URL_B}", self.root).to_dict()
        self.assertEqual(first, second, "两次调用结果不一致（说明有隐藏状态）")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before,
                         "预览竟然创建了目录")

    def test_limits_are_stated(self):
        """预览必须自曝边界：不能声称"已是最新"（那需要联网）。"""
        doc = plan_batch(URL_A, self.root).to_dict()
        self.assertTrue(doc["dry_run"])
        self.assertIn("ls-remote", doc["limits"])


class TestSameSourceAsEngine(unittest.TestCase):
    def test_uses_url_lib_parser(self):
        tree = ast.parse((ROOT / "gcm" / "app" / "batch_plan.py").read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertIn("parse_urls", imported,
                      "预览没有复用 url_lib.parse_urls（会与执行器判定错位）")

    def test_target_rule_matches_engine_line(self):
        """执行器用 `spec.local_path or root / spec.folder_name` —— 预览也必须是这一条。"""
        src = (ROOT / "gcm" / "app" / "engine.py").read_text(encoding="utf-8")
        self.assertIn("spec.local_path or self.root / spec.folder_name", src,
                      "engine 的目标目录推导变了，batch_plan 需要同步")
        plan_src = (ROOT / "gcm" / "app" / "batch_plan.py").read_text(encoding="utf-8")
        self.assertIn("spec.local_path", plan_src)

    def test_cli_exposes_dry_run(self):
        out = subprocess.run([sys.executable, "-m", "gcm", "--cli", "--help"],
                             cwd=ROOT, text=True, capture_output=True,
                             encoding="utf-8", errors="replace")
        self.assertIn("--dry-run", out.stdout + out.stderr,
                      "CLI 没有暴露 --dry-run（agent 无法在执行前预览）")


if __name__ == "__main__":
    unittest.main()
