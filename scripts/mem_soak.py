#!/usr/bin/env python
"""长时间挂机泄漏巡检（C3-6 / Rev7 B3-6）。

**为什么需要**：本工具的真实用法是「挂着刷几百个仓库、一挂一整天」。此前只有一次性
`mem_baseline.py`，没有「反复跑同一条链路，看内存会不会缓慢长」的检查。

做法：模拟 N 轮「任务结果 → 进进度态 → 发布事件 → 面板刷新」，用 `tracemalloc`
对比前后快照，打印增长最多的 10 个分配点。**不设硬断言**（机器差异大），
只给可复算的数字 + 一个宽松的 RSS 参考线；真正的判据是「Top 10 里有没有无界累积」。

用法：
    python scripts/mem_soak.py                 # 500 轮
    python scripts/mem_soak.py --rounds 2000
    python scripts/mem_soak.py --json
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _rss_mb() -> float:
    try:
        import psutil           # 可选依赖：没有就退化为 0
        return round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
    except Exception:           # noqa: BLE001 —— 没有 psutil 时只报 tracemalloc 数字
        return 0.0


def soak(rounds: int) -> dict:
    from gcm.app.event_bus import EventBus, EventType
    from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus

    bus = EventBus()
    tmp = Path(tempfile.mkdtemp(prefix="gcm_soak_"))
    spec = RepoSpec(owner="o", repo="r", url_https="https://github.com/o/r.git")

    def one_cycle(i: int) -> None:
        # 1) 事件发布（环形缓冲必须**有界**：500 条非进度 + 200 条进度）
        bus.publish(EventType.TASK_RESULT, {"key": f"o/r{i}", "status": "success"})
        bus.publish(EventType.TASK_PROGRESS, {"key": f"o/r{i}", "percent": i % 100})
        # 2) 结果对象（真实链路里每个任务都会造一个）
        res = SyncResult(spec=spec, status=SyncStatus.SUCCESS,
                         action=SyncAction.CLONED, message="克隆完成",
                         detail="x" * 200, path=str(tmp))
        _ = res.message  # 保持引用到本轮结束
        del res

    gc.collect()
    tracemalloc.start()
    rss0 = _rss_mb()
    snap0 = tracemalloc.take_snapshot()

    t0 = time.perf_counter()
    for i in range(rounds):
        one_cycle(i)
    elapsed = (time.perf_counter() - t0) * 1000

    gc.collect()
    snap1 = tracemalloc.take_snapshot()
    rss1 = _rss_mb()
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    diff = snap1.compare_to(snap0, "lineno")
    top = []
    for stat in diff[:10]:
        try:
            frame = stat.traceback[0]
            top.append({
                "file": f"{Path(frame.filename).name}:{frame.lineno}",
                "kib_delta": round(stat.size_diff / 1024, 1),
                "blocks_delta": int(stat.count_diff),
            })
        except Exception:       # noqa: BLE001 —— 快照行解析失败只跳过该行
            continue

    stats = bus.stats() if hasattr(bus, "stats") else {}
    return {
        "rounds": rounds,
        "elapsed_ms": round(elapsed, 1),
        "rss_before_mb": rss0,
        "rss_after_mb": rss1,
        "rss_delta_mb": round((rss1 - rss0), 1) if rss0 else None,
        "traced_current_kib": round(cur / 1024, 1),
        "traced_peak_kib": round(peak / 1024, 1),
        "event_buffer": {k: v for k, v in stats.items()
                         if k in ("buffered", "buffered_events", "buffered_progress",
                                  "published", "dropped", "subscribers")},
        "top_allocations": top,
        "note": "Top 分配点若出现与 rounds 成正比增长的容器，即为无界累积（真泄漏）",
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="长时间挂机泄漏巡检")
    ap.add_argument("--rounds", type=int, default=500)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = soak(int(args.rounds))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"轮数 {result['rounds']} · 用时 {result['elapsed_ms']}ms")
    print(f"RSS  {result['rss_before_mb']} → {result['rss_after_mb']} MB"
          f"（Δ {result['rss_delta_mb']} MB）")
    print(f"tracemalloc 当前/峰值：{result['traced_current_kib']} / "
          f"{result['traced_peak_kib']} KiB")
    print(f"事件缓冲：{result['event_buffer']}")
    print("Top 分配增长：")
    for row in result["top_allocations"]:
        print(f"  {row['kib_delta']:+8.1f} KiB  {row['blocks_delta']:+6d} 块  {row['file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
