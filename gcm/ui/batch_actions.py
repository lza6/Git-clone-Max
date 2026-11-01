"""批次级动作（C2-6）：暂停 / 继续。

**为什么独立成模块**：`main_window.py` 已 2800+ 行并登记为显式技术债，项目约定
「新增 UI 逻辑一律落独立模块，不得再往 main_window 里塞」（CLAUDE.md §6）。
本模块只做一件事：把「暂停/继续」的状态切换与用户可见反馈收在一处，
主窗保留一个两行转发方法（它需要是槽，且要被按钮连到）。
"""
from __future__ import annotations

from typing import Any

from ..i18n import tr


def toggle_batch_pause(owner: Any) -> bool:
    """切换本批次的暂停态；返回切换后是否处于暂停。

    语义（与「取消全部」的区别）：
    - **暂停** = 不再从待发队列取新任务，在途的继续跑完 → 不丢进度、不终止任何在跑任务；
    - **继续** = 立刻把待发队列补满到当前并发上限。
    用户挂机刷几百个仓库时最常要的就是这个中间档。
    """
    eng = getattr(owner, "engine", None)
    if eng is None:
        return False
    try:
        if getattr(eng, "paused", False):
            if eng.resume():
                owner.btn_pause_batch.setText(tr("暂停"))
                owner._emit_log(_datetime_label(), _level_info(), "已继续：待发队列恢复补投")
            return bool(getattr(eng, "paused", False))
        if eng.pause():
            owner.btn_pause_batch.setText(tr("继续"))
            owner._emit_log(_datetime_label(), _level_info(),
                            "已暂停：不再开始新仓库，在途的会跑完")
        else:
            owner._emit_log(_datetime_label(), _level_warn(),
                            "当前没有待发任务，无需暂停")
        return bool(getattr(eng, "paused", False))
    except Exception as e:      # noqa: BLE001 —— 纯状态切换，失败只提示不拖垮批次
        owner._emit_log(_datetime_label(), _level_warn(), f"暂停/继续失败：{e}")
        return False


def _datetime_label() -> str:
    from .main_window import _fmt_dt
    return _fmt_dt()


def _level_info():
    from .theme import LogLevel
    return LogLevel.SYSTEM


def _level_warn():
    from .theme import LogLevel
    return LogLevel.WARN
