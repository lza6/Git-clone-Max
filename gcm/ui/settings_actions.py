"""设置页的「动作型」回调（G76 接线收口）：需要弹窗 / 文件操作的那几条。

## 为什么单独成模块
`gcm/ui/settings_panel.py` 与 `gcm/ui/settings_groups.py` 都已经逼近 800 行硬上限
（守卫 `tests/test_g68_settings_ui.py::test_both_modules_under_line_cap` 钉住）。
G76 要给「只有读侧、没有写侧」的能力补入口，其中三条天然带着模态确认与文件操作，
塞回原文件会立刻破线。按项目约定（新增 UI 逻辑落独立模块）抽到这里：

- `add_data_dir_section`：数据目录私密性 + 迁移入口（控件搭建）；
- `save_post_clone_hook`：克隆后钩子保存（**任意命令执行面**，必须二次确认）；
- `migrate_data_dir_flow`：数据目录迁移流程（不可逆操作，必须先拒绝在跑任务）。

三者都通过 `panel`/`owner` 访问设置与存储，不持有自己的状态（无全局、无缓存）。
"""
from __future__ import annotations

import weakref

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QLabel, QMessageBox, QPushButton

from ..i18n import tr
from .theme import LogLevel


class SettingsWiring(QObject):
    """G76：设置页的字段写入回调集合（控件的信号接到这里）。

    **为什么单独成类**：`settings_panel.py` 已贴近 800 行硬上限
    （守卫 `test_both_modules_under_line_cap`），G76 要补 6 个写入侧，
    塞回面板必破线；按项目约定「新增 UI 逻辑落独立模块」抽出来。

    **为什么弱引用持有面板**：Qt 对 QObject 的绑定方法用弱引用保存连接，
    但本类若强引用面板就形成「面板→wiring→面板」引用环 —— 这正是 G69-18
    追了很久的崩溃根因（引用环让销毁落到循环 GC 手里，可能在 Qt 派发事件中途析构）。
    """

    def __init__(self, panel):
        super().__init__(panel)
        self._panel_ref = weakref.ref(panel)

    # ---------------------------------------------------------------- 内部
    @property
    def _panel(self):
        return self._panel_ref()

    @property
    def _owner(self):
        panel = self._panel
        return getattr(panel, "owner", None) if panel is not None else None

    def _save_field(self, name: str, value) -> bool:
        """写一个设置字段并立即落盘（跨进程锁由 `SettingsStore.save` 负责）。"""
        owner = self._owner
        if owner is None:
            return False
        setattr(owner.settings, name, value)
        owner.settings_store.save(owner.settings)
        return True

    # ---------------------------------------------------------------- 简单字段
    def save_minimize_to_tray(self, checked):
        """关窗是否收进托盘（读侧 `MainWindow.closeEvent`，此前无写入点）。"""
        self._save_field("minimize_to_tray", bool(checked))

    def save_clone_timeout(self, value):
        """单次 clone 超时（`engine_factory` 读它决定 CloneWorker 超时）。"""
        self._save_field("clone_timeout", int(value))

    def save_watchdog_silent(self, checked):
        """看门狗静默续跑；同时作用到运行中的看门狗定时器。"""
        self._save_field("watchdog_silent", bool(checked))
        wd = getattr(self._owner, "watchdog", None)
        if wd is not None and hasattr(wd, "silent"):
            try:
                wd.silent = bool(checked)
            except Exception:  # noqa: BLE001 —— 运行期属性不可写不该影响保存
                pass

    def save_sched_report_format(self, _index=0):
        """定时报表格式 csv/md（调度器读 `sched_report_format`）。"""
        owner = self._owner
        if owner is None:
            return
        combo = getattr(owner, "combo_sched_format", None)
        fmt = str(combo.currentData() or "csv") if combo is not None else "csv"
        self._save_field("sched_report_format", fmt)
        if hasattr(owner, "_start_report_scheduler"):
            owner._start_report_scheduler()

    # ---------------------------------------------------------------- 需要确认的
    def save_post_clone_hook(self):
        panel = self._panel
        if panel is not None:
            save_post_clone_hook(panel)

    def migrate_data_dir(self):
        panel = self._panel
        if panel is not None:
            migrate_data_dir_flow(panel)


def add_data_dir_section(owner, layout, row: int) -> int:
    """数据目录：私密性状态 + 一键迁移。返回下一可用行号。

    G76：`is_data_dir_private` / `migrate_data_dir`（G44-6）此前**零生产调用点** ——
    能力写好了但用户完全够不到；现在给出状态显示与显式迁移入口（含二次确认）。
    """
    from ..db.settings import is_data_dir_private
    data_dir = str(getattr(owner, "data_dir", "") or "")
    try:
        private = bool(is_data_dir_private(data_dir)) if data_dir else True
    except Exception:  # noqa: BLE001 —— 路径解析失败不该让设置页打不开
        private = True
    owner.lbl_data_dir = QLabel(
        tr("数据目录：") + data_dir
        + (tr("（私有：仅当前用户可读）") if private
           else tr("（公开目录：同机其它用户可读，建议迁移）")))
    owner.lbl_data_dir.setObjectName("muted")
    owner.lbl_data_dir.setWordWrap(True)
    layout.addWidget(owner.lbl_data_dir, row, 0, 1, 2)
    owner.btn_migrate_data_dir = QPushButton(tr("迁移到用户私有目录"))
    owner.btn_migrate_data_dir.setEnabled(not private)
    owner.btn_migrate_data_dir.setToolTip(
        tr("把设置/数据库/日志搬到用户私有目录；不覆盖同名文件，源目录保留，迁移后需重启"))
    owner.btn_migrate_data_dir.clicked.connect(owner.settings_panel.wiring.migrate_data_dir)
    layout.addWidget(owner.btn_migrate_data_dir, row + 1, 0)
    return row + 2


