"""撤销入口（Rev12 U-06/U-07，尼尔森原则③）。

**为什么是浮条而不是模态框**：撤销必须**无摩擦**。模态确认框打断当前动作，
用户会条件反射地点「确定」把它关掉 —— 那时撤销就失效了。底部浮条（Toast）
不抢焦点，5 秒内点一下就能回退，超时自动消失（这条范式来自 Rev10 §6.2 Toast 纪律）。

**为什么不用 QSystemTrayIcon.showMessage**：托盘气泡会被系统「专注助手」吞掉，
而且点击回调跨平台不一致。浮条是窗口内控件，行为确定、可测试。
"""
from __future__ import annotations

import weakref
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..i18n import tr

#: 浮条停留时长（毫秒）。5s 是 Material/Windows 通知的常见下限，低于 3s 用户来不及反应。
TOAST_MS = 5000


class UndoBar(QFrame):
    """底部撤销浮条：一句「已删除 3 条记录」+ 一个「撤销」按钮。"""

    def __init__(self, owner: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # **必须弱引用 owner**：main_window 持有 undo_bar，undo_bar 若强引用
        # main_window 就构成引用环 —— 窗口只能等循环 GC 回收，而循环 GC 在任意
        # 分配点触发（含 QApplication.processEvents 内部），Qt 曾在事件分发中途
        # 销毁控件导致 SIGSEGV（见 CLAUDE.md §6 的崩溃根因记录）。守卫：
        # tests/test_g69_regressions.TestG69_18NoMainWindowRefCycle。
        self._owner_ref = weakref.ref(owner)
        self._record_id = ""
        self.setObjectName("undoBar")
        self.setFrameShape(QFrame.Shape.NoFrame)
        # 浮条必须能盖在中央控件上，否则只能挤在布局里推挤其它控件
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 隐藏**并让布局收回高度**。仅 hide() 时布局仍按 sizeHint 预留一条空白
        # （实测 30px 永久空白），所以同时把垂直尺寸策略设为 Ignored —— 这正是
        # 「隐藏态不该占位、显示态自己撑开一行」的标准做法。
        from PyQt6.QtWidgets import QSizePolicy as _SP
        self.setSizePolicy(_SP.Policy.Preferred, _SP.Policy.Maximum)
        self.hide()

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(12)
        self.msg = QLabel("", self)
        self.msg.setObjectName("undoBarMsg")
        self.msg.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.msg, 1)
        self.btn_undo = QPushButton(tr("撤销"), self)
        self.btn_undo.setObjectName("undoBarBtn")
        self.btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_undo.clicked.connect(self._on_undo)   # 绑定方法，不成引用环
        row.addWidget(self.btn_undo, 0)
        self.btn_close = QPushButton(tr("关闭"), self)
        self.btn_close.setObjectName("undoBarClose")
        self.btn_close.setFlat(True)
        self.btn_close.clicked.connect(self.hide)      # 绑定方法
        row.addWidget(self.btn_close, 0)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TOAST_MS)
        self._timer.timeout.connect(self.hide)         # 绑定方法

    # ------------------------------------------------------------------ 对外
    def offer(self, record_id: str, text: str) -> None:
        """展示一条可撤销操作。重复调用会替换上一条（不做队列，避免堆叠）。"""
        self._record_id = record_id
        self.msg.setText(text)
        self.btn_undo.setEnabled(True)
        # 本控件在布局里（tabs 与状态栏之间），显示/隐藏由布局自动让位，
        # 不需要也不应该用绝对定位盖住内容（覆盖式实测评测过会挡住底部按钮）。
        # 用 show() + updateGeometry：父窗口尚未 show() 时 setVisible(True) 不生效，
        # 且布局必须收到几何失效通知才会把这一行让出来。
        self.show()
        self.updateGeometry()
        self._timer.start()

    def clear(self) -> None:
        self._record_id = ""
        self._timer.stop()
        self.hide()

    def _on_undo(self) -> None:
        rid = self._record_id
        if not rid:
            self.hide()
            return
        self.btn_undo.setEnabled(False)
        owner = self._owner_ref()
        if owner is None:                      # 主窗已释放：浮条自己也该消失
            self.hide()
            return
        runner = getattr(owner, "undo_op", None)
        if runner is None:
            self.msg.setText(tr("撤销入口未接线"))
            self._timer.start()
            return
        ok, text = runner(rid)
        self.msg.setText(text)
        if ok:
            # 成功：1.5s 后自动消失，让用户看到结果
            self._record_id = ""
            self._timer.start(1500)
        else:
            # 失败：保持可见，让用户看清原因（原则①：状态必须可见）
            self._timer.stop()

    def reposition(self, parent_rect: Any, *, bottom_offset: int = 24) -> None:
        """贴到父控件底部。

        `bottom_offset` 由主窗按底部**按钮行高度**传入 —— 实测过写死 24px 会把
        「打开克隆目录 / 失败汇总 / 重试队列 / 诊断包」整排按钮盖住（截图可见），
        那是可用性改进反而制造可用性缺陷。
        """
        try:
            w = min(720, max(360, int(parent_rect.width()) - 48))
            h = self.sizeHint().height()
            x = (int(parent_rect.width()) - w) // 2
            y = int(parent_rect.height()) - h - max(8, int(bottom_offset))
            self.setGeometry(x, y, w, h)
        except Exception as e:  # noqa: BLE001 —— 布局失配不该影响撤销功能本身
            from ..app.applog import warn as _warn
            _warn(f"撤销浮条重定位失败（浮条可能位置不对，功能不受影响）：{e}")


