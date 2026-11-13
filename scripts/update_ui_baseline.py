#!/usr/bin/env python
"""UI 固定基线管理（Rev16 B4）：docs/screenshots/ 是**入库的**像素基准。

背景：R15-11 遗留 —— 截图基线从未入库，`--compare` 没有可比对象，
UI 回归只能靠人眼。本脚本把「更新基线」变成一个显式动作：

- `--verify`：把 docs/screenshots/ 与本次新截图（reports/<dir>）逐张比，
  有回归退出码 1（CI/验收流水线用）。
- `--update`：把新截图拷成新基线（要求新截图目录里**至少包含**现有基线的
  全部同名文件，防止基线悄悄缩水）；成功后打印每张图，供验证记录留痕。

口径（沿 scripts/pixel_diff.py）：单通道差 >8 记"变了"，占比 ≤0.5% 通过；
尺寸不同直接失败；缺口失败。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "docs" / "screenshots"


def _compare(baseline: Path, current: Path) -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import pixel_diff
    res = pixel_diff.compare_dirs(baseline, current)
    print("基线比对：", "通过" if res["ok"] else "有回归")
    for name in res.get("missing") or []:
        print(f"  缺失（基线有、本次没截到）：{name}")
    for line in res.get("regressions") or []:
        print(f"  变化：{line}")
    return 0 if res["ok"] else 1


def _update(baseline: Path, current: Path) -> int:
    if not current.exists():
        print(f"新截图目录不存在：{current}")
        return 2
    cur = {p.name for p in current.glob("*.png")}
    base = {p.name for p in baseline.glob("*.png")} if baseline.exists() else set()
    missing = sorted(base - cur)
    if missing:
        print(f"拒绝更新：新截图缺 {len(missing)} 张既有基线图（基线不能缩水）：")
        for name in missing:
            print(f"  {name}")
        return 1
    baseline.mkdir(parents=True, exist_ok=True)
    for name in sorted(cur & base or cur):
        shutil.copy2(current / name, baseline / name)
    added = sorted(cur - base)
    for name in added:
        shutil.copy2(current / name, baseline / name)
    # 复核一次：新基线必须与本次截图零回归
    rc = _compare(baseline, current)
    n = len(list(baseline.glob("*.png")))
    print(f"基线已更新：{baseline}（{n} 张，共 "
          f"{sum(f.stat().st_size for f in baseline.glob('*.png'))} 字节）")
    if added:
        print(f"新增 {len(added)} 张：{', '.join(added)}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", metavar="DIR",
                    help="把 docs/screenshots/ 与 DIR 里的新截图逐张比对")
    ap.add_argument("--update", metavar="DIR",
                    help="把 DIR 里的新截图设为新基线（先做缩水检查）")
    args = ap.parse_args()
    if not args.verify and not args.update:
        ap.print_help()
        return 2
    if args.update:
        return _update(BASELINE, Path(args.update))
    return _compare(BASELINE, Path(args.verify))


if __name__ == "__main__":
    sys.exit(main())
