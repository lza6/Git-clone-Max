"""G81 / P2-C（N-4）：changelog 碎片机制的守卫。

背景：`gcm/__init__.py` 曾同时承载「版本号 + 76 条 changelog」——150/163 行是
changelog 文本、最长单条 1,347 字符、被 88/320 次提交触碰（27.5%），任何改动都
容易在同一处冲突。拆分后：
- `gcm/__init__.py` 只保留版本契约（`__version__`）
- `gcm/_changelog_data.py` 承载条目（生成文件）
- `changelog.d/*.md` 承载待发布碎片
- `scripts/changelog.py` 负责 new / build / check

本守卫冻结四件事：
1. 对外 API 不变：`gcm.__changelog__` 仍是 `tuple[str, ...]`（老消费者不破）；
2. 单一来源：`__changelog__` 必须就是 `_changelog_data.ENTRIES`，不能另抄一份；
3. 版本对齐：首条必须以当前 `__version__` 开头（改版本却忘写 changelog 会红）；
4. 碎片格式：`<slug>.<category>.md`，category 必须在白名单内。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRAGMENT_DIR = ROOT / "changelog.d"
CATEGORIES = ("added", "changed", "fixed", "internal")
_FRAGMENT_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]*\.(" + "|".join(CATEGORIES) + r")\.md$")


class TestChangelogPublicAPI(unittest.TestCase):
    """拆分后对外契约必须一字不差（`publish_release.py` 依赖它遍历）。"""

    def test_is_tuple_of_str(self) -> None:
        import gcm

        self.assertIsInstance(gcm.__changelog__, tuple,
                              "__changelog__ 必须仍是 tuple（消费方按 tuple 用）")
        self.assertTrue(all(isinstance(x, str) for x in gcm.__changelog__))

    def test_single_source_no_duplicate(self) -> None:
        """不得在 `__init__.py` 里再抄一份字面量条目。"""
        import gcm
        from gcm._changelog_data import ENTRIES

        self.assertIs(gcm.__changelog__, ENTRIES,
                      "__changelog__ 必须就是 ENTRIES 本身（同源，不能复制）")

    def test_init_file_is_small(self) -> None:
        """`__init__.py` 只放版本契约 —— 回涨说明有人把 changelog 塞回来了。"""
        n = len((ROOT / "gcm" / "__init__.py").read_text(
            encoding="utf-8").splitlines())
        self.assertLess(n, 40,
                        f"gcm/__init__.py 已有 {n} 行；changelog 应放 "
                        "changelog.d/ + _changelog_data.py，不要塞回这里")

    def test_first_entry_matches_version(self) -> None:
        import gcm

        self.assertTrue(
            gcm.__changelog__[0].startswith(f"{gcm.__version__}: "),
            f"首条 changelog 必须以 {gcm.__version__!r} 开头（当前："
            f"{gcm.__changelog__[0][:40]!r}…）。发版后要 build 出对应条目。")


class TestFragmentFormat(unittest.TestCase):
    def test_fragment_names_follow_convention(self) -> None:
        if not FRAGMENT_DIR.is_dir():
            self.skipTest("changelog.d/ 尚不存在")
        for p in FRAGMENT_DIR.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            self.assertRegex(
                p.name, _FRAGMENT_RE,
                f"碎片 {p.name} 不合规：应为 <slug>.<{'|'.join(CATEGORIES)}>.md")

    def test_fragments_have_title_line(self) -> None:
        if not FRAGMENT_DIR.is_dir():
            self.skipTest("changelog.d/ 尚不存在")
        for p in FRAGMENT_DIR.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            text = p.read_text(encoding="utf-8")
            first = next((ln for ln in text.splitlines() if ln.strip()), "")
            self.assertTrue(first.startswith("# "),
                            f"{p.name} 第一条非空行必须是 '# <标题>'")

    def test_no_stray_txt_left_in_root(self) -> None:
        """碎片目录里只应有 .md（防止把模板/临时文件留在里面）。"""
        if not FRAGMENT_DIR.is_dir():
            self.skipTest("changelog.d/ 尚不存在")
        bad = [p.name for p in FRAGMENT_DIR.iterdir()
               if p.is_file() and p.suffix.lower() not in (".md", ".gitkeep")]
        self.assertEqual(bad, [], f"changelog.d/ 里有非 .md 文件：{bad}")


class TestChangelogScript(unittest.TestCase):
    """`scripts/changelog.py check` 必须能跑通（它是发布流程的一环）。"""

    def test_check_exit_code_zero(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "changelog.py"), "check"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(
            proc.returncode, 0,
            f"changelog check 失败：\nstdout={proc.stdout}\nstderr={proc.stderr}")

    def test_build_rejects_bad_version(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "changelog.py"),
             "build", "--version", "not-a-version"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(proc.returncode, 2,
                         "非法版本号必须以退出码 2 拒绝（不产生副作用）")


if __name__ == "__main__":
    unittest.main()
