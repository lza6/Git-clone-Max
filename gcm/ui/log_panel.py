"""设置页「黑匣子日志」区（Rev15 R15-00 从 settings_panel 迁出）。

独立原因：`settings_panel.py` 已 799 行，再加设置页工作区改动会撞 800 硬上限；
日志组与设置工作区（QSplitter）本来就是同一屏的两块，放在独立模块更清楚。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import tr
from .theme import make_highlighter

if TYPE_CHECKING:  # 仅类型标注（避免循环导入）
    from .main_window import MainWindow


def build_log_box(owner: MainWindow) -> QGroupBox:
    """构建黑匣子日志区（控件挂 owner.log_count/log_view/btn_save_log/btn_clear_log）。"""
    g2 = QGroupBox(tr("黑匣子日志（实时）"))
    g2.setToolTip(tr("应用运行日志实时输出；可导出为文本文件排查问题"))
    l2 = QVBoxLayout(g2)
    tb = QHBoxLayout()
    owner.log_count = QLabel(tr("0 条"))
    owner.log_count.setObjectName("muted")
    tb.addWidget(owner.log_count)
    tb.addStretch()
    owner.btn_save_log = QPushButton(tr("导出日志…"))
    owner.btn_save_log.setToolTip(tr("把当前日志内容导出为文本文件"))
    owner.btn_save_log.clicked.connect(owner.save_log)
    owner.btn_clear_log = QPushButton(tr("清空日志"))
    owner.btn_clear_log.clicked.connect(owner.clear_log)
    tb.addWidget(owner.btn_save_log)
    tb.addWidget(owner.btn_clear_log)
    # Rev8 C6-4：关键字过滤（显示层；模型永远全量，清空即重放恢复）
    from .log_filter import build_log_filter_row
    build_log_filter_row(owner, tb)
    l2.addLayout(tb)
    owner.log_view = QPlainTextEdit()
    owner.log_view.setObjectName("console")
    owner.log_view.setReadOnly(True)
    owner.log_view.setMaximumBlockCount(4000)
    owner.log_view.setMinimumHeight(80)
    owner.log_view.setAccessibleName(tr("黑匣子日志"))
    l2.addWidget(owner.log_view, 1)
    # 关键字高亮：保留在 owner 上，避免包装对象被回收。
    try:
        owner.log_highlighter = make_highlighter(owner.log_view.document())
    except Exception:
        owner.log_highlighter = None
    return g2


__all__ = ["build_log_box"]
