"""G71-7 引擎构造工厂：把 `MainWindow._launch` 里的 40+ 行参数拼装拆出来。

拆分动机（B7 长函数治理）：
- `_launch` 原本 153 行，其中约 40 行是「从 settings 逐个读字段 → 传给
  `SyncEngine`」的纯映射；它与 UI 逻辑无关，却把主流程埋在参数列表里。
- 参数映射最容易漂移（新增设置项时漏接线 → 用户改了没用）。集中到一处后，
  `tests/test_g71_event_stream.py::TestEngineFactory` 可以一次断言多个字段。
行为**零变化**：这里逐项照抄原参数（含注释里的 G 编号），只把 `owner` 当
数据源读取，不触碰任何 UI 控件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..app.engine import SyncEngine


def build_sync_engine(owner: Any, target_root: Path | str, *,
                      single_branch: bool, mirror: bool,
                      event_bus: Optional[Any] = None,
                      enable_retry_queue: bool = True) -> SyncEngine:
    """按 `owner.settings` 构造一个 `SyncEngine`。**这是设置 → 引擎的唯一映射点。**

    `owner` 需要提供：`settings`、`db`、`progress_path`、`data_dir`、
    `session_store`、`watchdog`（不在这里使用）、`_progress_gc_mod()`。

    **为什么必须唯一**（v9.12.0 实测教训）：`MainWindow.__init__` 曾经也手写了一份
    参数列表（G45 时代留下），两份列表慢慢漂移 —— 启动时那个「空闲引擎」漏掉了
    `host_concurrency` / `time_budget` / `concurrency_state_path`，于是用户改完
    设置重启后**控件回填正确、引擎却仍读默认值**（真机实测：改 11/20，重启后引擎
    仍是 4/0）。现在启动期也走本函数，两处合并成一处，删掉手写列表。

    `enable_retry_queue=False`：给**启动期的空闲引擎**用。
    理由：重投要求复用上一批的 `_last_batch_ctx`（host_by_key / fetch_depth / mirror，
    见 G67-3），而启动时空闲引擎的该快照为空 —— 让它自动重投会把非 GitHub 仓库
    落库成 `host="github.com"`，污染 `UNIQUE(owner,repo,host)`（G67-3 修过的同类缺陷）。
    真正的批次引擎（`_launch`）仍走默认 True。
    """
    s = getattr(owner, "settings", None)
    if s is None:
        raise ValueError("build_sync_engine: owner.settings 缺失")
    data_dir = Path(getattr(owner, "data_dir", ""))

    return SyncEngine(
        target_root,
        db=getattr(owner, "db", None),
        progress_path=getattr(owner, "progress_path", None),
        concurrency=int(getattr(s, "concurrency", 8)),
        fetch_timeout=int(getattr(s, "fetch_timeout", 300)),
        clone_timeout=int(getattr(s, "clone_timeout", 600)),
        time_budget=float(getattr(s, "time_budget_min", 0) or 0) * 60.0,
        host_concurrency=int(getattr(s, "host_concurrency", 4)),
        retries=int(getattr(s, "retries", 2)),
        proxy=str(getattr(s, "proxy", "") or ""),
        token=str(getattr(s, "token", "") or ""),
        submodule=bool(getattr(s, "submodule", False)),
        rate_limit_kbps=int(getattr(s, "rate_limit_kbps", 0) or 0),
        host_tokens=getattr(s, "host_tokens", None) or {},
        mirror_prefix=getattr(s, "mirror_prefix", None) or {},
        mirror_use_presets=bool(getattr(s, "mirror_use_presets", True)),
        mirror_fallback=bool(getattr(s, "mirror_fallback", True)),
        mirror_probe_timeout=float(getattr(s, "mirror_probe_timeout", 8) or 8),
        host_auth_types=getattr(s, "host_auth_types", None) or {},
        precheck_remote=bool(getattr(s, "precheck_remote", False)),
        single_branch=bool(single_branch),
        force_ipv4=bool(getattr(s, "force_ipv4", False)),
        lfs_enabled=bool(getattr(s, "lfs_enabled", False)),
        post_clone_hook=str(getattr(s, "post_clone_hook", "")),
        ssh_key=str(getattr(s, "ssh_key_path", "")),
        mirror=bool(mirror),
        retry_queue_path=(data_dir / "retry_queue.json") if enable_retry_queue else None,
        retry_enabled=(bool(getattr(s, "retry_enabled", False))
                       and enable_retry_queue),
        retry_max=int(getattr(s, "retry_max", 2) or 2),
        retry_backoff_base_sec=int(getattr(s, "retry_backoff_base_sec", 30) or 30),
        session_store=getattr(owner, "session_store", None),
        watchdog_enabled=bool(getattr(s, "watchdog_enabled", False)),
        watchdog_interval_min=int(getattr(s, "watchdog_interval_min", 5) or 5),
        progress_gc=_progress_gc(owner),
        concurrency_state_path=data_dir / "concurrency_state.json",
        event_bus=event_bus,
    )


def _progress_gc(owner: Any) -> Any:
    """惰性取进度治理模块；宿主没有该方法时返回 None（引擎等价于不启用）。"""
    fn = getattr(owner, "_progress_gc_mod", None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            return None
    return None
