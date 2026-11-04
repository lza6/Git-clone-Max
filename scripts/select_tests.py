"""改哪块 → 必跑哪些测试（G84-14）。

## 解决的问题
`test_ledger.md` 的 61 个接缝是**手工维护**的，"改了 X 要跑哪些测试"散落在
`CLAUDE.md` 与人的记忆里。漏跑一个的结果是：绿灯发布、线上回归。
本脚本用**静态 import 图**把这件事变成可执行命令：

    python scripts/select_tests.py --changed-from-git     # CI/本地：按 git diff 选
    python scripts/select_tests.py gcm/db/settings.py     # 指定文件

输出三行（可被脚本消费）：

    tests=test_g84_settings_safety,test_g67_closure     # 必跑
    untested=gcm/ui/radar_chart.py                      # 在爆炸半径里但没有任何测试 import 它
    run_unknown=                                        # 推不出来时非空 —— "猜一个比没有更糟"

## 诚实性（照抄 ripwire 的纪律）
- 只看**静态 import**：`getattr`、字符串式导入、Qt 信号槽、QTimer 回调都看不见。
  所以 `untested=` 是"没找到证据"，不是"确定没测"。脚本把这一点写进输出，不装全知。
- 推不出任何测试时**不虚构**，直接给 `run_unknown=1` 让人自己决定。
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "gcm"


def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, mod: str) -> set[str]:
    """解析一个文件对 gcm 内部模块的引用（含函数内惰性 import）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return set()
    is_pkg = path.name == "__init__.py"
    package = mod if is_pkg else mod.rsplit(".", 1)[0]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "gcm" or a.name.startswith("gcm."):
                    out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".")
                up = node.level - 1
                if up:
                    base_parts = base_parts[:-up] if up <= len(base_parts) else []
                base = ".".join(base_parts)
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            if not (target == "gcm" or target.startswith("gcm.")):
                continue
            out.add(target)
            for a in node.names:
                out.add(f"{target}.{a.name}")
    return out


def build_graph() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """返回 (模块→它 import 的模块, 测试文件→它 import 的模块)。"""
    gcm_edges: dict[str, set[str]] = {}
    for p in PGK_FILES():
        gcm_edges[module_name(p)] = _imports(p, module_name(p))
    test_edges: dict[str, set[str]] = {}
    for p in sorted((ROOT / "tests").glob("test_*.py")):
        test_edges[p.stem] = _imports(p, f"tests.{p.stem}")
    return gcm_edges, test_edges


def PGK_FILES() -> list[Path]:
    return [p for p in sorted(PKG.rglob("*.py")) if "__pycache__" not in p.parts]


def affected_modules(changed: set[str], gcm_edges: dict[str, set[str]]) -> set[str]:
    """从变更模块出发，沿**反向 import** 扩散，得到整条爆炸半径。"""
    reverse: dict[str, set[str]] = {}
    for mod, deps in gcm_edges.items():
        for d in deps:
            reverse.setdefault(d, set()).add(mod)
    seen = set(changed)
    stack = list(changed)
    while stack:
        cur = stack.pop()
        for importer in reverse.get(cur, set()):
            if importer not in seen:
                seen.add(importer)
                stack.append(importer)
    return seen


def select(changed_files: list[str]) -> dict[str, list[str]]:
    gcm_edges, test_edges = build_graph()
    changed_mods = set()
    for rel in changed_files:
        rel = rel.replace("\\", "/")
        if rel.startswith("gcm/") and rel.endswith(".py"):
            changed_mods.add(module_name(ROOT / rel))
    if not changed_mods:
        # 改的是测试/文档/脚本：没有 gcm 模块进图 → 交给调用方决定
        return {"tests": [], "untested": [], "run_unknown": ["没有 gcm/*.py 变更"]}

    affected = affected_modules(changed_mods, gcm_edges)
    tests: list[str] = []
    covered: set[str] = set()
    for test, deps in test_edges.items():
        hit = deps & affected
        if hit:
            tests.append(test)
            covered |= hit
    untested = sorted(m for m in affected if m not in covered)
    out: dict[str, list[str]] = {"tests": sorted(tests), "untested": untested,
                                 "run_unknown": []}
    if not tests:
        out["run_unknown"] = ["爆炸半径内没有任何测试 import（要么补测试，要么人工确认）"]
    return out


def changed_from_git(base: str | None) -> list[str]:
    cmd = ["git", "diff", "--name-only"]
    cmd.append(f"{base}..." if base else "HEAD")
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True,
                       encoding="utf-8", errors="replace")
    return [line for line in r.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按变更选出必跑测试（静态 import 图）")
    ap.add_argument("files", nargs="*", help="变更文件（默认按 git diff HEAD）")
    ap.add_argument("--changed-from-git", action="store_true", help="从 git diff 取变更")
    ap.add_argument("--base", default=None, help="git diff 的基线（如 origin/main）")
    ap.add_argument("--strict", action="store_true",
                    help="有 run_unknown 或 untested 时以 4 退出（CI 用）")
    a = ap.parse_args(argv)

    files = a.files or (changed_from_git(a.base) if a.changed_from_git
                        else changed_from_git(None))
    res = select(files)
    print("tests=" + ",".join(res["tests"]))
    print("untested=" + ",".join(res["untested"]))
    print("run_unknown=" + ";".join(res["run_unknown"]))
    print("# 口径：仅静态 import 图；getattr/字符串导入/Qt 信号槽不可见 —— "
          "untested 表示「没有证据」，不是「确定没测」")
    if a.strict and (res["untested"] or res["run_unknown"]):
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
