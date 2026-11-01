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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CJK = re.compile(r"[\u4e00-\u9fff]")


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
        if "i18n" in p.as_posix():
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
    problems = check()
    zh = _dict(ROOT / "gcm/i18n/zh.py", "ZH")
    used = _used()
    if "--verbose" in argv:
        print(f"ZH={len(zh)} 使用中的字面量键={len(used)}")
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
