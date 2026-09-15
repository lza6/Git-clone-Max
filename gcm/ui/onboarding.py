"""G36-1 首次运行向导：三步引导对话框（选下载目录 → 选模式 → 完成）。

纯 UI 组件，不碰 settings / 文件持久化——first_run_done 的写入由主控
（main_window）负责。本模块只提供 selected_dir / selected_mode / skipped
以及 accepted 信号供主控读取。
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

from .theme import QSS

# 克隆模式选项：combo 显示文本（主控可据此映射 full/shallow value）
MODE_FULL = "满量克隆"
MODE_SHALLOW = "浅克隆"


class OnboardingDialog(QDialog):
    """三步首次运行向导。

    步骤 1 选择下载目录；步骤 2 选择克隆模式；步骤 3 完成摘要。
    「跳过」始终可见 → skip() 关闭并置 skipped=True。
    """

    def __init__(self, parent=None, default_dir: str = ""):
        super().__init__(parent)
        self.default_dir = default_dir or ""
        self.skipped = False
        self.setWindowTitle("欢迎使用 Git-clone-Max")
        self.resize(520, 340)
        self.setMinimumSize(460, 300)
        self.setStyleSheet(QSS)
        self._build_ui()

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setSpacing(12)

        title = QLabel("👋 欢迎使用 Git-clone-Max")
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
        self.btn_skip = QPushButton("跳过")
        self.btn_skip.clicked.connect(self.skip)
        footer.addWidget(self.btn_skip)
        footer.addStretch(1)
        self.btn_back = QPushButton("上一步")
        self.btn_back.clicked.connect(self._on_back)
        footer.addWidget(self.btn_back)
        self.btn_next = QPushButton("下一步")
        self.btn_next.clicked.connect(self._on_next)
        footer.addWidget(self.btn_next)
        v.addLayout(footer)

        self._on_page_changed(0)

    def _build_step1(self) -> QWidget:
        """步骤 1：选择下载目录。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lab = QLabel("选择下载目录")
        lab.setObjectName("accent")
        lay.addWidget(lab)
        hint = QLabel("克隆的仓库将保存到该目录，稍后可在设置中修改。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        row = QHBoxLayout()
        self.dir_edit = QLineEdit(self.default_dir)
        self.dir_edit.setPlaceholderText("请选择下载目录")
        row.addWidget(self.dir_edit, 1)
        self.btn_browse = QPushButton("浏览…")
        self.btn_browse.clicked.connect(self._on_browse)
        row.addWidget(self.btn_browse)
        lay.addLayout(row)
        lay.addStretch(1)
        return page

    def _build_step2(self) -> QWidget:
        """步骤 2：初始克隆模式。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lab = QLabel("选择克隆模式")
        lab.setObjectName("accent")
        lay.addWidget(lab)
        hint = QLabel("满量克隆保留完整历史；浅克隆只拉取最新代码，速度更快。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([MODE_FULL, MODE_SHALLOW])
        self.mode_combo.setCurrentIndex(0)
        lay.addWidget(self.mode_combo)
        lay.addStretch(1)
        return page

    def _build_step3(self) -> QWidget:
        """步骤 3：完成摘要 + 开始使用。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lab = QLabel("完成设置 🎉")
        lab.setObjectName("accent")
        lay.addWidget(lab)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        lay.addWidget(self.summary_label)
        self.btn_start = QPushButton("开始使用")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.accept)
        lay.addWidget(self.btn_start)
        lay.addStretch(1)
        return page

    # ------------------------------------------------------------ 导航
    def _on_page_changed(self, index: int) -> None:
        """页面切换时刷新按钮状态与步骤 3 摘要。"""
        self.btn_back.setEnabled(index > 0)
        self.btn_next.setText("完成" if index == 2 else "下一步")
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
        path = QFileDialog.getExistingDirectory(self, "选择下载目录", start)
        if path:
            self.dir_edit.setText(path)

    def _refresh_summary(self) -> None:
        self.summary_label.setText(
            f"下载目录：{self.selected_dir}\n克隆模式：{self.selected_mode}")

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
        """步骤 2 combo 当前文本（满量克隆 / 浅克隆）。"""
        return self.mode_combo.currentText()
