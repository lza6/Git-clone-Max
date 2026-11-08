"""prune 三态（Rev10 R-01）：远端已删仓库的安全清理。

语义矩阵（三模式互斥，`--no-clean` 为默认）：

| 模式 | 删 DB 记录 | 删本地目录 | 前提 |
|------|-----------|-----------|------|
| no-clean（默认） | 否 | 否 | 只报告 |
| prune            | 是 | 否 | 仅 remote_gone |
| prune-untouched  | 是 | 是 | 仅 remote_gone 且 protected=False |

三重护栏（规格钉死）：
1. `protected=True`（excluded 标记）任何模式不删；
2. 删除走 plan 预览（本模块产出 plan 行，UI/CLI 复用）；
3. 删除写 oplog（undoable=0，UI 明示「不可撤销」）。

`remote_gone` 判定：最近一次 sync_history 失败 message 含
"repository not found"（大小写不敏感）。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..util.oplog import OpLog

_REMOTE_GONE_MARKERS = ("repository not found", "remote: not found")


def is_remote_gone(history_rows: list[dict[str, Any]]) -> bool:
    """最近一次同步结果是否为「远端仓库不存在」。

    只看**最近一条**失败 —— 历史上的偶发 404 不算（可能后来恢复了）。
    """
    if not history_rows:
        return False
    last = history_rows[0]
    if str(last.get("status") or "") != "failed":
        return False
    msg = str(last.get("message") or "").lower()
    return any(m in msg for m in _REMOTE_GONE_MARKERS)


@dataclass
class PruneItem:
    """一条待清理项（报告/执行两用）。"""
    repo_id: int
    folder_name: str
    url: str
    local_path: str
    protected: bool          # excluded 标记 → 任何模式不删
    remote_gone: bool
    db_removed: bool = False
    dir_removed: bool = False
    skipped_reason: str = ""

    @property
    def will_touch(self) -> bool:
        return self.remote_gone and not self.protected


def collect_prune_items(db: Any) -> list[PruneItem]:
    """扫描全库，产出 prune 候选清单（只读，不写盘不联网）。"""
    out: list[PruneItem] = []
    for row in db.list_repos():
        rid = int(row["id"])
        hist = db.history(rid, 1)
        gone = is_remote_gone(hist)
        out.append(PruneItem(
            repo_id=rid,
            folder_name=str(row.get("folder_name") or ""),
            url=str(row.get("url") or ""),
            local_path=str(row.get("local_path") or ""),
            protected=bool(row.get("excluded")),
            remote_gone=gone,
            skipped_reason="" if gone else "最近一次同步不是「远端不存在」",
        ))
    return out


def execute_prune(db: Any, items: list[PruneItem], mode: str, *,
                  oplog: OpLog | None = None) -> dict[str, int]:
    """按模式执行清理。mode ∈ {"no-clean", "prune", "prune-untouched"}。

    返回计数：`pruned`（删了 DB 记录）/ `dir_removed` / `skipped_protected` /
    `reported`（no-clean 只报告）。
    """
    if mode not in ("no-clean", "prune", "prune-untouched"):
        raise ValueError(f"未知 prune 模式：{mode}")
    counts = {"pruned": 0, "dir_removed": 0, "skipped_protected": 0,
              "reported": 0}
    for it in items:
        if not it.remote_gone:
            continue
        if it.protected:
            it.skipped_reason = "protected（excluded 标记）任何模式不删"
            counts["skipped_protected"] += 1
            continue
        if mode == "no-clean":
            it.skipped_reason = "no-clean 模式只报告"
            counts["reported"] += 1
            continue
        # prune / prune-untouched：删 DB 记录
        try:
            db.delete_repo(it.repo_id)
            it.db_removed = True
            counts["pruned"] += 1
        except Exception:
            it.skipped_reason = "DB 删除失败"
            continue
        if oplog is not None:
            # R-01 护栏③：删除动作必须留痕；不可撤销（undoable=0）
            try:
                oplog.record("prune", it.folder_name,
                             summary=f"远端已删，清理本地记录：{it.url}",
                             undoable=False)
            except Exception as e:
                from .applog import warn as _warn
                _warn(f"prune oplog 记录失败（清理已发生，台账缺这条）：{e}")
        if mode == "prune-untouched":
            # 本地目录删除：只删存在且非空的判断交给 shutil；错误不上抛
            # （DB 已删，目录残留可手动清理，比中途失败半途而废好）
            p = Path(it.local_path) if it.local_path else None
            if p and p.exists():
                try:
                    shutil.rmtree(p)
                    it.dir_removed = True
                    counts["dir_removed"] += 1
                except Exception:
                    it.skipped_reason = "目录删除失败（DB 记录已清）"
    return counts
