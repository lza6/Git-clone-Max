"""「本地 API / 事件流」设置分组（G80-B5 抽出）。

为什么移出来：`settings_groups.py` 因为新增退避基数行超过 800 行（硬守卫），
而这个分组是**自包含的**（不依赖同文件其它构建器），最适合挪走。
与 G68 把 7 个构建器从 `build_settings_ui` 拆出去是同一范式：每组只负责自己。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLabel, QSpinBox

from ..i18n import tr


def build_group_local_api(owner, settings):
    """G71-9：GUI 内置本地只读 API（含 SSE 事件流）。

    为什么必须由 GUI 托管：独立 `--serve` 进程与 GUI 是**两个进程**，事件总线
    是进程内的；只有同进程托管，`/api/events` 才能看到引擎的真实事件。
    """
    g = QGroupBox(tr("本地 API / 事件流"))
    g.setToolTip(tr("开启后仅监听 127.0.0.1；只读（不可写仓库），提供 REST + SSE 事件流"))
    f = QFormLayout(g)
    f.setContentsMargins(10, 10, 10, 10)
    f.setHorizontalSpacing(16)
    f.setVerticalSpacing(10)
    f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    owner.ck_local_api = QCheckBox(tr("启用本地只读 API（含 SSE 事件流）"))
    owner.ck_local_api.setToolTip(
        tr("供本机 agent/脚本读取仓库状态、任务事件；默认关闭；只允许回环访问"))
    owner.ck_local_api.setChecked(bool(getattr(settings, "local_api_enabled", False)))
    owner.ck_local_api.toggled.connect(owner.settings_panel._save_local_api_enabled)
    f.addRow(tr("本地 API："), owner.ck_local_api)
    owner.spin_local_api_port = QSpinBox()
    owner.spin_local_api_port.setRange(1024, 65535)
    owner.spin_local_api_port.setValue(int(getattr(settings, "local_api_port", 8765) or 8765))
    owner.spin_local_api_port.setToolTip(tr("默认 8765；端口被占用时启动会失败并写入日志"))
    # G71-11（审查 R4）：用 `editingFinished` 而不是 `valueChanged` —— 后者
    # 每滚一格/输入一位都会触发「停旧服→绑新端口」，连续操作会卡界面。
    owner.spin_local_api_port.editingFinished.connect(
        owner.settings_panel._save_local_api_port_current)
    f.addRow(tr("端口："), owner.spin_local_api_port)
    hint = QLabel(tr("端点：GET /api/events（SSE）· /api/events/snapshot · /api/repos · /api/status"))
    hint.setObjectName("muted")
    hint.setWordWrap(True)
    f.addRow(tr("说明："), hint)
    return g


