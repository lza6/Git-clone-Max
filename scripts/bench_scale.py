#!/usr/bin/env python
"""规模基线（C3-5 / Rev7 B3-1）：合成 500 / 1000 / 3000 仓库，量关键路径耗时。

**为什么需要**：此前所有"能不能撑住几千仓库"的判断都靠估。这个脚本给可复算的数字，
并在 `docs/慢测定位.md` 留一份对照，避免下一轮又靠猜。

度量项（都是用户真会碰到的路径）：
1. 建库 + 灌 N 条仓库（一次性成本）
2. `db.list_repos()` 全量（管理页/统计中心都走它）
3. `db.list_repos_page(50)` 分页（HTTP API 走它）
4. `db.search_repos(q)` LIKE 下推
5. `ManageModel.set_rows()` + `QTableView` 绑定渲染（GUI 侧真实成本）
6. `dir_stats` 缓存命中/未命中（C3-1 的两级缓存）
7. `prune_history()` 在 10 万行历史下的耗时（C3-2 把它挪到了批次收尾）

用法：
    python scripts/bench_scale.py            # 500/1000/3000
    python scripts/bench_scale.py --sizes 3000
    python scripts/bench_scale.py --json    # 机读
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _synth(db, n: int) -> float:
    from gcm.models import RepoSpec
    t = time.perf_counter()
    for i in range(n):
        spec = RepoSpec(owner=f"owner{i // 10}", repo=f"repo{i}",
                        url_https=f"https://github.com/owner{i // 10}/repo{i}")
        db.upsert_repo(spec, f"/tmp/clones/owner{i // 10}__repo{i}", host="github.com")
    return (time.perf_counter() - t) * 1000


def bench(n: int) -> dict:
    from gcm.db.repo_db import Database
    tmp = Path(tempfile.mkdtemp(prefix=f"gcm_bench_{n}_"))
    db = Database(tmp / "repos.db")
    out: dict = {"repos": n}
    try:
        out["insert_ms"] = round(_synth(db, n), 1)

        t = time.perf_counter()
        rows = db.list_repos()
        out["list_repos_ms"] = round((time.perf_counter() - t) * 1000, 2)
        out["list_repos_rows"] = len(rows)

        t = time.perf_counter()
        page, total = db.list_repos_page(limit=50, offset=0)
        out["list_repos_page50_ms"] = round((time.perf_counter() - t) * 1000, 2)
        out["list_repos_page_total"] = total
        del page

        t = time.perf_counter()
        hits = db.search_repos("repo123")
        out["search_like_ms"] = round((time.perf_counter() - t) * 1000, 2)
        out["search_like_hits"] = len(hits)
        del hits

        # 库内目录大小缓存（C3-1）：未命中 vs 命中
        paths = [(str(r.get("local_path") or ""), i * 1024) for i, r in enumerate(rows[:n])]
        paths = [(p, s) for p, s in paths if p]
        t = time.perf_counter()
        db.get_dir_sizes([p for p, _ in paths])
        out["dir_stats_miss_ms"] = round((time.perf_counter() - t) * 1000, 2)
        db.set_dir_sizes(paths)
        t = time.perf_counter()
        got = db.get_dir_sizes([p for p, _ in paths])
        out["dir_stats_hit_ms"] = round((time.perf_counter() - t) * 1000, 2)
        out["dir_stats_hit_count"] = len(got)

        # GUI 侧：模型重建 + 表格绑定（真实成本，不是纯 DB）
        try:
            from PyQt6.QtWidgets import QApplication, QTableView
            app = QApplication.instance() or QApplication([])
            from gcm.ui.manage_model import ManageModel
            m = ManageModel()
            t = time.perf_counter()
            m.set_rows(rows)
            out["model_set_rows_ms"] = round((time.perf_counter() - t) * 1000, 2)
            v = QTableView()
            v.setModel(m)
            v.resize(1200, 800)
            t = time.perf_counter()
            for _ in range(3):
                app.processEvents()
            out["table_first_paint_ms"] = round((time.perf_counter() - t) * 1000, 2)
            v.deleteLater()
        except Exception as e:      # noqa: BLE001 —— 无 Qt 环境时只报 DB 侧数字
            out["gui_error"] = str(e)[:120]
    finally:
        db.close()
    return out


def bench_prune(rows: int = 100000) -> dict:
    """`prune_history` 在最坏规模下的耗时（C3-2 把它从"每 100 条"挪到"每批一次"）。"""
    from gcm.db.repo_db import Database
    from gcm.models import RepoSpec
    tmp = Path(tempfile.mkdtemp(prefix="gcm_bench_prune_"))
    db = Database(tmp / "repos.db")
    try:
        rid = db.upsert_repo(RepoSpec(owner="o", repo="r", url_https="u"), "/tmp/x")
        db._conn.execute("BEGIN")
        db._conn.executemany(
            "INSERT INTO sync_history (repo_id,status,action,message,duration_ms,started_at)"
            " VALUES (?,?,?,?,?,datetime('now'))",
            [(rid, "success", "cloned", "ok", 1200)] * rows)
        db._conn.commit()
        t = time.perf_counter()
        deleted = db.prune_history(90)
        return {"history_rows": rows, "prune_ms": round((time.perf_counter() - t) * 1000, 1),
                "prune_deleted": deleted}
    finally:
        db.close()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Git-clone-Max 规模基线")
    ap.add_argument("--sizes", default="500,1000,3000",
                    help="逗号分隔的仓库规模（默认 500,1000,3000）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)
    sizes = [int(s) for s in str(args.sizes).split(",") if s.strip()]
    results = [bench(n) for n in sizes]
    results.append(bench_prune())
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    for r in results:
        if "repos" in r:
            print(f"\n=== {r['repos']} 仓库 ===")
            for k, v in r.items():
                if k != "repos":
                    print(f"  {k:26s} {v}")
        else:
            print(f"\n=== prune_history @ {r['history_rows']} 行 ===")
            for k, v in r.items():
                if k != "history_rows":
                    print(f"  {k:26s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
