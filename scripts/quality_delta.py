"""质量棘轮（G84-15）：只拦「变坏」，不要求存量达标。

## 为什么不直接设硬阈值
本项目已有三处超 800 行模块被**用户显式接受**（`main_window` / `service` / `engine`）。
再加一条"所有模块必须 <800 行"的门禁只会逼人把代码换个地方堆。所以这里用**棘轮**：

- 基线（`evidence/quality_baseline.json`）冻结当前存量；
- 只对**本次改动的文件**比对：行数明显上涨、`except` 数量上涨、或**新增**一个超限模块 → 报错；
- 允许 `--ack <key> --reason "..."` 带理由豁免，**但豁免会在该指标继续恶化后自动失效**
  （照抄 ripwire 的语义：裸 ack 会把门禁变成橡皮章）。

## 用法
    python scripts/quality_delta.py                  # 按 git diff HEAD 比对
    python scripts/quality_delta.py --update-baseline  # 基线随版本更新（发版时跑一次）
    python scripts/quality_delta.py --ack gcm/ui/x.py:lines --reason "拆分子面板中，本批 +40"
退出码：0 = 没有变坏；3 = 有恶化；2 = 基线缺失（先跑 --update-baseline）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "gcm"
BASELINE = ROOT / "evidence" / "quality_baseline.json"

#: 单文件行数上涨容差（小改动会有 ±几行的正常波动）
LINE_TOLERANCE = 20
#: 模块行数硬上限（与 tests/test_g79_repo_hygiene 的集合断言同口径）
LINE_CAP = 800


def _modules() -> list[Path]:
    return [p for p in sorted(PKG.rglob("*.py")) if "__pycache__" not in p.parts]


def _count_handlers(text: str) -> int:
    """粗数 `except` 子句（含 `except:` / `except X:` / `except (X, Y):`）。"""
    n = 0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("except") and ":" in s:
            n += 1
    return n


def snapshot() -> dict:
    per_file: dict[str, dict[str, int]] = {}
    for p in _modules():
        text = p.read_text(encoding="utf-8", errors="replace")
        per_file[p.relative_to(ROOT).as_posix()] = {
            "lines": len(text.splitlines()),
            "excepts": _count_handlers(text),
        }
    oversized = sorted(k for k, v in per_file.items() if v["lines"] > LINE_CAP)
    return {
        "generated_by": "scripts/quality_delta.py --update-baseline",
        "line_cap": LINE_CAP,
        "oversized": oversized,
        "per_file": per_file,
        "acks": {},
    }


def changed_files(base: str | None = None) -> list[str]:
    """变更文件清单；基线 ref 不可用（CI 上没有 origin/xxx）时退化为 diff HEAD。

    退化而不是报错：门禁在"取不到基线"时最该做的是**别把 job 弄红**，
    同时也不假装有结果 —— 退化成 diff HEAD 后若无改动，就是 WORSE 0。
    """
    candidates = []
    if base:
        candidates.append(f"{base}...")
    candidates.append("HEAD")
    for spec in candidates:
        r = subprocess.run(["git", "diff", "--name-only", spec], cwd=ROOT, text=True,
                           capture_output=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return [x.replace("\\", "/") for x in r.stdout.splitlines() if x.strip()]
    return []


def compare(base_data: dict, files: list[str], acks: dict[str, str]) -> list[str]:
    now = snapshot()
    problems: list[str] = []
    base_files = base_data.get("per_file", {})
    base_oversized = set(base_data.get("oversized", []))

    for rel in files:
        if not rel.startswith("gcm/") or not rel.endswith(".py"):
            continue
        cur = now["per_file"].get(rel)
        if cur is None:
            continue                     # 删掉的模块不算变坏
        old = base_files.get(rel)
        if old is None:
            continue                     # 新模块由「新增超限模块」那条守
        # ① 行数上涨
        if cur["lines"] > old["lines"] + LINE_TOLERANCE:
            key = f"{rel}:lines"
            if key not in acks:
                problems.append(f"{rel}：行数 {old['lines']} → {cur['lines']}"
                                f"（+{cur['lines'] - old['lines']} > 容差 {LINE_TOLERANCE}）")
        # ② except 数量上涨（本项目的静默吞异常是历史病）
        if cur["excepts"] > old["excepts"]:
            key = f"{rel}:excepts"
            if key not in acks:
                problems.append(f"{rel}：except 子句 {old['excepts']} → {cur['excepts']}"
                                "（新增异常处理必须要么记日志要么给提示）")

    # ③ 新增超限模块
    new_oversized = set(now["oversized"]) - base_oversized
    for rel in sorted(new_oversized):
        key = f"{rel}:size"
        if key not in acks:
            problems.append(f"{rel}：{now['per_file'][rel]['lines']} 行 > {LINE_CAP} 上限"
                            "（新增第 4 个超限模块）")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="质量棘轮：只拦变坏")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--base", default=None)
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--ack", action="append", default=[], metavar="KEY")
    ap.add_argument("--reason", default="")
    a = ap.parse_args(argv)

    if a.update_baseline:
        data = snapshot()
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8", newline="\n")
        print(f"baseline 已更新：{BASELINE.relative_to(ROOT)}"
              f"（{len(data['per_file'])} 模块，超限 {len(data['oversized'])}）")
        return 0

    if not BASELINE.is_file():
        print("✗ 缺少基线：先跑一次 python scripts/quality_delta.py --update-baseline")
        return 2
    base_data = json.loads(BASELINE.read_text(encoding="utf-8"))
    acks = dict(base_data.get("acks") or {})
    for key in a.ack:
        if not a.reason.strip():
            print(f"✗ ack {key} 必须带 --reason（无理由的豁免等于关闭门禁）")
            return 2
        acks[key] = a.reason.strip()
    base_data["acks"] = acks

    files = a.files or changed_files(a.base)
    problems = compare(base_data, files, acks)
    if problems:
        print("质量恶化：")
        for p in problems:
            print(f"  ✗ {p}")
        print(f"\nWORSE {len(problems)}")
        return 3
    print("WORSE 0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
