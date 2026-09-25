"""G55 工程与测试体系：tracemalloc 假批次内存峰值/增长基线脚本。

对「批量仓库规格解析」这一高并发场景做纯内存基线：
- 用 tracemalloc 记录解析 N 条仓库 URL 的峰值内存与净增长；
- 模拟假批次（不触发网络、不启动 Qt），纯函数 CPU 任务；
- 输出基线表 + 回归分类（相比上一次基线文件）。

用法：
    python scripts/mem_baseline.py [--batch N] [--baseline FILE]
默认 N=5000，基线文件 scripts/.mem_baseline.json（不存在则只打印本次值）。

回归分类规则（针对峰值）：
- 峰值 > 基线峰值 * 1.25           -> REGRESSION
- 峰值 > 基线峰值 * 1.10           -> WATCH
- 其余                            -> OK
"""
from __future__ import annotations

import argparse
import json
import sys
import tracemalloc
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_BATCH = 5000
DEFAULT_BASELINE = Path(__file__).resolve().parent / ".mem_baseline.json"


def parse_batch_urls(batch: int) -> int:
    """解析 batch 条仓库 URL（github/gitlab/gitee 混合），返回有效条数。

    纯函数、无网络；复用 url_lib.parse_any_repo_url 的解析逻辑。
    """
    from gcm.app.url_lib import parse_any_repo_url
    samples = [
        "https://github.com/owner/repo",
        "git@github.com:owner/repo.git",
        "https://gitlab.com/group/sub/repo",
        "owner/repo",
        "https://gitee.com/owner/repo",
        "https://codeberg.org/owner/repo",
        "",
        "not a url",
    ]
    valid = 0
    for i in range(batch):
        raw = samples[i % len(samples)]
        if parse_any_repo_url(raw):
            valid += 1
    return valid


def measure(batch: int) -> dict[str, Any]:
    """tracemalloc 采样一次，返回 {batch, peak, growth, valid, elapsed}。"""
    import time
    tracemalloc.start()
    t0 = time.perf_counter()
    valid = parse_batch_urls(batch)
    cur, peak = tracemalloc.get_traced_memory()
    elapsed = time.perf_counter() - t0
    tracemalloc.stop()
    return {
        "batch": batch,
        "peak_bytes": peak,
        "growth_bytes": cur,
        "valid": valid,
        "elapsed_s": round(elapsed, 4),
    }


def classify(peak: int, base_peak: int) -> str:
    """峰值回归分类：OK / WATCH / REGRESSION。"""
    if base_peak <= 0:
        return "OK"
    ratio = peak / base_peak
    if ratio > 1.25:
        return "REGRESSION"
    if ratio > 1.10:
        return "WATCH"
    return "OK"


def _fmt_mb(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MB"


def load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_baseline(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH, help=f"假批次条数（默认 {DEFAULT_BATCH}）")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE, help="基线文件路径")
    args = ap.parse_args(argv)

    result = measure(args.batch)
    base = load_baseline(args.baseline)
    cls = classify(result["peak_bytes"], base["peak_bytes"]) if base is not None else "OK"

    print("=" * 64)
    print("G55 mem baseline (tracemalloc, fake batch)")
    print("=" * 64)
    print(f"batch        : {result['batch']}")
    print(f"valid        : {result['valid']}")
    print(f"peak         : {result['peak_bytes']} bytes  ({_fmt_mb(result['peak_bytes'])})")
    print(f"growth       : {result['growth_bytes']} bytes  ({_fmt_mb(result['growth_bytes'])})")
    print(f"elapsed      : {result['elapsed_s']} s")
    if base is not None:
        print("-" * 64)
        print(f"baseline peak: {base['peak_bytes']} bytes  ({_fmt_mb(base['peak_bytes'])})")
    print("-" * 64)
    print(f"classification: {cls}")
    if args.baseline.exists() or args.baseline == DEFAULT_BASELINE:
        save_baseline(args.baseline, result)
        print(f"baseline saved: {args.baseline}")
    return 0 if cls != "REGRESSION" else 1


if __name__ == "__main__":
    sys.exit(main())