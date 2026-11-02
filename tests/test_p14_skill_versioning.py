"""Rev8 P1-4 守卫：技能包可安装化（version + selftest）。

两条不变量：
1. 每个 `skills/*/SKILL.md` 的 YAML frontmatter 必须有 `version`（可安装化的
   最低要求；没有版本就无法判断"装的是哪版"）。
2. 每个带 `scripts/` 的技能包，脚本必须支持 `--selftest`（自校验入口，装完
   能一键证明自己可用 —— 与 skills/G56-1-验收说明.md 的遗留风险呼应：打包
   exe 环境下 `sys.executable` 不可靠，selftest 必须自带解释器探测）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"


def _skill_dirs() -> list[Path]:
    return sorted(d for d in SKILLS.iterdir() if d.is_dir() and (d / "SKILL.md").exists())


class TestSkillVersion(unittest.TestCase):
    def test_every_skill_has_version(self):
        for d in _skill_dirs():
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            fm = text.split("---", 2)[1] if text.startswith("---") else ""
            self.assertRegex(
                fm, r"(?m)^version:\s*\S+", f"{d.name}/SKILL.md 缺 version 字段")

    def test_versions_are_semver_like(self):
        for d in _skill_dirs():
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            fm = text.split("---", 2)[1] if text.startswith("---") else ""
            m = re.search(r"(?m)^version:\s*(\S+)", fm, re.M)
            if m:
                self.assertRegex(m.group(1), r"^\d+\.\d+\.\d+$",
                                 f"{d.name} version 非 semver：{m.group(1)}")


class TestSelftest(unittest.TestCase):
    def test_scripts_support_selftest(self):
        for d in _skill_dirs():
            scripts = d / "scripts"
            if not scripts.is_dir():
                continue  # 纯文档技能（如 gcm-feature）不要求
            for py in scripts.glob("*.py"):
                if py.name.startswith("__"):
                    continue
                src = py.read_text(encoding="utf-8", errors="replace")
                self.assertIn("--selftest", src,
                              f"{py.relative_to(ROOT)} 不支持 --selftest")


class TestSelftestRuns(unittest.TestCase):
    """真跑一个 selftest（真实闭环，不只看字符串存在）。"""

    def test_clone_skill_selftest_passes(self):
        import subprocess
        r = subprocess.run(
            [str(ROOT / ".venv/Scripts/python.exe"),
             str(ROOT / "skills/gcm-clone/scripts/gcm_smoke.py"), "--selftest"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, f"selftest 失败：{r.stdout}\n{r.stderr}")


if __name__ == "__main__":
    unittest.main()
