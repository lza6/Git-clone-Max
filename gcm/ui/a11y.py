"""无障碍命名落地（Rev14 R14-04）：主操作显式命名 + 输入控件按标签推导。

**先纠正一个常见误判**：Qt 对 `QPushButton` / `QToolButton` / `QCheckBox` /
`QRadioButton` 的 accessible name **默认取 `text()`**，所以「按钮没有
`accessibleName`」并不等于屏幕阅读器读不出用途。真正读不出的是**没有可见文本**
的输入控件（`QLineEdit` / `QSpinBox` / `QComboBox`）—— 读屏只会报「编辑框」。

因此这里做两件事，顺序有讲究：

1. **主操作显式命名**（`apply_main_action_names`）：这 14 个入口是全流程的关键动作，
   但按钮文字会变（「暂停」/「继续」），拿它当稳定名字不可靠；显式给
   `accessibleName` + `accessibleDescription`，读屏才有确定的读法。
2. **输入控件按标签推导**（`autoname_inputs`）：优先 `QLabel.setBuddy()` 绑定，
   其次同一 layout 中紧邻的前一个标签文本，再次 `toolTip()` 首行。
   **推不出来就留空**，由 `scripts/a11y_audit.py` 的棘轮计数盯着 —— 不编造名字。

不改已有 `accessibleName`：人工写的命名永远优先于机器推导。
"""
from __future__ import annotations

import re
from typing import Any

_WS = re.compile(r"\s+")
_ACCEL = re.compile(r"&(?!&)")
_ELLIPSIS = ("…", "...")


def _clean(text: Any) -> str:
    """去掉助记符 `&`、结尾省略号与冒号、压缩空白。

    读屏不需要「并发数（1–32）：」里的那个冒号 —— 名字念成「并发数（1–32）」即可；
    「载入…」的省略号同理（省略号表示"会弹对话框"，读屏念出来是噪音）。
    """
    s = _WS.sub(" ", str(text or "")).strip()
    s = _ACCEL.sub("", s)
    for dot in _ELLIPSIS:
        if s.endswith(dot):
            s = s[: -len(dot)].rstrip()
    return s.rstrip(":：").strip()


#: 主操作：`属性名 → (accessibleName, accessibleDescription)`。
#: 描述写「按下去会发生什么」，不是重复名字 —— 读屏用户靠它判断该不该点。
MAIN_ACTIONS: dict[str, tuple[str, str]] = {
    "btn_save_list": ("保存当前清单", "把输入区里的仓库地址保存成一个可复用的清单文件"),
    "combo_load_list": ("选择已保存的清单", "切换要载入的任务清单"),
    "btn_load_list": ("载入选中清单", "把选中清单里的仓库地址填进输入区"),
    "btn_star_import": ("导入 Star 仓库", "拉取 GitHub 上自己 Star 过的仓库并填进输入区"),
    "btn_start": ("开始下载或更新", "按当前输入区与选项开始并行克隆；已在管理的仓库走增量更新"),
    "btn_cancel": ("取消全部任务", "停止当前批次的全部任务，已完成的仓库保留"),
    "btn_clear": ("清空输入区", "只清空输入框文本，不影响已经下载的仓库"),
    "btn_open": ("打开克隆目录", "在资源管理器中打开本次下载的根目录"),
    "btn_failures": ("查看失败汇总", "按错误类型分组查看本批次失败原因"),
    "btn_retry_queue": ("打开重试队列", "查看等待自动重试的任务及其下次重试时间"),
    "btn_diag_bundle": ("生成诊断包", "把脱敏后的日志与配置摘要打包，用于反馈问题"),
    "btn_edit_quick": ("编辑快捷填充", "增删改输入区上方的快捷填充标签"),
    "btn_pause_batch": ("暂停或继续批次", "暂停后不会启动新的仓库，已在跑的仓库继续完成"),
    "btn_more_tools": ("更多工具菜单", "窗口太矮时，工具按钮会收进这个菜单"),
}

