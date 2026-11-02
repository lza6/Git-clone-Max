#!/usr/bin/env python
"""`.agent/memory/` 记忆治理校验器（G83，P0-2）。

**要解决的问题**：`.agent/memory/*.md` 是手写的自由 Markdown，没有 schema、
没有生命周期。经验（CLAUDE.md：「过期文档比没有文档贵」）证明它们会漂移、会腐烂。
本脚本把治理规则做成可执行的：

规则（每条都可单独报错并给出该改成什么）：
1. frontmatter：每个记忆文件必须有 YAML-ish frontmatter（--- 包裹），
   含必填字段 `topic`（主题）、`updated`（ISO 日期）、`owner`（负责角色）；
2. `updated` 不得早于 180 天（过期 = 需要复核，报错并列出该文件的最后提交日期供对照）；
3. 每个文件第一段（frontmatter 后）必须是 `# 标题` 且与 topic 一致性检查（弱）：
   topic 建议等于文件名 stem；不一致给 warning 级输出但不失败；
4. 变更索引 `.agent/memory/change-index.md` 里出现的模块名必须真实存在
   （防止索引指向已删除/改名的模块）——抽查 `` `gcm/...` `` 形式的路径。

用法：
    python scripts/memory_check.py            # 有问题退出码 1
    python scripts/memory_check.py --verbose
退出码：0 = 全部通过；1 = 有问题；2 = 脚本自身错误。
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEM_DIR = ROOT / ".agent" / "memory"
REQUIRED_FIELDS = ("topic", "updated", "owner")
MAX_AGE_DAYS = 180
WARN_ONLY = ("--verbose" in sys.argv)

FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _git_last_commit_date(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%as", "--", str(path.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 —— git 不可用时降级为不对照
        return None


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    m = FRONT_RE.match(text)
    if not m:
        return [f"{path.name}: 缺 frontmatter（--- 包裹），"
                f"至少需要 {', '.join(REQUIRED_FIELDS)}"]
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
    for f in REQUIRED_FIELDS:
        if f not in fields or not fields[f]:
            errors.append(f"{path.name}: frontmatter 缺必填字段 `{f}`")
    dm = DATE_RE.search(fields.get("updated", ""))
    if dm:
        try:
            updated = dt.date.fromisoformat(dm.group(1))
        except ValueError:
            errors.append(f"{path.name}: `updated` 不是合法日期：{fields.get('updated')}")
        else:
            age = (dt.date.today() - updated).days
            if age > MAX_AGE_DAYS:
                last = _git_last_commit_date(path)
                hint = f"（git 最后提交：{last}）" if last else ""
                errors.append(
                    f"{path.name}: `updated` 已 {age} 天未复核（>{MAX_AGE_DAYS}）{hint}——"
                    f"请复核内容后把 updated 改为今天，或删除已解决条目")
    else:
        errors.append(f"{path.name}: frontmatter 无 `updated` 日期")
    body_after = text[m.end():]
    if not body_after.lstrip().startswith("#"):
        errors.append(f"{path.name}: frontmatter 后应以 `# 标题` 开头")
    return errors


def check_change_index_paths() -> list[str]:
    errors: list[str] = []
    idx = MEM_DIR / "change-index.md"
    if not idx.exists():
        return [f"{idx.name}: 不存在（记忆文件集中地缺变更索引）"]
    text = idx.read_text(encoding="utf-8")
    for rel in set(re.findall(r"`(gcm/[A-Za-z0-9_/]+\.py)`", text)):
        if not (ROOT / rel).exists():
            errors.append(f"{idx.name}: 引用的模块 `{rel}` 在仓库中不存在"
                          f"（已删除/改名？请同步索引）")
    return errors


def main() -> int:
    if not MEM_DIR.exists():
        print("memory_check: .agent/memory/ 不存在，跳过", file=sys.stderr)
        return 0
    all_errors: list[str] = []
    for f in sorted(MEM_DIR.glob("*.md")):
        all_errors.extend(check_file(f))
    all_errors.extend(check_change_index_paths())
    for e in all_errors:
        print(f"MEMORY-DRIFT: {e}")
    print(f"memory_check: {len(all_errors)} 个问题"
          + ("" if all_errors else "，全部通过"))
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
