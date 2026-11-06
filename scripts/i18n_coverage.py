#!/usr/bin/env python
"""i18n 覆盖检查：每个 `tr("字面量")` 键必须同时存在于 zh 与 en，且英文真的翻译过。

**为什么需要**（Rev7 X-5）：`gcm/i18n/{zh,en}.py` 标注为「生成文件」，但生成脚本
从来没进仓库。于是新增 `tr("...")` 时没人重跑生成 —— 实测有 **72 个键**在用却不在字典里，
`tr()` 的兜底是「返回 key 本身」，所以在**英文模式下这 72 处直接显示中文**。
更糟的是 ENTRIES 对称（ZH/EN 都是 494 键），看起来"没问题"。

判定（三条，都能被反例证伪）：
1. 代码里所有 `tr("字面量")` 的键必须 ∈ ZH，且 ∈ EN；
2. ZH 与 EN 的键集合必须完全相等（不能只改一本）；
3. 含中文的键，其 EN 值不得再含中文（否则就是「占位未翻译」，等价于回退）。

用法：
    python scripts/i18n_coverage.py            # 只报告；有问题退出码 1
    python scripts/i18n_coverage.py --verbose  # 打印统计
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CJK = re.compile(r"[\u4e00-\u9fff]")
#: Rev14 R14-18\uff1a\u672a\u8d70 `tr()` \u7684\u4e2d\u6587\u53ef\u89c1\u5b57\u9762\u91cf\u7684\u57fa\u7ebf\u6587\u4ef6\uff08\u8ba1\u6570\u53ea\u964d\u4e0d\u5347\uff09\u3002
BASELINE = ROOT / "scripts" / "i18n_baseline.json"


def _dict(path: Path, name: str) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return {k.value: v.value for k, v in zip(node.value.keys, node.value.values)
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}
    return {}


def _used() -> dict[str, str]:
    """{key: 首个出现位置}，只认 tr() 的**字面量**参数（f-string / 变量无法静态检查）。"""
    out: dict[str, str] = {}
    for p in sorted((ROOT / "gcm").rglob("*.py")):
        # `gcm/i18n/` 是字典本体；`_changelog_data.py` 是**发布说明**（中文是刻意的，
        # 不进 UI、不切英文）。棘轮盯的是"用户可见文案"，这两类不该混进分母。
        if "i18n" in p.as_posix() or p.name == "_changelog_data.py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "tr"
                    and n.args and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                out.setdefault(n.args[0].value,
                               f"{p.relative_to(ROOT).as_posix()}:{n.lineno}")
    return out


def untranslated() -> dict[str, int]:
    """未走 `tr()` 的中文可见字面量计数（按文件）。

    **为什么需要**：`_used()` 只看 `tr()` 的实参 —— 没被 `tr()` 包住的中文
    它根本看不见，于是"覆盖检查通过"与"英文模式下满屏中文"可以同时成立
    （Rev13 R12 记录的假绿）。这里从**另一头**数：每个含中文的字符串常量，
    排除 docstring、排除 `tr()` 实参，剩下的就是"哪怕切到英文也不会变"的文案。

    计入的包括日志与提示文本（日志也面向用户），不含注释（注释不是字符串常量）。
    """
    per: dict[str, int] = {}
    for p in sorted((ROOT / "gcm").rglob("*.py")):
        # 同上：字典本体与**发布说明**都不是 UI 文案
        if "i18n" in p.as_posix() or p.name == "_changelog_data.py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                if ast.get_docstring(node, clean=False) and node.body:
                    docstrings.add(id(node.body[0].value))
        tr_args: set[int] = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "tr"):
                for arg in node.args:
                    tr_args.add(id(arg))
        count = 0
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings and id(node) not in tr_args
                    and _CJK.search(node.value)):
                count += 1
        if count:
            per[p.relative_to(ROOT).as_posix()] = count
    return per


def load_baseline() -> dict:
    if not BASELINE.exists():
        return {}
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def check_untranslated() -> list[str]:
    """Rev14 R14-18 棘轮：未走 `tr()` 的中文字面量总数不得高于基线。"""
    per = untranslated()
    total = sum(per.values())
    base = load_baseline()
    if not base:
        return []
    problems: list[str] = []
    if total > int(base.get("total", 0)):
        worst = sorted(per.items(), key=lambda kv: -kv[1])[:5]
        problems.append(
            f"未走 tr() 的中文字面量 {base.get('total')} → {total}"
            f"（新增请用 tr()；确实不改请说明理由并下调基线）最多的是 {worst}")
    for fname, count in (base.get("by_file") or {}).items():
        cur = per.get(fname, 0)
        if cur > int(count):
            problems.append(f"{fname} 未翻译字面量 {count} → {cur}")
    return problems


def check() -> list[str]:
    zh = _dict(ROOT / "gcm/i18n/zh.py", "ZH")
    en = _dict(ROOT / "gcm/i18n/en.py", "EN")
    used = _used()
    problems: list[str] = []
    miss_zh = sorted(set(used) - set(zh))
    miss_en = sorted(set(used) - set(en))
    for k in miss_zh:
        problems.append(f"zh 缺键 @{used[k]}：{k!r}")
    for k in miss_en:
        problems.append(f"en 缺键 @{used[k]}：{k!r}")
    only_zh = sorted(set(zh) - set(en))
    only_en = sorted(set(en) - set(zh))
    for k in only_zh:
        problems.append(f"只有 zh 有（en 漏了）：{k!r}")
    for k in only_en:
        problems.append(f"只有 en 有（zh 漏了）：{k!r}")
    for k, v in en.items():
        if _CJK.search(k) and _CJK.search(str(v)):
            problems.append(f"en 未翻译（值仍是中文）：{k!r} -> {v!r}")
    return problems


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--update-baseline" in argv:
        per = untranslated()
        payload = {"total": sum(per.values()),
                   "by_file": dict(sorted(per.items(), key=lambda kv: -kv[1]))}
        BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"i18n 基线已更新：总数 {payload['total']} → "
              f"{BASELINE.relative_to(ROOT)}")
        return 0
    problems = check() + check_untranslated()
    zh = _dict(ROOT / "gcm/i18n/zh.py", "ZH")
    used = _used()
    if "--verbose" in argv:
        per = untranslated()
        print(f"ZH={len(zh)} 使用中的字面量键={len(used)} "
              f"未走 tr() 的中文字面量={sum(per.values())}")
    if problems:
        print(f"i18n 覆盖问题 {len(problems)} 条：")
        for p in problems[:40]:
            print("  -", p)
        if len(problems) > 40:
            print(f"  …（其余 {len(problems) - 40} 条省略）")
        return 1
    print("I18N OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
