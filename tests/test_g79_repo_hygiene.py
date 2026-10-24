"""G79 仓库卫生守卫（v9.10.0 治理轮）。

背景 —— 这三类问题**都真实发生过**，而且都是「换个名字就会重演」的类型：

1. `reports/ui-audit-final/` 的 38 张 PNG（约 3.6 MB）长期在 git 索引里，
   而 `.gitignore` 只忽略了另外三批截图目录（`ui-baseline-*` / `ui-g75*` / `ui-g78*`）。
   根因是一次自称「截图基线与忽略规则收敛」的提交只收敛了其中一部分。
2. 根目录躺着 `z.png`（误放的截图，被撤销过一次又被带回）与 1 行占位的顶层 `cli.py`
   （真入口是 `gcm/cli.py`，两者容易被混淆）。`git add -A` 时它们永远在噪音里。
3. `gcm/` 的超 800 行文件清单是**被显式接受的技术债**（见 `CLAUDE.md` §6），
   但「悄悄多出第 4 个超限文件」没有任何守卫拦。

设计取舍（为什么这么写）：

- **用 `git ls-files` 而不是扫文件系统**：只有「入库」才是问题。本地留 26 MB 截图做对照是合理的，
  所以断言对象必须是索引，不是磁盘。
- **根目录用白名单而不是黑名单**：黑名单只能挡住已知的死文件，白名单要求新增顶层条目必须显式登记 ——
  登记动作本身就是一次「这真的该进仓库吗」的自问。
- **模块规模用「集合相等」而不是「小于阈值」**：`< 800` 拦不住「又多了第 4 个 801 行文件」；
  集合相等既拦新增，也拦「偷偷把某个拆掉了却没人更新登记」。
- **文档数字用「从 md 抽数 vs 实测」**：文档漂移的代价在本项目已经付过（`计划书/` 被以
  「全部闭环」删除后又被独立复验推翻）。这条守卫只覆盖最容易机械核对的「测试文件数」。

**变异实验记录（守卫有效性的唯一证据）**：见 `evidence/G79-hygiene.json`。
每个断言都必须「破坏后能红」，否则等于没写。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 允许入库的截图目录（README 展示图，体积小且被文档直接引用）
_SCREENSHOT_WHITELIST = ("docs/screenshots/",)

# 根目录允许出现的顶层条目（目录 + 文件）。新增任何一条都必须先在这里登记理由。
_ROOT_WHITELIST = {
    # 目录
    ".agent", ".github", "docs", "evidence", "gcm", "packaging",
    "reports", "scripts", "skills", "tasks", "tests",
    # 文件
    ".coveragerc", ".gitattributes", ".gitignore", ".ignore",
    "CLAUDE.md", "Git-clone-Max.spec", "LICENSE", "README.md",
    "policy.md", "pyproject.toml", "requirements-build.txt", "requirements.txt",
    "test_ledger.md", "workflow_status.md",
    # 2026-10-24 登记：参考项目对标审计的**工作流状态文件**（用户明确要求落盘在本仓根，
    # 是那次任务的单一状态源：1748 条参考项目清单 + 差距台账 + 批次计划）。
    "参考的结果计划指南.md",
}

# 被显式接受的超 800 行模块（`CLAUDE.md` §6 登记的技术债）。
# 新增成员必须同时更新 `CLAUDE.md`，否则这条断言会红 —— 这是故意的。
_ACCEPTED_OVERSIZED = {
    "gcm/ui/main_window.py",
    "gcm/git/service.py",
    "gcm/app/engine.py",
}
_OVERSIZED_LIMIT = 800

# 断言基线：测试文件数与主窗行数（`CLAUDE.md` / `README.md` 必须与实测一致）。
# 这两个数字会随开发变化，改代码时**同步改这里和文档**，守卫才有意义。
_EXPECTED_TEST_FILES = 140


def _git_ls_files() -> list[str]:
    """返回 git 索引中的文件路径（POSIX 风格，中文不转义）。"""
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"git ls-files 失败：{out.stderr[:400]}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


class TestNoScreenshotsInGit(unittest.TestCase):
    """截图产物不得入库（白名单 `docs/screenshots/` 除外）。"""

    def test_no_png_under_reports_in_index(self) -> None:
        offenders = [
            p for p in _git_ls_files()
            if p.lower().endswith(".png") and not p.startswith(_SCREENSHOT_WHITELIST)
        ]
        self.assertEqual(
            offenders, [],
            "以下截图在 git 索引里。截图可重建（scripts/ui_screenshot_audit.py <dir>），"
            "应出库并加入 .gitignore：\n  " + "\n  ".join(offenders),
        )

    def test_whitelisted_screenshots_still_tracked(self) -> None:
        """白名单目录必须真的有图 —— 防止「把白名单删空让上一条永远绿」。"""
        tracked = [p for p in _git_ls_files() if p.startswith(_SCREENSHOT_WHITELIST)]
        self.assertTrue(tracked, f"{_SCREENSHOT_WHITELIST} 下应仍有被跟踪的展示图")


class TestRootDirWhitelist(unittest.TestCase):
    """根目录顶层条目必须有登记。"""

    def test_top_level_entries_are_whitelisted(self) -> None:
        tops = {p.split("/", 1)[0] for p in _git_ls_files()}
        unexpected = sorted(tops - _ROOT_WHITELIST)
        self.assertEqual(
            unexpected, [],
            "根目录出现未登记的顶层条目。要么删掉，要么在 "
            "tests/test_g79_repo_hygiene.py::_ROOT_WHITELIST 里登记理由：\n  "
            + "\n  ".join(unexpected),
        )

    def test_whitelist_has_no_dead_entries(self) -> None:
        """白名单里的条目必须真的存在于索引 —— 防止过期豁免悄悄放宽守卫。"""
        tops = {p.split("/", 1)[0] for p in _git_ls_files()}
        dead = sorted(_ROOT_WHITELIST - tops)
        self.assertEqual(dead, [], f"白名单里这些条目在索引中已不存在，应删掉：{dead}")


class TestNoNewOversizedModules(unittest.TestCase):
    """超 800 行模块集合必须等于已登记的集合。"""

    def _line_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for path in sorted((ROOT / "gcm").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            counts[rel] = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        return counts

    def test_oversized_set_matches_registry(self) -> None:
        counts = self._line_counts()
        actual = {rel for rel, n in counts.items() if n > _OVERSIZED_LIMIT}
        self.assertEqual(
            actual, _ACCEPTED_OVERSIZED,
            "超 800 行的模块集合与登记不一致。\n"
            f"新增的（应先拆分，或在 CLAUDE.md §6 登记为已接受的技术债并更新本守卫）："
            f"{sorted(actual - _ACCEPTED_OVERSIZED)}\n"
            f"已消失的（应在 CLAUDE.md 与本守卫里移除）：{sorted(_ACCEPTED_OVERSIZED - actual)}",
        )

    def test_registry_really_exceeds_limit(self) -> None:
        """登记的每个文件必须真的超限 —— 防止白登记一个「豁免」。"""
        counts = self._line_counts()
        for rel in _ACCEPTED_OVERSIZED:
            self.assertIn(rel, counts, f"登记的模块不存在：{rel}")
            self.assertGreater(
                counts[rel], _OVERSIZED_LIMIT,
                f"{rel} 已降到 {counts[rel]} 行，应从技术债登记中移除",
            )


class TestDocsCountsMatchCode(unittest.TestCase):
    """文档声称的测试文件数必须等于实测（最易机械核对的一阶事实）。"""

    def _test_file_count(self) -> int:
        return len(list((ROOT / "tests").glob("test_*.py")))

    def test_readme_test_file_count_matches(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        m = re.search(r"tests/.*?（\s*(\d+)\s*个测试文件", text)
        self.assertIsNotNone(m, "README.md 里找不到「tests/ …（N 个测试文件）」的声明")
        self.assertEqual(
            int(m.group(1)), self._test_file_count(),
            "README.md 声称的测试文件数与实测不一致",
        )

    def test_claude_md_test_file_count_matches(self) -> None:
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        m = re.search(r"tests/\s+测试（\s*(\d+)\s*文件）", text)
        self.assertIsNotNone(m, "CLAUDE.md 里找不到「tests/ 测试（N 文件）」的声明")
        self.assertEqual(
            int(m.group(1)), self._test_file_count(),
            "CLAUDE.md 声称的测试文件数与实测不一致",
        )

    def test_expected_test_files_constant_matches(self) -> None:
        self.assertEqual(
            _EXPECTED_TEST_FILES, self._test_file_count(),
            "本守卫的基线常量过期：改了测试文件数就同步改 _EXPECTED_TEST_FILES",
        )

    def test_claude_md_main_window_lines_matches(self) -> None:
        """`CLAUDE.md` §6 登记的「主窗现值 N 行」必须等于实测（容差 ±5）。

        G79b：本文件顶部注释早就声称「断言基线：测试文件数与**主窗行数**」，但实际只实现了
        测试文件数 —— 又一处「注释比实现大」的空转风险。补齐它，并给 ±5 容差
        （只加注释/空行不算漂移，见指南 R-05）。
        """
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        m = re.search(r"main_window\.py`\s*\*\*(\d+)\*\*", text)
        self.assertIsNotNone(m, "CLAUDE.md 里找不到「main_window.py` **N**」的行数声明")
        declared = int(m.group(1))
        actual = len((ROOT / "gcm/ui/main_window.py").read_text(
            encoding="utf-8").splitlines())
        self.assertLessEqual(
            abs(declared - actual), 5,
            f"CLAUDE.md 声明主窗 {declared} 行，实测 {actual} 行（容差 ±5）",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
