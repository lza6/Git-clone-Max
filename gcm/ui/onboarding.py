"""G36-1 首次运行向导：三步引导对话框（选下载目录 → 选模式 → 完成）。

纯 UI 组件，不碰 settings / 文件持久化——first_run_done 的写入由主控
（main_window）负责。本模块只提供 selected_dir / selected_mode / skipped
以及 accepted 信号供主控读取。

G71-5：全部用户可见文案走 `tr()`（原先硬编码中文，与项目 i18n 约定冲突）；
模式值改为**语义值**（full/shallow）并存进 combo 的 `userData`，避免主控
用 `text.startswith("浅")` 这种依赖中文文案的脆弱判断（切英文即失效）。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr

# 克隆模式语义值（存 combo userData；主控按值判断，不依赖显示文案）
MODE_FULL = "full"
MODE_SHALLOW = "shallow"
# 兼容旧调用方：这两个常量在 v9.7.0 前是中文显示文本
MODE_FULL_TEXT = "满量克隆"
MODE_SHALLOW_TEXT = "浅克隆"


class OnboardingDialog(QDialog):
    """三步首次运行向导。

    步骤 1 选择下载目录；步骤 2 选择克隆模式；步骤 3 完成摘要。
    「跳过」始终可见 → skip() 关闭并置 skipped=True。
    """

    def __init__(self, parent=None, default_dir: str = ""):
        super().__init__(parent)
        from .theme import apply_parent_theme
        apply_parent_theme(self)
        self.default_dir = default_dir or ""
        self.skipped = False
        self.setWindowTitle(tr("欢迎使用 Git-clone-Max"))
        from .layout import fit_dialog_to_screen
        fit_dialog_to_screen(self, 520, 340, 460, 300)
        self._build_ui()

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setSpacing(12)

        title = QLabel(tr("欢迎使用 Git-clone-Max"))
        title.setObjectName("pageTitle")
        v.addWidget(title)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1())
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())
        self.stack.currentChanged.connect(self._on_page_changed)
        v.addWidget(self.stack, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.btn_skip = QPushButton(tr("跳过"))
        self.btn_skip.clicked.connect(self.skip)
        footer.addWidget(self.btn_skip)
        footer.addStretch(1)
        self.btn_back = QPushButton(tr("上一步"))
        self.btn_back.clicked.connect(self._on_back)
        footer.addWidget(self.btn_back)
        self.btn_next = QPushButton(tr("下一步"))
        self.btn_next.clicked.connect(self._on_next)
        footer.addWidget(self.btn_next)
        v.addLayout(footer)

        self._on_page_changed(0)

    def _build_step1(self) -> QWidget:
        """步骤 1：选择下载目录。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lab = QLabel(tr("选择下载目录"))
        lab.setObjectName("accent")
        lay.addWidget(lab)
        hint = QLabel(tr("克隆的仓库将保存到该目录，稍后可在设置中修改。"))
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        row = QHBoxLayout()
        self.dir_edit = QLineEdit(self.default_dir)
        self.dir_edit.setPlaceholderText(tr("请选择下载目录"))
        # G74：长路径默认把光标放末尾会只显示尾部（看不到盘符），
        # 改为显示开头并给完整路径 tooltip。
        self.dir_edit.setCursorPosition(0)
        if self.default_dir:
            self.dir_edit.setToolTip(self.default_dir)
        row.addWidget(self.dir_edit, 1)
        self.btn_browse = QPushButton(tr("浏览…"))
        self.btn_browse.clicked.connect(self._on_browse)
        row.addWidget(self.btn_browse)
        lay.addLayout(row)
        lay.addStretch(1)
        # G74：步骤 1 内容比步骤 2/3 矮，QStackedWidget 取最高页后这里会留下大片空白。
        # 用一条真实有用的说明填位，而不是让用户面对空面板。
        tip = QLabel(tr("小提示：仓库按「作者__仓库」命名保存；"
                        "再次下载同一地址会走增量更新，本地改动不会被覆盖。"))
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        return page

    def _build_step2(self) -> QWidget:
        """步骤 2：初始克隆模式。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lab = QLabel(tr("选择克隆模式"))
        lab.setObjectName("accent")
        lay.addWidget(lab)
        hint = QLabel(tr("满量克隆保留完整历史；浅克隆只拉取最新代码，速度更快。"))
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.mode_combo = QComboBox()
        # 显示文案可翻译，值保持稳定语义（主控只认 full/shallow）
        self.mode_combo.addItem(tr(MODE_FULL_TEXT), MODE_FULL)
        self.mode_combo.addItem(tr(MODE_SHALLOW_TEXT), MODE_SHALLOW)
        self.mode_combo.setCurrentIndex(0)
        lay.addWidget(self.mode_combo)
        lay.addStretch(1)
        return page

    def _build_step3(self) -> QWidget:
        """步骤 3：完成摘要 + 开始使用。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lab = QLabel(tr("完成设置"))
        lab.setObjectName("accent")
        lay.addWidget(lab)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        lay.addWidget(self.summary_label)
        self.btn_start = QPushButton(tr("开始使用"))
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.accept)
        lay.addWidget(self.btn_start)
        lay.addStretch(1)
        return page

    # ------------------------------------------------------------ 导航
    def _on_page_changed(self, index: int) -> None:
        """页面切换时刷新按钮状态与步骤 3 摘要。"""
        self.btn_back.setEnabled(index > 0)
        self.btn_next.setText(tr("完成") if index == 2 else tr("下一步"))
        if index == 2:
            self._refresh_summary()

    def _on_next(self) -> None:
        """下一步；位于步骤 3 时等同于「完成」。"""
        if self.stack.currentIndex() >= 2:
            self.accept()
            return
        self.stack.setCurrentIndex(self.stack.currentIndex() + 1)

    def _on_back(self) -> None:
        if self.stack.currentIndex() > 0:
            self.stack.setCurrentIndex(self.stack.currentIndex() - 1)

    def _on_browse(self) -> None:
        """浏览目录：写入模块内引用的 QFileDialog（测试可 mock）。"""
        start = self.dir_edit.text().strip() or self.default_dir
        path = QFileDialog.getExistingDirectory(self, tr("选择下载目录"), start)
        if path:
            self.dir_edit.setText(path)

    def _refresh_summary(self) -> None:
        self.summary_label.setText(
            tr("下载目录：{dir}\n克隆模式：{mode}").format(
                dir=self.selected_dir, mode=self.selected_mode))

    def skip(self) -> None:
        """跳过向导：标记已跳过并直接关闭（持久化由主控决定）。"""
        self.skipped = True
        self.close()

    # ------------------------------------------------------------ 属性（供主控读取）
    @property
    def selected_dir(self) -> str:
        """步骤 1 目录文本；为空时回退 default_dir。"""
        return self.dir_edit.text().strip() or self.default_dir

    @property
    def selected_mode(self) -> str:
        """步骤 2 combo 当前显示文本（中文/英文随语言）。"""
        return self.mode_combo.currentText()

    @property
    def selected_mode_value(self) -> str:
        """步骤 2 的语义值：`full` / `shallow`（主控应优先用它）。"""
        value = self.mode_combo.currentData()
        return str(value) if value in (MODE_FULL, MODE_SHALLOW) else MODE_FULL
