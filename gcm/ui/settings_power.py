"""批次③桌面集成：电源相关设置行（防休眠 / 完成后睡眠）。

从 `settings_groups.py` 拆出（该文件贴 800 行上限）。
三个新设置**全部默认关**（用户 2026-11-19 拍板的默认值口径）：
- `prevent_sleep_during_batch`：批次进行期间阻止系统休眠；
- `sleep_after_batch` + `sleep_after_batch_seconds`：批次完成后倒计时睡眠
  （托盘气泡提示，可随时取消；仅睡眠、不关机）。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QGridLayout, QLabel, QSpinBox

from ..i18n import tr


def add_power_rows(owner, settings, layout: QGridLayout, start_row: int) -> int:
    """往「启动与后台运行」组追加电源两行。返回下一可用行号。"""
    owner.ck_prevent_sleep = QCheckBox(tr("批次进行期间阻止系统休眠"))
    owner.ck_prevent_sleep.setChecked(
        bool(getattr(settings, "prevent_sleep_during_batch", False)))
    owner.ck_prevent_sleep.setToolTip(tr(
        "勾选：只要有任务在跑，系统就不会自动睡眠（Windows电源策略；"
        "长批次中途睡眠会中断下载）。默认关。"))
    owner.ck_prevent_sleep.stateChanged.connect(
        owner.settings_panel.wiring.save_prevent_sleep_during_batch)

    owner.ck_sleep_after_batch = QCheckBox(tr("批次完成后让电脑睡眠"))
    owner.ck_sleep_after_batch.setChecked(
        bool(getattr(settings, "sleep_after_batch", False)))
    owner.ck_sleep_after_batch.setToolTip(tr(
        "勾选：批次全部结束后倒计时进入睡眠（仅睡眠，不是关机；有窗口弹出会自动取消）。"
        "适合「挂机下载完直接睡觉」的场景。默认关。"))
    owner.ck_sleep_after_batch.stateChanged.connect(
        owner.settings_panel.wiring.save_sleep_after_batch)

    owner.spin_sleep_after_sec = QSpinBox()
    owner.spin_sleep_after_sec.setRange(10, 3600)
    owner.spin_sleep_after_sec.setSingleStep(10)
    owner.spin_sleep_after_sec.setValue(
        int(getattr(settings, "sleep_after_batch_seconds", 60) or 60))
    owner.spin_sleep_after_sec.setToolTip(tr("批次结束后延迟多少秒进入睡眠（倒计时期间可取消）"))
    owner.spin_sleep_after_sec.setAccessibleName(tr("睡眠倒计时（秒）"))
    owner.spin_sleep_after_sec.valueChanged.connect(
        owner.settings_panel.wiring.save_sleep_after_seconds)

    layout.addWidget(owner.ck_prevent_sleep, start_row, 0, 1, 2)
    layout.addWidget(owner.ck_sleep_after_batch, start_row + 1, 0, 1, 2)
    owner.lbl_sleep_after_sec = QLabel(tr("睡眠倒计时（秒）"))
    layout.addWidget(owner.lbl_sleep_after_sec, start_row + 2, 0)
    layout.addWidget(owner.spin_sleep_after_sec, start_row + 2, 1)
    return start_row + 3
