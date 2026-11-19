"""批次③电源守卫的 UI 生命周期（从 main_window / progress_table 拆出，
两个文件都贴 800 行上限）。

- `batch_sleep_guard_start(owner)`：按设置开启防休眠；
- `batch_sleep_guard_end(owner)`：撤防休眠 + 按设置起"睡眠倒计时"
  （QTimer 单发；期间任何新批次启动会经 `cancel_pending_sleep` 取消）；
- `cancel_pending_sleep(owner)`：用户点了"开始"或手动取消时撤销待执行的睡眠。

设计约束：全部 getattr 兜底 + 永不抛（电源是锦上添花，不能拖垮主流程）；
失败路径走 applog.warn 留痕（不是静默吞）；倒计时托盘气泡只提示一次。
"""
from __future__ import annotations

from typing import Any

from ..app.applog import warn as _warn
from ..i18n import tr


def batch_sleep_guard_start(owner: Any) -> None:
    """批次开始：按设置开启"防休眠"。失败留痕（能力缺失不提示轰炸）。"""
    try:
        from .power import prevent_sleep_start
        if not prevent_sleep_start():
            _warn("防休眠开启失败（非 Windows 或电源策略不可用）——批次照常运行")
    except Exception as e:  # noqa: BLE001 —— 电源能力缺失不拖垮主流程
        _warn(f"防休眠开启异常：{e}")


def batch_sleep_guard_end(owner: Any) -> None:
    """批次结束：撤防休眠；若开启「完成后睡眠」则起倒计时。"""
    try:
        from .power import prevent_sleep_end
        prevent_sleep_end()
    except Exception as e:  # noqa: BLE001
        _warn(f"防休眠撤除异常：{e}")
    try:
        if not bool(getattr(owner.settings, "sleep_after_batch", False)):
            return
        # 有失败/冲突时也照睡 —— 用户要的是"跑完就睡"；但**零任务**不睡
        # （防止空批次误触发）。
        sec = int(getattr(owner.settings, "sleep_after_batch_seconds", 60) or 60)
        sec = max(10, min(3600, sec))
        from PyQt6.QtCore import QTimer
        timer = QTimer(owner)
        timer.setSingleShot(True)
        timer.setInterval(sec * 1000)
        timer.timeout.connect(lambda: _do_sleep(owner))
        owner._sleep_timer = timer
        timer.start()
        try:
            tray = getattr(owner, "tray", None)
            if tray is not None:
                tray.notify(tr("睡眠倒计时"),
                            tr("{s} 秒后让电脑睡眠（运行新批次会自动取消）").format(s=sec))
        except Exception as e:  # noqa: BLE001 —— 提示失败不影响倒计时主功能
            _warn(f"睡眠倒计时托盘提示失败：{e}")
    except Exception as e:  # noqa: BLE001
        _warn(f"睡眠倒计时布置失败：{e}")


def cancel_pending_sleep(owner: Any) -> None:
    """撤销待执行的睡眠（新批次启动 / 用户手动取消时调用）。幂等。"""
    timer = getattr(owner, "_sleep_timer", None)
    if timer is not None:
        try:
            timer.stop()
        except Exception as e:  # noqa: BLE001
            _warn(f"睡眠倒计时取消异常：{e}")
        owner._sleep_timer = None


def _do_sleep(owner: Any) -> None:
    owner._sleep_timer = None
    # 睡前再确认：倒计时期间用户又点了新批次（busy=True）就不睡。
    if bool(getattr(owner, "busy", False)):
        return
    try:
        from .power import request_sleep
        if not request_sleep():
            _warn("睡眠请求未生效（非 Windows 或电源策略不可用）")
    except Exception as e:  # noqa: BLE001
        _warn(f"睡眠请求异常：{e}")