def build_oplog_dialog(owner: Any) -> Any:
    """「操作历史…」对话框：列出最近可撤销记录，逐条给撤销按钮。

    独立函数而不继承 QDialog，是为了让主窗只持有一个 `exec()` 调用点，
    避免把对话框状态混进 2980 行的主窗类里。
    """
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QScrollArea

    from ..app.undo import action_label, can_undo
    from ..util.oplog import default_oplog

    oplog = getattr(owner, "oplog", None) or default_oplog(getattr(owner, "data_dir", "."))
    dlg = QDialog(owner)
    dlg.setWindowTitle(tr("操作历史"))
    dlg.resize(560, 420)
    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(14, 14, 14, 14)
    outer.setSpacing(10)

    tip = QLabel(tr("最近可撤销的操作（最多 20 条）。撤销只回退记录，不动磁盘上的仓库目录。"), dlg)
    tip.setWordWrap(True)
    outer.addWidget(tip)

    area = QScrollArea(dlg)
    area.setWidgetResizable(True)
    holder = QWidget(area)
    col = QVBoxLayout(holder)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(6)

    records = oplog.recent(20, undoable_only=True)
    if not records:
        from .empty_state import build_empty_state
        col.addWidget(build_empty_state(
            tr("没有可撤销的操作"), tr("做过可撤销的操作后会出现在这里"), parent=holder))
    for rec in records:
        line = QFrame(holder)
        h = QHBoxLayout(line)
        h.setContentsMargins(8, 6, 8, 6)
        label = QLabel(
            f"{action_label(rec.action)} · {rec.target}", line)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setToolTip(rec.summary or rec.target)
        h.addWidget(label, 1)
        btn = QPushButton(tr("撤销"), line)
        btn.setEnabled(can_undo(rec))
        btn.setProperty("oplog_id", rec.id)
        btn.clicked.connect(dlg.accept)   # 只关闭，真正的撤销交给主窗（单点执行）
        h.addWidget(btn, 0)
        col.addWidget(line)
    col.addStretch(1)
    area.setWidget(holder)
    outer.addWidget(area, 1)

    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
    box.rejected.connect(dlg.reject)
    box.accepted.connect(dlg.accept)
    outer.addWidget(box)
    return dlg


__all__ = ["TOAST_MS", "UndoBar", "build_oplog_dialog"]
