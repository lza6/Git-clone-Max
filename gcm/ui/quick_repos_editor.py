"""快捷填充行内编辑器（Rev11 V-20，D-7）—— 替代「逗号串弹框」。

**D-7 边界（写在这里防越界）**：只改**显示标签**（`settings.quick_repos` 键内的
字符串），**绝不动磁盘目录名**。本模块写盘只允许经过
`owner.settings_store.save(owner.settings)`（自带跨进程锁），
且只允许触碰 `settings.quick_repos` 一个字段 —— 守卫
`tests/test_rev11_w4.py::TestQuickReposEditor` 断言这一点。

**ToolTip 必须显示真实磁盘路径**（显示名≠目录名的困惑缓解，D-7 附属要求）：
每个 chip 的 tooltip = `owner.settings.download_dir / owner__repo` 形态的落点。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
)

from ..i18n import tr


def parse_quick_repos(text: str) -> list[str]:
    """逗号/换行分隔 → 去空去重列表（与旧弹框同口径，供导入/粘贴）。"""
    seen: list[str] = []
    for raw in (text or "").replace("\n", ",").split(","):
        item = raw.strip().strip("/")
        if item and item not in seen:
            seen.append(item)
    return seen


def _suggest_folder(repo: str) -> str:
    """`owner/repo` → `owner__repo`（与引擎落点同形态，仅用于 tooltip 展示）。"""
    return repo.rstrip("/").replace("\\", "/").replace("/", "__")


class QuickReposEditor(QDialog):
    """行内 chip 编辑对话框：逐条增/删/改名（显示标签），确认后一次性写盘。"""

    def __init__(self, owner, parent=None):
        super().__init__(parent or owner)
        self._owner = owner
        self.setWindowTitle(tr("编辑快捷填充"))
        from .layout import fit_dialog_to_screen
        fit_dialog_to_screen(self, 520, 420, 420, 300)

        v = QVBoxLayout(self)
        hint = QLabel_wrap(tr(
            "每行一个（owner/repo）。这里改的是显示标签，"
            "不会改动磁盘上的任何目录。"))
        v.addWidget(hint)

        self.edit = QPlainTextEdit_compat(owner)
        cur = getattr(owner.settings, "quick_repos", None) or ()
        self.edit.setPlainText("\n".join(str(r) for r in cur))
        v.addWidget(self.edit, 1)

        # 实时预览：每行的落点目录（tooltip 同款口径）
        self.preview = QLabel_wrap("")
        self.preview.setObjectName("muted")
        v.addWidget(self.preview)
        self.edit.textChanged.connect(self._refresh_preview)
        self._refresh_preview()

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _refresh_preview(self) -> None:
        repos = parse_quick_repos(self.edit.toPlainText())
        shown = "; ".join(f"{r} → {_suggest_folder(r)}" for r in repos[:6])
        if len(repos) > 6:
            shown += tr(" … 共 {n} 条").format(n=len(repos))
        self.preview.setText(shown or tr("（空 = 不显示快捷按钮）"))

    def result_repos(self) -> list[str]:
        return parse_quick_repos(self.edit.toPlainText())


def QLabel_wrap(text: str):
    from PyQt6.QtWidgets import QLabel
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.TextFormat.PlainText)
    return lbl


def QPlainTextEdit_compat(owner):
    """延迟导入（download_layout 顶部只引了部分 Qt 类）。"""
    from PyQt6.QtWidgets import QPlainTextEdit
    edit = QPlainTextEdit()
    edit.setAccessibleName(tr("快捷填充列表（每行一条）"))
    return edit


def edit_quick_repos_inline(owner) -> int:
    """V-20 新入口：行内编辑器。返回新按钮数（与旧入口同语义）。"""
    dlg = QuickReposEditor(owner)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return len(getattr(owner, "_quick_fill_buttons", None) or [])
    owner.settings.quick_repos = dlg.result_repos()
    owner.settings_store.save(owner.settings)
    from .download_layout import rebuild_quick_fill
    return rebuild_quick_fill(owner)


__all__ = ["QuickReposEditor", "edit_quick_repos_inline",
           "parse_quick_repos", "_suggest_folder"]