def apply_main_action_names(owner: Any) -> int:
    """给 `owner` 上的主操作补 `accessibleName` / `accessibleDescription`。

    已有人工命名的**只补缺失的描述**，不覆盖名字。返回本轮设置的名字条数。

    没有 `try/except`：这里全是 `QWidget` 属性的直接赋值，没有 I/O、没有可失败
    路径（异常预算棘轮只降不升，纯命名不该占额度）。
    """
    from ..i18n import tr  # 延迟导入：本模块也会被脚本单独 import

    named = 0
    for attr, (name, desc) in MAIN_ACTIONS.items():
        w = getattr(owner, attr, None)
        if w is None:
            continue
        if not str(w.accessibleName() or "").strip():
            w.setAccessibleName(tr(name))
            named += 1
        if not str(w.accessibleDescription() or "").strip():
            w.setAccessibleDescription(tr(desc))
    return named


def _label_text_before(widget: Any) -> str:
    """同一 layout 中紧邻在 `widget` 之前的标签文本（有界回溯：只看同层与上一层）。"""
    parent = widget.parentWidget()
    layout = parent.layout() if parent is not None else None
    if layout is None:
        return ""
    target = None
    for i in range(layout.count()):
        it = layout.itemAt(i)
        if it is not None and it.widget() is widget:
            target = i
            break
    if target is None:
        return ""
    from PyQt6.QtWidgets import QCheckBox, QLabel, QRadioButton
    for j in range(target - 1, -1, -1):
        it = layout.itemAt(j)
        cand = it.widget() if it is not None else None
        if isinstance(cand, (QLabel, QCheckBox, QRadioButton)):
            text = _clean(cand.text())
            if text:
                return text
    return ""


def _buddy_text(widget: Any) -> str:
    """`QLabel.setBuddy(widget)` 绑定的那个标签的文本（最可靠的来源）。"""
    from PyQt6.QtWidgets import QLabel
    parent = widget.parentWidget()
    if parent is None:
        return ""
    for lbl in parent.findChildren(QLabel):
        if lbl.buddy() is widget:
            text = _clean(lbl.text())
            if text:
                return text
    return ""


def _tooltip_text(widget: Any) -> str:
    """tooltip 首行（等价于简短说明，比没有名字强）。"""
    tip = _clean(widget.toolTip())
    if not tip:
        return ""
    for sep in ("\n", "。"):
        if sep in tip:
            tip = tip.split(sep, 1)[0]
    return tip.strip()


def autoname_inputs(root: Any) -> int:
    """给没有名字的输入控件按「buddy 标签 → 前一个标签 → tooltip」推导名字。

    返回本轮补上的条数。推不出来的保持空 —— 空着能被 `a11y_audit` 数出来，
    编一个假名字会让读数永远消失。
    """
    if root is None:
        return 0
    from PyQt6.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit, QSpinBox, QTextEdit
    named = 0
    for cls in (QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit):
        for w in root.findChildren(cls):
            if str(w.accessibleName() or "").strip():
                continue
            text = _buddy_text(w) or _label_text_before(w) or _tooltip_text(w)
            if text:
                w.setAccessibleName(text)
                named += 1
    return named


def effective_name(widget: Any) -> str:
    """读屏实际会读到的名字：`accessibleName` → `text()` → tooltip 首行。

    `scripts/a11y_audit.py` 用它计数，避免把「Qt 自动用 text() 当名字」的按钮
    误报成缺失（那会让棘轮基线虚高、真缺口被噪音淹没）。
    """
    if widget is None:
        return ""
    name = _clean(getattr(widget, "accessibleName", lambda: "")())
    if name:
        return name
    # **只有按钮类**才拿 text() 当名字。`QSpinBox.text()` 返回的是**当前数值**、
    # `QLineEdit.text()` 返回**用户输入的内容** —— 把它们当 accessible name 会让
    # 审计永远认为"有名字"，棘轮从此对新加的无标签输入框失明（审查实测：
    # `effective_name(QSpinBox 值=7)` → '7'，基线里 QSpinBox 缺名恒为 0）。
    from PyQt6.QtWidgets import QAbstractButton
    if isinstance(widget, QAbstractButton):
        text = _clean(widget.text())
        if text:
            return text
    return _tooltip_text(widget)