def save_post_clone_hook(panel) -> None:
    """克隆后钩子保存。**会执行任意命令**，因此从空改成非空前必须显式确认。

    安全性：① 默认空；② 二次确认，默认按钮 = No；③ 无可见窗口时不弹模态
    （headless 下 `QMessageBox.exec` 会永久阻塞 —— G68-4 的教训），改为写日志留痕。
    """
    owner = panel.owner
    edit = getattr(owner, "edit_post_hook", None)
    new_val = edit.text().strip() if edit is not None else ""
    old_val = str(getattr(owner.settings, "post_clone_hook", "") or "")
    if new_val and new_val != old_val:
        can_modal = getattr(owner, "_can_show_modal", lambda: True)()
        if not can_modal:
            try:
                owner._emit_log("", LogLevel.WARN,
                                f"克隆后钩子已更新（无可见窗口，未弹确认）：{new_val}")
            except Exception:  # noqa: BLE001 —— 日志失败不该拦住保存
                pass
        else:
            ret = QMessageBox.warning(
                owner, tr("确认克隆后钩子"),
                tr("这条命令会在**每个**仓库克隆成功后，在该仓库目录执行：\n\n{hook}\n\n"
                   "它拥有你当前用户的权限（可删除文件、联网、改系统设置）。\n"
                   "确认是你自己写的脚本再继续。").format(hook=new_val),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                if edit is not None:
                    edit.setText(old_val)
                return
    owner.settings.post_clone_hook = new_val
    owner.settings_store.save(owner.settings)


def _private_target_dir() -> str:
    r"""迁移目标目录：与 `gcm/__main__.get_data_dir()` 保持同一策略。

    Windows → `%APPDATA%\Git-clone-Max`；POSIX → `$XDG_DATA_HOME/Git-clone-Max`，
    两者都没有时退回 `~/.local/share/Git-clone-Max`；连家目录都取不到则返回空串
    （调用方必须据空串拒绝迁移，不得瞎猜一个位置）。
    """
    import os
    from pathlib import Path

    for var in ("APPDATA", "XDG_DATA_HOME"):
        base = os.environ.get(var)
        if base:
            return str(Path(base) / "Git-clone-Max")
    home = os.path.expanduser("~")
    if home and home != "~":
        return str(Path(home) / ".local" / "share" / "Git-clone-Max")
    return ""


def migrate_data_dir_flow(panel) -> None:
    """把数据目录迁到 `%APPDATA%\\Git-clone-Max`（G44-6 的能力此前零入口）。

    安全约束（不可逆操作的前置条件）：
    ① 任务运行中直接拒绝（DB/进度文件正在被写）；
    ② 二次确认，且把源/目标路径原样展示，默认按钮 = No；
    ③ `migrate_data_dir` 自身不覆盖同名文件、不删源目录 → 失败可回退；
    ④ 迁移后必须重启才生效（数据目录在启动时解析，唯一来源 `get_data_dir()`）。
    """
    from pathlib import Path

    from ..db.settings import migrate_data_dir
    owner = panel.owner
    if getattr(owner, "busy", False):
        QMessageBox.warning(owner, tr("迁移数据目录"),
                            tr("任务运行中不能迁移，请等待完成或先取消。"))
        return
    src = str(getattr(owner, "data_dir", "") or "")
    dst = _private_target_dir()
    if not src or not dst:
        QMessageBox.warning(owner, tr("迁移数据目录"),
                            tr("无法确定数据目录或用户私有目录，已取消。"))
        return
    try:
        same = Path(src).resolve() == Path(dst).resolve()
    except OSError:
        same = False
    if same:
        QMessageBox.information(owner, tr("迁移数据目录"),
                                tr("数据目录已在私有目录，无需迁移。"))
        return
    ret = QMessageBox.question(
        owner, tr("迁移数据目录"),
        tr("将把数据目录内容迁移到：\n{dst}\n\n当前目录：\n{src}\n\n"
           "同名文件不会被覆盖，源目录保留；迁移完成后请重启应用。").format(
            dst=dst, src=src),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No)
    if ret != QMessageBox.StandardButton.Yes:
        return
    ok, msg = migrate_data_dir(src, dst)
    if ok:
        QMessageBox.information(
            owner, tr("迁移完成"),
            tr("{msg}。请手动重启应用以使用新数据目录。").format(msg=msg))
    else:
        QMessageBox.warning(owner, tr("迁移失败"), msg)


__all__ = ["add_data_dir_section", "save_post_clone_hook", "migrate_data_dir_flow"]
