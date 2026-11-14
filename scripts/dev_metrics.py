#!/usr/bin/env python
"""一次性打印《下一步改进指南》§1.3/§1.4 与各文档声称的全部度量数字。

**为什么需要它**（Rev7 C1-13）：本项目已经因为「文档写了就信」付过两次代价 ——
`计划书/` 被以「全部闭环」删除后又被独立复验推翻；`docs/architecture.md` /
`CLAUDE.md` 的模块行数表在多轮迭代后**六处同时过期**，而没有任何测试会因此变红。
这个脚本的作用是：把「文档说多少」变成「一条命令只读地量出来多少」。

用法：
    python scripts/dev_metrics.py            # 人类可读
    python scripts/dev_metrics.py --json     # 给 doc_drift_check.py / CI 用

它**只读**：不改任何文件、不连网、不建库。行数统计口径与
`tests/test_g79_repo_hygiene.py` 一致（`splitlines()`，与 `wc -l` 相同）。
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 与 test_g79_repo_hygiene 同口径：这些模块**已被显式接受**超 800 行。
ACCEPTED_OVERSIZED = {
    "gcm/ui/main_window.py",
    "gcm/git/service.py",
    "gcm/app/engine.py",
    # i18n 字典是生成文件（key 即中文、一行一条），行数随文案量线性增长；
    # `tests/test_g79_repo_hygiene` 已登记，这里必须同口径。
    "gcm/i18n/zh.py",
    "gcm/i18n/en.py",
}
OVERSIZED_LIMIT = 800
FUNCTION_LIMIT = 100

# B3-2（Rev18）：纯文档技能 —— 工作流检查清单类，没有可执行验收物，
# 缺 scripts/ 不是缺陷（SKILL.md 顶部已声明）。新增纯文档技能时在此登记。
PURE_DOC_SKILLS = {"gcm-feature"}


def py_files(sub: str) -> list[Path]:
    return sorted(p for p in (ROOT / sub).rglob("*.py") if "__pycache__" not in p.parts)


def line_counts() -> dict[str, int]:
    return {p.relative_to(ROOT).as_posix(): len(p.read_text(encoding="utf-8").splitlines())
            for p in py_files("gcm")}


def long_functions() -> list[tuple[int, str, str]]:
    """返回 [(行数, 文件, 函数名)]，只统计模块/类顶层函数（缩进 ≤4），与守卫同口径。"""
    out: list[tuple[int, str, str]] = []
    for p in py_files("gcm"):
        lines = p.read_text(encoding="utf-8").splitlines()
        cur, start = None, 0
        for i, line in enumerate(lines, 1):
            m = re.match(r"^(\s*)(?:async\s+)?def\s+(\w+)", line)
            if m and len(m.group(1)) <= 4:
                if cur and i - start > FUNCTION_LIMIT:
                    out.append((i - start, p.relative_to(ROOT).as_posix(), cur))
                cur, start = m.group(2), i
        if cur and len(lines) + 1 - start > FUNCTION_LIMIT:
            out.append((len(lines) + 1 - start, p.relative_to(ROOT).as_posix(), cur))
    out.sort(reverse=True)
    return out


def except_budget() -> dict[str, int]:
    """except 子句总数 / 其中 pass-only 数（守卫 test_g80_hygiene 用同一口径）。"""
    total = only_pass = 0
    for p in py_files("gcm"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:          # pragma: no cover - 语法错由 ruff/mypy 先拦
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                total += 1
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    only_pass += 1
    return {"except_total": total, "except_pass_only": only_pass}


def version() -> str:
    text = (ROOT / "gcm/__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def collect() -> dict:
    sizes = line_counts()
    overs = sorted(p for p, n in sizes.items() if n > OVERSIZED_LIMIT)
    near = sorted(((n, p) for p, n in sizes.items() if 500 < n <= OVERSIZED_LIMIT),
                  reverse=True)
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    skills = sorted(p for p in (ROOT / "skills").glob("gcm-*") if p.is_dir())
    data = {
        "version": version(),
        "gcm_py_files": len(sizes),
        "gcm_total_lines": sum(sizes.values()),
        "module_lines": sizes,
        "oversized": overs,
        "oversized_accepted": sorted(ACCEPTED_OVERSIZED),
        "oversized_set_matches": overs == sorted(ACCEPTED_OVERSIZED),
        "near_limit": [{"lines": n, "path": p} for n, p in near],
        "long_functions_count": len(long_functions()),
        "long_functions_top": [
            {"lines": n, "path": p, "func": f} for n, p, f in long_functions()[:5]],
        "test_files": len(tests),
        "test_total_lines": sum(
            len(p.read_text(encoding="utf-8", errors="replace").splitlines()) for p in tests),
        "skills": [p.name for p in skills],
        "skills_count": len(skills),
        "skills_without_scripts": [
            p.name for p in skills
            if not (p / "scripts").is_dir() and p.name not in PURE_DOC_SKILLS],
        "pure_doc_skills": sorted(PURE_DOC_SKILLS),
    }
    data.update(except_budget())
    return data


def main(argv: list[str]) -> int:
    data = collect()
    if "--json" in argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"__version__          = {data['version']}")
    print(f"gcm .py 文件数        = {data['gcm_py_files']}   总行数 = {data['gcm_total_lines']}")
    print(f"超 {OVERSIZED_LIMIT} 行模块（{len(data['oversized'])}）: {data['oversized']}")
    print(f"  与登记集合一致?      = {data['oversized_set_matches']}")
    print("500-800 行（贴着红线，下一个功能就可能越界）：")
    for row in data["near_limit"]:
        print(f"  {row['lines']:5d}  {row['path']}")
    print(f"超 {FUNCTION_LIMIT} 行函数数   = {data['long_functions_count']}（前 5："
          f"{[(r['lines'], r['func']) for r in data['long_functions_top']]}")
    print(f"except 子句 / pass-only = {data['except_total']} / {data['except_pass_only']}")
    print(f"tests/ test_*.py     = {data['test_files']}   总行数 = {data['test_total_lines']}")
    print(f"skills/ 技能包        = {data['skills_count']} {data['skills']}")
    print(f"  纯文档技能（无脚本为设计）= {data['pure_doc_skills']}")
    print(f"  缺 scripts/ 的        = {data['skills_without_scripts']}")
    print("\n（覆盖率与快集用例数需另跑，见指南 §1.2：")
    print("  coverage run --source=gcm scripts/ci_run_fast.py && coverage report）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
