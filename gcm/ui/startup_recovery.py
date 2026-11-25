"""B4：启动失败恢复对话框（故意极简：不读 settings / 数据库 / 主题）。

启动期最坏的失败模式是"双击没反应"：`exc_dump` 只把栈写进 `error.log` 就退出，
小白用户拿不到任何可执行的下一步。本模块在 `QApplication` 已创建、主窗尚未
构建成功时兜底，提供三个动作：打开数据目录 / 导出诊断包 / 重置设置（先备份）。

设计约束：
- 恢复路径本身不得依赖任何可能已损坏的组件 —— 只用 QtWidgets + 标准库；
- 所有用户可见文案走 `tr()`；
- 导出诊断包复用 `app/diag_bundle.py`（其中已 redact，且不依赖 DB）。
"""
from __future__ import annotations

import datetime
from contextlib import suppress
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from .. import __version__
from ..app.diag_bundle import build_diag_bundle, default_bundle_name
from ..i18n import tr

__all__ = ["collect_recovery_info", "export_recovery_bundle",
           "reset_settings_with_backup", "StartupRecoveryDialog",
           "run_recovery_dialog"]


def collect_recovery_info(data_dir: str | Path, error_text: str = "") -> dict[str, Any]:
    """收集恢复页要展示的事实（纯函数，不读损坏组件）。"""
    data = Path(data_dir)
    return {
        "version": __version__,
        "data_dir": str(data),
        "app_log": str(data / "app.log"),
        "error_log": str(data / "error.log"),
        "error": str(error_text or "").strip(),
    }


def export_recovery_bundle(data_dir: str | Path, error_text: str = "") -> Path:
    """导出诊断包（含启动错误栈）；返回 zip 路径。失败时上抛给调用方提示。"""
    data = Path(data_dir)
    info = collect_recovery_info(data, error_text)
    report = (
        f"startup recovery\nversion={info['version']}\n"
        f"data_dir={info['data_dir']}\n\n{info['error']}\n"
    )
    out = data / default_bundle_name("gcm-startup-recovery")
    return build_diag_bundle(out, data_dir=data, report_text=report)


def reset_settings_with_backup(data_dir: str | Path) -> Path | None:
    """把 settings.json 改名备份为 settings.corrupt-<时间>.json；无文件返回 None。

    只改名不删除：凭据/偏好仍在备份里，用户可手工取回。
    """
    src = Path(data_dir) / "settings.json"
    if not src.exists():
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = src.with_name(f"settings.corrupt-{stamp}.json")
    src.rename(dest)
    return dest


class StartupRecoveryDialog(QDialog):
    """启动失败三动作对话框（模态；不依赖主题/设置/数据库）。"""

    def __init__(self, data_dir: str | Path, error_text: str = "", parent=None):
        super().__init__(parent)
        self._data_dir = Path(data_dir)
        self._error_text = str(error_text or "")
        self.setWindowTitle(tr("启动失败"))
        self.resize(640, 460)

        info = collect_recovery_info(self._data_dir, self._error_text)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr(
            "Git-clone-Max 启动时遇到错误。你可以先尝试下面的恢复动作，"
            "或导出诊断包反馈问题。")))
        layout.addWidget(QLabel(tr("版本：{}").format(info["version"])))
        layout.addWidget(QLabel(tr("数据目录：{}").format(info["data_dir"])))
        layout.addWidget(QLabel(tr("日志：{}").format(info["app_log"])))
        layout.addWidget(QLabel(tr("错误摘要（可复制）：")))
        box = QTextEdit(self)
        box.setReadOnly(True)
        box.setPlainText(self._error_text[-8000:])
        layout.addWidget(box, 1)

        buttons = QDialogButtonBox(self)
        self._btn_open = QPushButton(tr("打开数据目录"), self)
        self._btn_export = QPushButton(tr("导出诊断包"), self)
        self._btn_reset = QPushButton(tr("重置设置（先备份）"), self)
        self._btn_quit = QPushButton(tr("退出"), self)
        for b in (self._btn_open, self._btn_export, self._btn_reset,
                  self._btn_quit):
            buttons.addButton(b, QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)

        self._btn_open.clicked.connect(self._open_data_dir)
        self._btn_export.clicked.connect(self._export_bundle)
        self._btn_reset.clicked.connect(self._reset_settings)
        self._btn_quit.clicked.connect(self.reject)

    # 绑定方法连接（无 lambda 捕获 self，避免引用环）
    def _open_data_dir(self) -> None:
        # 「打开目录」唯一实现是 util/openfolder（gcm/ui 不得自写 startfile/xdg-open）
        from ..util.openfolder import open_folder
        if not open_folder(self._data_dir):
            QMessageBox.warning(self, tr("打开失败"),
                                tr("操作失败：{}").format(self._data_dir))

    def _export_bundle(self) -> None:
        try:
            path = export_recovery_bundle(self._data_dir, self._error_text)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("导出失败"), tr("操作失败：{}").format(e))
            return
        QMessageBox.information(self, tr("已导出诊断包"),
                                tr("已导出：{}").format(path))

    def _reset_settings(self) -> None:
        try:
            backup = reset_settings_with_backup(self._data_dir)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("重置失败"), tr("操作失败：{}").format(e))
            return
        if backup is None:
            QMessageBox.information(self, tr("无需重置"),
                                    tr("没有可重置的 settings.json。"))
            return
        QMessageBox.information(
            self, tr("已重置"),
            tr("设置已备份到：{}").format(backup) + "\n" + tr("重置后请重新启动应用。"))


def run_recovery_dialog(app: Any, data_dir: str | Path,
                        error_text: str = "") -> int:
    """构造并模态显示恢复对话框；异常不外泄（兜底路径自身不能再崩）。"""
    try:
        dlg = StartupRecoveryDialog(data_dir, error_text)
        return int(dlg.exec())
    except Exception as e:  # noqa: BLE001 —— 对话框都起不来时只能退回 stderr
        with suppress(Exception):  # 连 stderr 都不可用的极端场景：放弃提示
            import sys as _sys
            print(f"[startup-recovery] {e}", file=_sys.stderr)
        return 0
