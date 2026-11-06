"""行级完成动效（G36-6，Rev11 从 progress_table.py 抽出以守 800 行红线）。

成功绿/失败红背景色 1.2s 淡出到主题底色。仅在非 offscreen（有真实渲染）
且设置开启时由 `_on_worker_result` 调用。动效结束后背景复位为主题面板色，
不影响排序/过滤的 UserRole 数据。
"""
from __future__ import annotations

from PyQt6.QtCore import QVariantAnimation
from PyQt6.QtGui import QBrush, QColor

from . import motion
from .theme import current_palette

# 动效并发上限（32 个任务同时完成时不再创建 32 个动画）
MAX_CONCURRENT = 8


def animate_result_row(panel, index: int, status, SyncStatus) -> None:
    """对 panel.table 第 index 行的状态格做一次背景闪动。

    `panel` 需要 `.table` 与 `.owner`（动效引用挂 owner 级 `_row_anims`）。
    `SyncStatus` 由调用方传入（避免本模块反向依赖 models）。
    任何异常静默吞掉 —— 动效不能拖垮结果链路（G36-6 既有口径）。
    """
    try:
        st = panel.table.item(index, 2)
        if st is None:
            return
        anims = getattr(panel.owner, "_row_anims", None)
        if anims is not None and len(anims) >= MAX_CONCURRENT:
            return
        pal = current_palette()
        flash = pal["success"] if status == SyncStatus.SUCCESS else pal["error"]
        start_color = QColor(flash)
        base_color = QColor(pal["panel"])
        anim = QVariantAnimation(panel)
        anim.setDuration(motion.SLOW_MS)
        anim.setStartValue(start_color)
        anim.setEndValue(base_color)
        anim.valueChanged.connect(
            lambda c: st.setBackground(QBrush(QColor(c))))
        anim.finished.connect(
            lambda: st.setBackground(QBrush(base_color)))
        # 保留引用防止被 GC（存到窗口级列表）
        if not hasattr(panel.owner, "_row_anims"):
            panel.owner._row_anims = []
        panel.owner._row_anims.append(anim)
        anim.finished.connect(lambda: panel.owner._row_anims.remove(anim))
        anim.start()
    except (RuntimeError, AttributeError) as exc:
        # 合理失败路径：控件已销毁（RuntimeError）/ owner 无 _row_anims 挂载点
        # （AttributeError，测试桩）。留痕不静默（pass-only 预算只降不升）。
        from ..app.applog import warn
        warn(f"行完成动效启动失败（row={index}）：{exc}")


__all__ = ["animate_result_row", "MAX_CONCURRENT"]
