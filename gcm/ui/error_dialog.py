"""统一错误展示（Rev12 U-18）：裸 `QMessageBox.warning(str(e))` 的替代件。

**为什么需要**：失败面板（`failure_panel`）已按 `errors.ErrorInfo` 渲染
三段式（发生了什么 / 为什么 / 怎么办）+ 真按钮（去设置 / 诊断包 / 重试），
但同类的单发错误散在各面板里仍是 `warning(title, str(e))` —— 一行原始
异常文本，用户不知道下一步能做什么。本组件把「结构化错误」带进对话框：

- 文本：`ErrorInfo.display_lines()` 三段式（title/cause/action）；
- 动作：按 `action_kind` 渲染真按钮（与失败面板同一份判据，不写第二份映射）；
- 兜底：分类器拿不准时退化为纯文本框（不比原来更差）。

**边界**：只替换**带异常文本**的失败类弹窗（warning/critical + str(e)）；
「输入区为空」这类提示型 information 不属于错误，不改造。
"""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..app.errors import classify_error
from ..i18n import tr


def classify_exception(e: BaseException, context: str = "") -> Any:
    """把任意异常映射成 `ErrorInfo`（U-18 唯一分类入口，复用 classify_error）。

    `context` 是发生场景的一句话（如「保存任务清单」），拼进 raw 供三段式引用；
    分类不准时 `classify_error` 自会落 CAT_OTHER → 建议「导出诊断包」，比
    裸 `str(e)` 多出「怎么办」一段。
    """
    detail = f"{context}：{e}" if context else str(e)
    return classify_error(str(e), detail)



def _kind_button(info: Any, parent: QWidget) -> Optional[QPushButton]:
    """按 action_kind 渲染单个动作按钮（与 failure_panel 同判据，无第二份映射）。

    返回 None 表示该类别没有本地可做的动作（copy_diag 类由「复制详情」承担）。
    跳设置复用主窗既有的 `_goto_settings_section`（C2-4 引入，失败面板同款）；
    parent 不是主窗（测试/内嵌场景）就没有这个方法，按钮自动省略 —— 动作缺失
    不该让错误本身展示不出来。
    """
    kind = getattr(info, "action_kind", "none")
    if kind == "retry":
        return None  # 单发错误没有「这一类」可重试的队列上下文
    if kind in ("open_settings", "switch_mirror"):
        goto = getattr(parent, "_goto_settings_section", None)
        if goto is None:
            return None
        btn = QPushButton(tr("打开网络设置"))
        btn.setToolTip(tr("认证/代理类问题通常要去设置页更新 Token 或代理"))
        btn.clicked.connect(lambda: goto("network"))
        return btn
    return None


class ErrorDialog(QDialog):
    """三段式错误对话框：主文本 + 可选动作按钮 + 「复制详情」。

    独立 QDialog 而不是 QMessageBox：要放两个按钮且语义不同
    （动作按钮 vs 关闭），QMessageBox 的按钮语义对不上。
    """

    def __init__(self, info: Any, parent: Optional[QWidget] = None,
                 title: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle(title or tr("操作失败"))
        self._info = info
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        head = QLabel(tr(info.title) if getattr(info, "title", "") else tr("操作失败"))
        head.setObjectName("h2")
        lay.addWidget(head)

        body = QLabel("\n".join(
            tr(x) for x in (
                (tr("为什么：") + tr(getattr(info, "cause", ""))),
                (tr("怎么办：") + tr(getattr(info, "action", ""))),
            ) if x.strip() != tr("为什么：") and x.strip() != tr("怎么办：")))
        body.setWordWrap(True)
        lay.addWidget(body)

        raw = str(getattr(info, "raw", "") or "").strip()
        # cause 是分类器生成的人话，raw 是原始报错；分类器用 raw 拼出 cause 时
        # 两者内容重叠，把 raw 再显示一遍是噪音 —— 只在 raw 携带新信息时展示。
        cause = str(getattr(info, "cause", "") or "").strip()
        if raw and raw not in cause and cause not in raw:
            raw_lbl = QLabel(raw[:600])
            raw_lbl.setWordWrap(True)
            raw_lbl.setObjectName("muted")
            lay.addWidget(raw_lbl)

        row = QHBoxLayout()
        action = _kind_button(info, self)
        if action is not None:
            row.addWidget(action)
        row.addStretch(1)
        btn_copy = QPushButton(tr("复制详情"))
        btn_copy.setToolTip(tr("把三段式说明与原始错误文本复制到剪贴板（已打码）"))
        btn_copy.clicked.connect(self._copy_detail)
        row.addWidget(btn_copy)
        lay.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.clicked.connect(self._on_close)
        lay.addWidget(bb)

    def _on_close(self, *_a) -> None:
        self.accept()

    def _copy_detail(self) -> None:
        from PyQt6.QtWidgets import QApplication
        info = self._info
        text = "\n".join(x for x in (
            str(getattr(info, "title", "")),
            str(getattr(info, "cause", "")),
            str(getattr(info, "action", "")),
            str(getattr(info, "raw", "")),
        ) if x.strip())
        QApplication.clipboard().setText(text)


def show_error_message(parent: QWidget, message: str, *, context: str = "",
                       title: Optional[str] = None) -> None:
    """str 载荷入口：底层 (ok, msg) 返回值 / worker 信号传回的错误文本。

    与 `show_error` 同一分类链（classify_error），只是载荷不是异常对象。
    worker 传回的字符串通常已是异常的 `str()` 产物（如扫描线程把
    异常转成信号载荷），分类器对它同样能命中网络/认证/权限类判据。
    """
    try:
        info = classify_error(message, f"{context}：{message}" if context else message)
    except Exception:       # noqa: BLE001 —— 分类失败退化为纯文本（不比原来差）
        QMessageBox.warning(parent, title or tr("操作失败"), message)
        return
    try:
        dlg = ErrorDialog(info, parent=parent, title=title)
        dlg.exec()
    except Exception:       # noqa: BLE001 —— 对话框构建失败退化为纯文本
        QMessageBox.warning(parent, title or tr("操作失败"), message)


def show_error(parent: QWidget, e: BaseException, *, context: str = "",
               title: Optional[str] = None) -> None:
    """一键入口：分类 → 展示。模态（错误必须被确认，与既有弹窗语义一致）。"""
    try:
        info = classify_exception(e, context)
    except Exception:       # noqa: BLE001 —— 分类失败退化为纯文本（不比原来差）
        QMessageBox.warning(parent, title or tr("操作失败"), str(e))
        return
    try:
        dlg = ErrorDialog(info, parent=parent, title=title)
        dlg.exec()
    except Exception:       # noqa: BLE001 —— 对话框构建失败退化为纯文本
        QMessageBox.warning(parent, title or tr("操作失败"), str(e))
