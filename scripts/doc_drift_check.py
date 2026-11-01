#!/usr/bin/env python
"""文档数字漂移检测（Rev7 C1-4）。

**要解决的问题**（真事，不是假设）：v9.11.0 时 `CLAUDE.md` §6 与
`docs/architecture.md` §4 的模块行数表**六处同时过期**（main_window 2658/2790 实测 2791；
service 1768 实测 1899；engine 1169 实测 1402；progress_table 799 实测 790；
repo_db 799 实测 793；settings_panel 779 实测 798），`CLAUDE.md` 写「14 个函数超 100 行」
实测 20 个，`docs/慢测定位.md` 写「116 个测试文件 / 1105 用例」实测 159/1675，
`README.md` 写「5 个技能包」实际 6 个。**这些漂移没有任何测试会红。**

`CLAUDE.md` §0 自己写着「过期文档比没有文档贵」。这个脚本就是让那句话可执行。

检查项（每一项都必须能单独报错并给出**该改成什么**）：
1. 模块行数表：`CLAUDE.md` §6 与 `docs/architecture.md` §4 里声明的行数 vs 实测（容差 ±5）；
2. 超 100 行函数数：`CLAUDE.md` 声称值 vs 实测（精确）；
3. 测试文件数：`README.md` / `CLAUDE.md` / `tests/test_g79_repo_hygiene._EXPECTED_TEST_FILES` 三处 = 实测；
4. 技能包数：`README.md` 声称值 vs `skills/gcm-*/` 实际目录数；且每个技能包必须有 `SKILL.md`；
5. 版本号：`README.md` 的「当前版本：vX.Y.Z」= `gcm.__version__`。

用法：
    python scripts/doc_drift_check.py           # 只报告，有漂移则退出码 1
    python scripts/doc_drift_check.py --verbose # 同时打印通过项

退出码：0 = DRIFT 0；1 = 有漂移；2 = 脚本自身无法解析文档（需人工修脚本）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# 窄编码终端（中文 Windows 的 cp936）下 `✓/✗` 会抛 UnicodeEncodeError ——
# 而本脚本唯一的用途就是在**有漂移时**把这些行打出来（实测过：那一刻以 traceback 结束）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import dev_metrics  # noqa: E402  （同目录脚本，复用同一套度量口径）

LINE_TOLERANCE = 5

# 文档里用 `文件名` **N** 或表格 | `路径` | N | 声明的模块行数。
# 只检查这两个文件的这两节 —— 其它文档不声称行数。
_CLAUDE_SECTION_START = "## 6. 已知边界"
_ARCH_SECTION_START = "## 4. 模块规模基线"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _actual_lines() -> dict[str, int]:
    return dev_metrics.line_counts()


def _resolve(name: str, actual: dict[str, int]) -> tuple[str | None, str]:
    """把文档里写的模块名（可能只有 basename）映射到实际路径。

    返回 `(路径或 None, 问题描述)`。basename 有歧义时返回问题而**不是静默跳过** ——
    否则文档里写一句 `` `__init__.py` **N**`` 就会永远不被检查且不报错（审查意见）。
    """
    if name in actual:
        return name, ""
    cands = [p for p in actual if p.endswith("/" + name)]
    if len(cands) == 1:
        return cands[0], ""
    if len(cands) > 1:
        return None, (f"CLAUDE.md §6 里的 `{name}` 有 {len(cands)} 个同名文件"
                      f"（{'、'.join(sorted(cands))}），无法确定指的是哪个 → 请写全路径")
    return None, ""


def check_claude_md(actual: dict[str, int]) -> list[str]:
    """CLAUDE.md §6：`文件名` **N** 形式声明的行数。"""
    problems: list[str] = []
    text = _read("CLAUDE.md")
    start = text.find(_CLAUDE_SECTION_START)
    if start < 0:
        return ["CLAUDE.md 找不到「## 6. 已知边界」章节（脚本需同步）"]
    section = text[start:]
    seen = 0
    for m in re.finditer(r"`([A-Za-z0-9_]+\.py)`\s*\*\*(\d+)\*\*", section):
        name, claimed = m.group(1), int(m.group(2))
        path, problem = _resolve(name, actual)
        if problem:
            problems.append(problem)
            continue
        if path is None:
            continue
        seen += 1
        real = actual[path]
        if abs(real - claimed) > LINE_TOLERANCE:
            problems.append(
                f"CLAUDE.md §6：`{name}` 声称 {claimed} 行，实测 {real} 行"
                f"（容差 ±{LINE_TOLERANCE}）→ 请改成 **{real}**")
    if seen == 0:
        problems.append("CLAUDE.md §6 未解析到任何模块行数（正则失效或该节被改写）")
    return problems


def check_architecture_md(actual: dict[str, int]) -> list[str]:
    """docs/architecture.md §4：Markdown 表格 | `路径` | N | 形式。"""
    problems: list[str] = []
    text = _read("docs/architecture.md")
    start = text.find(_ARCH_SECTION_START)
    if start < 0:
        return ["docs/architecture.md 找不到「## 4. 模块规模基线」章节（脚本需同步）"]
    end = text.find("\n## ", start + 1)
    section = text[start:end if end > 0 else len(text)]
    seen = 0
    for m in re.finditer(r"^\|\s*`(gcm/[^`]+)`\s*\|\s*(\d+)\s*\|", section, re.M):
        path, claimed = m.group(1), int(m.group(2))
        if path not in actual:
            problems.append(f"docs/architecture.md §4：`{path}` 已不存在，请删掉该行")
            continue
        seen += 1
        real = actual[path]
        if abs(real - claimed) > LINE_TOLERANCE:
            problems.append(
                f"docs/architecture.md §4：`{path}` 声称 {claimed} 行，实测 {real} 行"
                f"（容差 ±{LINE_TOLERANCE}）→ 请改成 {real}")
    if seen == 0:
        problems.append("docs/architecture.md §4 未解析到任何模块行数（正则失效或表格被改写）")
    return problems


def check_long_function_count() -> list[str]:
    """CLAUDE.md §6：「N 个函数超 100 行」（允许 `**N 个**` 这类 Markdown 强调）。"""
    text = _read("CLAUDE.md")
    m = re.search(r"(\d+)\s*个\*{0,2}\s*函数超\s*\*{0,2}\s*100\s*行", text)
    if not m:
        return ["CLAUDE.md 未声明「N 个函数超 100 行」（该句被改写？）"]
    claimed = int(m.group(1))
    real = len(dev_metrics.long_functions())
    if claimed != real:
        return [f"CLAUDE.md §6：声称 {claimed} 个函数超 100 行，实测 {real} 个 → 请改成 {real}"]
    return []


def check_test_file_count() -> list[str]:
    """README / CLAUDE.md / 守卫常量 三处 = 实测。"""
    problems: list[str] = []
    real = len(list((ROOT / "tests").glob("test_*.py")))
    readme = re.search(r"tests/.*?（\s*(\d+)\s*个测试文件", _read("README.md"))
    if readme is None:
        problems.append("README.md 找不到「tests/ …（N 个测试文件）」（该句被改写？）")
    elif int(readme.group(1)) != real:
        problems.append(
            f"README.md：声称 {readme.group(1)} 个测试文件，实测 {real} → 请改成 {real}")
    claude = re.search(r"tests/.*?（\s*(\d+)\s*个测试文件", _read("CLAUDE.md")) \
        or re.search(r"tests/\s*测试（\s*(\d+)\s*文件）", _read("CLAUDE.md"))
    if claude is None:
        problems.append("CLAUDE.md 找不到测试文件数声明（该句被改写？）")
    elif int(claude.group(1)) != real:
        problems.append(
            f"CLAUDE.md：声称 {claude.group(1)} 个测试文件，实测 {real} → 请改成 {real}")
    guard = re.search(r"_EXPECTED_TEST_FILES\s*=\s*(\d+)",
                      _read("tests/test_g79_repo_hygiene.py"))
    if guard is None:
        problems.append("test_g79_repo_hygiene：找不到 _EXPECTED_TEST_FILES（守卫被改名？）")
    elif int(guard.group(1)) != real:
        problems.append(
            f"tests/test_g79_repo_hygiene._EXPECTED_TEST_FILES = {guard.group(1)}，实测 {real}"
            f" → 请改成 {real}")
    return problems


def check_skills() -> list[str]:
    """README 声称的技能包数 vs `skills/gcm-*/` 实际目录数；每个必须有 SKILL.md。"""
    problems: list[str] = []
    dirs = sorted(p for p in (ROOT / "skills").glob("gcm-*") if p.is_dir())
    for d in dirs:
        if not (d / "SKILL.md").is_file():
            problems.append(f"skills/{d.name}/ 缺 SKILL.md（技能包必须自包含）")
    m = re.search(r"skills/`?\s*提供\s*\**\s*(\d+)\s*个开箱技能包", _read("README.md"))
    if m is None:
        problems.append("README.md 找不到「skills/ 提供 N 个开箱技能包」（该句被改写？）")
    elif int(m.group(1)) != len(dirs):
        problems.append(
            f"README.md：声称 {m.group(1)} 个技能包，实际 {len(dirs)} 个 "
            f"（{'、'.join(d.name for d in dirs) if isinstance(dirs, list) else dirs}）"
            f" → 请改成 {len(dirs)}")
    return problems


def check_schema_version() -> list[str]:
    """`docs/data-model.md` 声称的 schema 版本 = `repo_db._SCHEMA_VERSION`。

    C3-1 实测教训：代码从 v5 涨到 v6 时，`data-model.md` 还写着 **4** —— 两轮都没人发现，
    因为没有任何守卫盯这个数字。数据库迁移链是**最难回溯**的一类漂移（用户库已经改了，
    文档却说是旧结构）。
    """
    import re as _re
    code = (ROOT / "gcm/db/repo_db.py").read_text(encoding="utf-8")
    m_code = _re.search(r"^_SCHEMA_VERSION\s*=\s*(\d+)", code, _re.M)
    if not m_code:
        return ["gcm/db/repo_db.py 里找不到 _SCHEMA_VERSION（脚本需同步）"]
    real = int(m_code.group(1))
    doc = (ROOT / "docs/data-model.md").read_text(encoding="utf-8")
    m_doc = _re.search(r"schema 版本[^\n]*?\*\*(\d+)\*\*", doc)
    if not m_doc:
        return ["docs/data-model.md 找不到「schema 版本 = **N**」声明（脚本需同步）"]
    if int(m_doc.group(1)) != real:
        return [f"docs/data-model.md：schema 版本写的是 v{m_doc.group(1)}，"
                f"实测 _SCHEMA_VERSION={real} → 请改成 **{real}** 并补迁移链条目"]
    return []


def check_version() -> list[str]:
    """README「当前版本：vX.Y.Z」= gcm.__version__。"""
    real = dev_metrics.version()
    m = re.search(r"当前版本[：:]\s*\**v?(\d+\.\d+\.\d+)", _read("README.md"))
    if m is None:
        return ["README.md 找不到「当前版本：vX.Y.Z」（该句被改写？）"]
    if m.group(1) != real:
        return [f"README.md：当前版本 {m.group(1)}，实际 __version__ = {real} → 请改成 {real}"]
    return []


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv
    try:
        actual = _actual_lines()
    except Exception as e:  # pragma: no cover - 目录结构被改坏
        print(f"无法读取 gcm/ 模块行数：{e}")
        return 2
    checks = [
        ("CLAUDE.md 模块行数", lambda: check_claude_md(actual)),
        ("architecture.md 模块行数", lambda: check_architecture_md(actual)),
        ("超 100 行函数数", check_long_function_count),
        ("测试文件数（三处）", check_test_file_count),
        ("技能包数", check_skills),
        ("版本号", check_version),
        ("数据库 schema 版本", check_schema_version),
    ]
    problems: list[str] = []
    for label, fn in checks:
        got = fn()
        if got:
            problems.extend(got)
        elif verbose:
            print(f"  ✓ {label}")
    if problems:
        print("文档漂移：")
        for p in problems:
            print(f"  ✗ {p}")
        print(f"\nDRIFT {len(problems)}")
        return 1
    print("DRIFT 0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
