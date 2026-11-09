"""统一忙态（C2-3 / Rev7 B2-1）：进入置忙 → 退出**必恢复**。

**为什么需要**：耗时入口各自实现忙态，行为不一致 —— 有的禁用按钮、有的只写日志、
有的什么都不做。用户侧的结果就是「点了没反应 / 又点了一次 / 不知道还在跑」。

**设计（刻意做小）**：
- 一个上下文管理器 `busy(owner, job_id, buttons=(...), cancellable=...)`：
  进入时禁用指定按钮并发布 `job_started`；退出时**无条件**恢复（`finally`），
  失败时发布 `job_finished(result="failed")` 并记日志后再上抛。
- 只接**明确耗时且当前没有反馈**的入口（诊断包 / 报表导出）；已有的
  （克隆批次有 `busy` 标志、Star 导入有 `_star_busy`、镜像测速有 `_mirror_probe_busy`）
  **不重复改造** —— 那属于「为了整齐而动能跑的代码」，回归面不值当。
- `cancellable` 只做记录（`immediate` / `cooperative` / `non_cancellable`），
  用于 UI 文案与事件负载；真正取消由各入口自己的取消机制负责。
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator
from typing import Any

CANCELLABLE_KINDS = ("immediate", "cooperative", "non_cancellable")


def _emit(owner: Any, event_type: str, **payload: Any) -> None:
    """尽力发布事件（没有事件总线时静默跳过 —— 忙态不该因为观测失败而失败）。"""
    try:
        from ..app.event_bus import get_event_bus
        bus = get_event_bus()
        if bus is not None:
            bus.publish(event_type, dict(payload))
            return
    except Exception as e:      # noqa: BLE001 —— 观测失败不影响忙态本身
        from ..app.applog import warn as _warn
        _warn(f"忙态事件发布失败（{event_type}）：{e}")


def _set_buttons_enabled(owner: Any, buttons: Iterable[str],
                         enabled: bool) -> dict[str, bool]:
    """批量置/复位按钮并返回 {属性名: 原状态}（跨线程入口复用 U-15）。

    与 `busy()` 的进入段同语义；跨线程链路没法用上下文管理器
    （进入与退出在两个槽里），所以拆出这两个纯函数。
    """
    saved: dict[str, bool] = {}
    for name in buttons:
        w = getattr(owner, name, None)
        if w is None:
            continue
        try:
            saved[name] = bool(w.isEnabled())
            w.setEnabled(enabled)
        except Exception as e:      # noqa: BLE001 —— 单控件失败不拖垮整段
            from ..app.applog import warn as _warn
            _warn(f"忙态设置控件 {name} 失败：{e}")
    return saved


def _restore_buttons(owner: Any, saved: dict[str, bool]) -> None:
    """按 saved 快照恢复按钮（与 busy() 退出段同语义，恢复失败留痕）。"""
    for name, was_enabled in saved.items():
        w = getattr(owner, name, None)
        if w is None:
            continue
        try:
            w.setEnabled(was_enabled)
        except Exception as e:      # noqa: BLE001 —— 恢复失败也要留痕
            from ..app.applog import warn as _warn
            _warn(f"忙态恢复控件 {name} 失败（可能永久灰掉）：{e}")


@contextlib.contextmanager
def busy(owner: Any, job_id: str, *, buttons: Iterable[str] = (),
         cancellable: str = "immediate") -> Iterator[dict[str, Any]]:
    """忙态上下文：进入置忙，退出**必恢复**（异常路径也一样）。

    `buttons`：owner 上的**属性名**（如 `"btn_diag_bundle"`），进入时禁用、退出时
    恢复到进入前的状态（不是无脑 setEnabled(True) —— 若按钮本来就该禁用，不该被点亮）。

    用法：
        with busy(self, "diag_bundle", buttons=("btn_diag_bundle",)):
            ...耗时操作...
    """
    kind = cancellable if cancellable in CANCELLABLE_KINDS else "immediate"
    saved = _set_buttons_enabled(owner, buttons, False)
    from ..app.event_bus import EventType as _ET
    _emit(owner, _ET.JOB_STARTED, job=job_id, cancellable=kind)
    outcome = "ok"
    try:
        yield {"job": job_id, "cancellable": kind}
    except BaseException:
        outcome = "failed"
        raise
    finally:
        _restore_buttons(owner, saved)
        from ..app.event_bus import EventType as _ET2
        _emit(owner, _ET2.JOB_FINISHED, job=job_id, result=outcome, cancellable=kind)
