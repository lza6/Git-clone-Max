"""黑匣子日志的关键字过滤（Rev8 C6-4，从 settings_panel 抽出保持 800 线）。

显示层过滤：`LogBuffer.filter_text` 只影响 flush/replay 渲染哪些行，
`LogModel` 永远全量 —— 清空过滤即恢复完整视图。
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import QLineEdit


def build_log_filter_row(owner: Any, toolbar: Any) -> QLineEdit:
    """在日志工具行追加过滤输入框；挂 owner.log_filter_edit 并接线。

    **引用环铁则**（test_g69_regressions 实测拦下）：信号连接不得用 lambda
    捕获 owner —— 这里用 weakref 让回调在 owner 销毁后自动失效，
    MainWindow 才能按引用计数释放。
    """
    import weakref as _wk
    owner_ref = _wk.ref(owner)
    owner.log_filter_edit = QLineEdit()
    owner.log_filter_edit.setPlaceholderText(
        owner._tr_safe("按关键字过滤日志…") if hasattr(owner, "_tr_safe")
        else "按关键字过滤日志…")
    owner.log_filter_edit.setObjectName("logFilter")
    owner.log_filter_edit.setClearButtonEnabled(True)
    owner.log_filter_edit.setMaximumWidth(220)
    owner.log_filter_edit.setToolTip(
        owner._tr_safe("只显示包含关键字的日志行；清空恢复全部")
        if hasattr(owner, "_tr_safe") else "只显示包含关键字的日志行；清空恢复全部")

    def _on_filter_changed(_text: str) -> None:
        o = owner_ref()
        if o is not None:
            apply_log_filter(o)
    owner.log_filter_edit.textChanged.connect(_on_filter_changed)
    toolbar.addWidget(owner.log_filter_edit)
    return owner.log_filter_edit


def apply_log_filter(owner: Any) -> None:
    """把输入框当前值应用到 LogBuffer；空值触发全量重放。"""
    buf = getattr(owner, "_log_buffer", None)
    edit = getattr(owner, "log_filter_edit", None)
    if buf is None or edit is None:
        return
    buf.filter_text = edit.text()
    if buf.filter_text.strip():
        buf.flush()
    else:
        buf.replay_all()
