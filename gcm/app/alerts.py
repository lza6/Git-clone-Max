"""G53 数据洞察：多维评分卡 / 阈值告警 / 存储看板。

- scorecard_for_repo / scorecards：6 维评分（成功率/陈旧度/冲突/大小/标签/最近同步）
- storage_overview：数据目录占用 + 磁盘剩余 + 超阈值建议
- AlertScanner：后台线程按间隔扫描阈值告警（陈旧/单仓失败率/全局失败率/磁盘水位），
  24h 去重 + 可选回调/Webhook（全部可注入时钟便于测试）
"""
from __future__ import annotations

import math
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

# 评分维度名（0-100）
DIM_LABELS: dict[str, str] = {
    "success_rate": "成功率",
    "freshness": "陈旧度",
    "conflicts": "冲突健康",
    "size_score": "仓库大小",
    "tags_score": "标签完整",
    "recency": "最近同步",
}

_DIM_KEYS = tuple(DIM_LABELS)


def _now() -> float:
    return time.time()


def _days(ts_float: Optional[float], now: float) -> Optional[float]:
    """距 now 的天数；无时间返回 None。"""
    if not ts_float:
        return None
    return max(0.0, (now - float(ts_float)) / 86400.0)


def scorecard_for_repo(repo: dict[str, Any], history: list[dict[str, Any]],
                       size_bytes: Optional[int] = None,
                       now: Optional[float] = None) -> dict[str, Any]:
    """单仓 6 维评分。repo 来自 db.list_repos() 行；history 来自 db.history(repo_id,20)。"""
    now = now or _now()
    terminal = [h for h in history if h.get("status") in ("success", "failed", "conflict")]
    n = len(terminal)
    ok = sum(1 for h in terminal if h.get("status") == "success")
    conflict = sum(1 for h in terminal if h.get("status") == "conflict")
    success_rate = round(100.0 * ok / n) if n else 0

    # 陈旧度：最近一次成功同步距今天数，60 天线性衰减到 0
    last_sync = None
    for h in terminal:
        try:
            last_sync = float(h.get("started_at") or 0) or None
            if last_sync:
                break
        except (TypeError, ValueError):
            continue
    days_since = _days(last_sync, now)
    freshness = round(max(0.0, 100.0 - (days_since or 90) / 60.0 * 100.0))

    # 冲突健康：冲突次数 25 分递减
    conflicts = max(0, 100 - conflict * 25)

    # 仓库大小：<=50MB 满分，之后对数衰减
    if size_bytes:
        mb = size_bytes / 1048576.0
        size_score = 100 if mb <= 50 else max(0, round(100 - math.log2(mb / 50.0) * 20))
    else:
        size_score = 0

    # 标签完整：有任一标签即满
    tags = repo.get("tags") or ""
    tags_score = 100 if str(tags).strip("[]{}\" ") else 0

    # 最近同步：阈值分档
    if days_since is None:
        recency = 0
    elif days_since <= 7:
        recency = 100
    elif days_since <= 14:
        recency = 80
    elif days_since <= 30:
        recency = 60
    elif days_since <= 60:
        recency = 40
    elif days_since <= 90:
        recency = 20
    else:
        recency = 0

    dims = {
        "success_rate": success_rate,
        "freshness": freshness,
        "conflicts": conflicts,
        "size_score": size_score,
        "tags_score": tags_score,
        "recency": recency,
    }
    overall = round(sum(dims.values()) / len(dims))
    suggestions: list[str] = []
    if success_rate < 60:
        suggestions.append("同步失败率偏高，检查网络/凭据或改用镜像")
    if freshness < 50:
        suggestions.append("仓库长时间未同步，考虑加入一键更新")
    if conflicts > 0:
        suggestions.append("存在本地冲突，建议处理分叉后重新同步")
    if tags_score < 100:
        suggestions.append("未打标签，建议补充便于检索")
    if not suggestions:
        suggestions.append("状态良好，无需处理")
    return {
        "key": f"{repo.get('owner')}/{repo.get('repo')}",
        "repo_id": repo.get("id"),
        **dims,
        "overall": overall,
        "suggestions": suggestions,
    }


def scorecards(all_repos: list[dict[str, Any]],
               per_repo_history: Callable[[int], list[dict[str, Any]]],
               per_repo_size: Optional[Callable[[Any], Optional[int]]] = None,
               now: Optional[float] = None) -> list[dict[str, Any]]:
    """批量评分；返回按 overall 升序（最差在前）的卡片列表。"""
    cards = []
    for repo in all_repos:
        rid = repo.get("id")
        hist = []
        try:
            hist = per_repo_history(rid) if rid is not None else []
        except Exception:
            hist = []
        size = None
        if per_repo_size:
            try:
                size = per_repo_size(repo.get("local_path"))
            except Exception:
                size = None
        cards.append(scorecard_for_repo(repo, hist, size, now=now))
    cards.sort(key=lambda c: c["overall"])
    return cards


def overall_summary(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """评分卡总览：各维均值 + 最弱仓前 5。"""
    if not cards:
        return {"avg": {}, "overall": 0, "weakest": [], "suggestions": ["（暂无仓库数据）"]}
    avg = {k: round(sum(c[k] for c in cards) / len(cards)) for k in _DIM_KEYS}
    overall = round(sum(avg.values()) / len(avg))
    weakest = cards[:5]
    return {"avg": avg, "overall": overall, "weakest": weakest,
            "suggestions": weakest[0].get("suggestions", []) if weakest else []}


# ---------------------------------------------------------------- 存储看板
_DB_FILES = ("repos.db", "repos.db-wal", "repos.db-shm", "progress.json", "history.json")


def storage_overview(data_dir: Optional[Path], threshold_gb: int = 5) -> dict[str, Any]:
    """数据目录占用 + 磁盘剩余 + 超阈值建议。任何异常兜底返回可读结构。"""
    base = Path(data_dir) if data_dir else Path.cwd()
    files: dict[str, int] = {}
    total = 0
    try:
        for name in _DB_FILES:
            p = base / name
            sz = p.stat().st_size if p.exists() else 0
            files[name] = sz
            total += sz
        du = shutil.disk_usage(str(base))
        free_bytes, total_bytes = du.free, du.total
        free_gb = free_bytes / 1073741824.0
        over = free_gb < threshold_gb
        suggest = ("磁盘剩余偏低，建议清理历史/备份后迁移数据目录"
                   if over else "磁盘空间充足")
        return {
            "data_dir": str(base), "files": files, "total_bytes": total,
            "free_bytes": free_bytes, "total_bytes_disk": total_bytes,
            "free_gb": round(free_gb, 1), "threshold_gb": threshold_gb,
            "over_threshold": over, "suggest": suggest,
        }
    except Exception as e:  # noqa: BLE001
        return {"data_dir": str(base), "files": files, "total_bytes": total,
                "free_bytes": 0, "total_bytes_disk": 0, "free_gb": 0.0,
                "threshold_gb": threshold_gb, "over_threshold": False,
                "suggest": f"存储看板不可用：{e}"}


# ---------------------------------------------------------------- 阈值告警
def _stale_alert(repo: dict[str, Any], stale_days: int, now: float) -> Optional[dict[str, Any]]:
    try:
        last = float(repo.get("last_sync_at") or 0) or None
    except (TypeError, ValueError):
        last = None
    if last is None:
        return None
    d = (now - last) / 86400.0
    if d > stale_days:
        return {"key": f"stale:{repo.get('owner')}/{repo.get('repo')}", "kind": "stale",
                "level": "warning",
                "title": "仓库长时间未同步",
                "message": f"{repo.get('owner')}/{repo.get('repo')} 已 {int(d)} 天未同步（阈值 {stale_days} 天）"}  # noqa: E501
    return None


def scan_alerts(all_repos: list[dict[str, Any]],
                per_repo_history: Callable[[int], list[dict[str, Any]]],
                data_dir: Optional[Path] = None,
                stale_days: int = 30,
                repo_fail_rate_pct: int = 50,
                global_fail_rate_pct: int = 30,
                disk_min_gb: int = 5,
                now: Optional[float] = None) -> list[dict[str, Any]]:
    """扫描全部阈值告警；返回告警列表（空=无）。"""
    now = now or _now()
    out: list[dict[str, Any]] = []

    # 陈旧 + 单仓失败率
    for repo in all_repos:
        st = _stale_alert(repo, stale_days, now)
        if st:
            out.append(st)
        rid = repo.get("id")
        hist = []
        try:
            hist = per_repo_history(rid) if rid is not None else []
        except Exception:
            hist = []
        terminal = [h for h in hist if h.get("status") in ("success", "failed", "conflict")]
        if terminal:
            fail = sum(1 for h in terminal if h.get("status") == "failed")
            rate = 100.0 * fail / len(terminal)
            if rate > repo_fail_rate_pct:
                out.append({
                    "key": f"failrepo:{repo.get('owner')}/{repo.get('repo')}",
                    "kind": "repo_fail_rate", "level": "warning",
                    "title": "单仓失败率偏高",
                    "message": (f"{repo.get('owner')}/{repo.get('repo')} "
                                f"失败率 {rate:.0f}%（阈值 {repo_fail_rate_pct}%）"),
                })

    # 磁盘水位
    try:
        info = storage_overview(data_dir, disk_min_gb)
        if info.get("over_threshold"):
            out.append({"key": "disk_free", "kind": "disk", "level": "critical",
                        "title": "磁盘剩余不足",
                        "message": info["suggest"]})
    except Exception:
        pass
    return out


class AlertScanner:
    """后台告警扫描线程（可注入时钟/回调，24h 去重）。"""

    def __init__(self, scan_fn: Callable[[], list[dict[str, Any]]],
                 interval_sec: float = 3600.0,
                 now_fn: Optional[Callable[[], float]] = None,
                 on_alerts: Optional[Callable[[list[dict[str, Any]]], None]] = None):
        self._scan_fn = scan_fn
        self._interval = max(1.0, float(interval_sec))
        self._now_fn = now_fn or _now
        self._on_alerts = on_alerts
        self._last_fired: dict[str, float] = {}
        self._stop_evt: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None

    def scan_once(self, now: Optional[float] = None) -> list[dict[str, Any]]:
        """执行一次扫描并应用 24h 去重；返回本次实际触发的告警。"""
        now = now or self._now_fn()
        fired: list[dict[str, Any]] = []
        for alert in (self._scan_fn() or []):
            key = alert.get("key")
            if key:
                last = self._last_fired.get(key)
                if last is not None and now - last < 86400.0:
                    continue  # 24h 内已触发过 → 去重
                self._last_fired[key] = now
            fired.append(alert)
        if fired and self._on_alerts:
            try:
                self._on_alerts(fired)
            except Exception:
                pass
        return fired

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="g53-alert-scanner")
        self._thread.start()

    def _loop(self) -> None:
        evt = self._stop_evt
        while evt is not None and not evt.wait(self._interval):
            try:
                self.scan_once()
            except Exception:
                pass

    def stop(self) -> None:
        if self._stop_evt:
            self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self._stop_evt = None

    def reset_dedup(self) -> None:
        self._last_fired.clear()
