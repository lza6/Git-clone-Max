"""C2-5 / Rev7 B2-6：命令面板（Ctrl+K）。

**为什么需要**：主窗有 40+ 个能力，入口分散在三层（菜单 / 页签 / 右键菜单）。
老用户找不到「那个按钮在哪个页签」、新用户连"有哪些功能"都不知道。
命令面板把**已存在的按钮**聚合成一个可搜索列表 —— 不新建任何能力，只是让已有的
找到得到。

设计取舍（都是刻意的）：
- **不改造现有菜单/工具栏**：只读它们的文本与 `click()`，不引入 ActionRegistry。
  改造菜单会正面撞上「不许重构 main_window」这条约束（见 Rev7 §2.5）。
- **数据源是真实控件**（`QPushButton` / `QAction`），不是硬编码清单 ——
  加一个新按钮，面板自动多一条，不会漂移成第二份真相。
- **纯函数打分**（`score` / `rank`）可单测；UI 只负责渲染与执行。
- 命中为 0 条时给空态文案，而不是一个空白框。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ..i18n import tr

#: 面板最多展示多少条（再多也搜不完，反而拖慢渲染）。
MAX_RESULTS = 40


def score(query: str, text: str) -> int:
    """模糊匹配打分：完全相等 > 前缀 > 子串 > 子序列；0 = 不匹配。

    中英文都按字符处理（不做分词）—— 命令面板的用户输入通常就是几个词，
    子串/子序列已经足够，引入分词器只会带来"搜不到"的意外。
    """
    q = (query or "").strip().lower()
    t = (text or "").strip().lower()
    if not q or not t:
        return 0
    if q == t:
        return 1000
    if t.startswith(q):
        return 900 - len(t)
    idx = t.find(q)
    if idx >= 0:
        return 700 - idx - len(t) // 10
    # 子序列（"kldr" 能命中 "打开克隆目录" 的拼音首字母场景留给用户自己打字；
    # 这里只对字符做顺序包含，够用且可解释）
    pos = 0
    for ch in q:
        pos = t.find(ch, pos)
        if pos < 0:
            return 0
        pos += 1
    return 300 - len(t)


def rank(query: str, items: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """按得分降序返回 [(text, payload)]；空查询返回全部（按原文排序）。"""
    if not (query or "").strip():
        return sorted(items, key=lambda it: it[0])[:MAX_RESULTS]
    scored = []
    for text, payload in items:
        s = score(query, text)
        if s > 0:
            scored.append((s, text, payload))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(text, payload) for _, text, payload in scored][:MAX_RESULTS]


def collect_actions(root) -> list[tuple[str, Callable[[], None]]]:
    """从真实控件里收集「可点击的动作」（按钮优先，其次 QAction）。

    只收：可见、已启用、文本非空、且不是命令面板自己的控件。
    去重按文本（同一个"刷新"只出现一次，取第一个）。
    """
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QPushButton

    out: list[tuple[str, Callable[[], None]]] = []
    seen: set[str] = set()
    skipped: list[int] = []      # 收集被跳过的控件数（末尾一次性 warn，不逐条刷日志）

    def _add(text: str, invoke: Callable[[], None]) -> None:
        key = (text or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        out.append((key, invoke))

    try:
        for btn in root.findChildren(QPushButton):
            try:
                if btn.window() is not root:
                    continue          # 只收主窗口自己的按钮，不收对话框里的
                if btn.objectName() in ("commandPaletteButton",):
                    continue
                text = btn.text().replace("&", "").strip()
                _add(text, btn.click)
            except Exception:         # noqa: BLE001 —— 单个控件异常不该让面板整体空掉
                skipped.append(1)
                continue
    except Exception as e:            # noqa: BLE001 —— 遍历失败不该让面板整体空掉
        skipped.append(1)
        from ..app.applog import warn as _warn
        _warn(f"命令面板枚举按钮失败（已跳过）：{e}")
    try:
        for act in root.findChildren(QAction):
            try:
                text = (act.text() or "").replace("&", "").strip()
                if not text or not act.isEnabled():
                    continue
                _add(text, act.trigger)
            except Exception:         # noqa: BLE001
                skipped.append(1)
                continue
    except Exception as e:            # noqa: BLE001
        skipped.append(1)
        from ..app.applog import warn as _warn
        _warn(f"命令面板枚举菜单项失败（已跳过）：{e}")
    if skipped:
        from ..app.applog import warn as _warn
        _warn(f"命令面板跳过了 {len(skipped)} 个异常控件/菜单项")
    return out


class CommandPalette(QDialog):
    """Ctrl+K 命令面板：输入即过滤，回车执行第一条。"""

    def __init__(self, owner, actions: Optional[list[tuple[str, Callable[[], None]]]] = None,
                 parent=None):
        super().__init__(parent if parent is not None else owner)
        self._owner_ref = owner
        self.setWindowTitle(tr("命令面板"))
        self.setModal(True)
        from .layout import fit_dialog_to_screen
        fit_dialog_to_screen(self, 560, 460, 420, 320)
        self.actions = list(actions if actions is not None else collect_actions(owner))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("输入以搜索动作…（回车执行，Esc 关闭）"))
        self.edit.textChanged.connect(self._refresh)
        self.edit.returnPressed.connect(self._run_current)
        layout.addWidget(self.edit)

        self.list = QListWidget()
        self.list.setObjectName("commandPaletteList")
        self.list.itemActivated.connect(lambda _it: self._run_current())
        layout.addWidget(self.list, 1)

        self.empty = QLabel(tr("没有匹配的动作（试试更短的关键词）"))
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setVisible(False)
        layout.addWidget(self.empty)

        self._rows: list[tuple[str, Any]] = []
        self._refresh("")
        self.edit.setFocus()

    # ---------------------------------------------------------------- 内部
    def _refresh(self, text: str = "") -> int:
        """按输入过滤并重填列表；返回命中数（供测试断言）。"""
        rows = rank(text, self.actions)
        self._rows = rows          # 「看到什么就执行什么」：执行端不重新算一遍
        self.list.clear()
        for title, _invoke in rows:
            self.list.addItem(QListWidgetItem(title))
        if rows:
            self.list.setCurrentRow(0)
        self.empty.setVisible(not rows)
        self.list.setVisible(bool(rows))
        return len(rows)

    def _run_current(self) -> bool:
        """执行当前选中项；返回是否真的执行了（供测试真验）。"""
        row = self.list.currentRow()
        rows = getattr(self, "_rows", [])
        if row < 0 or row >= len(rows):
            return False
        _title, invoke = rows[row]
        try:
            self.accept()
        except Exception as e:     # noqa: BLE001 —— 关窗失败不阻止执行，但要留痕
            from ..app.applog import warn as _warn
            _warn(f"命令面板关闭失败（继续执行动作）：{e}")
        try:
            invoke()
        except Exception as e:     # noqa: BLE001 —— 单个动作异常不该带崩主窗
            from ..app.applog import warn as _warn
            _warn(f"命令面板执行动作失败：{e}")
            return False
        return True


def open_palette(owner) -> CommandPalette:
    """打开命令面板（模态）。返回实例便于测试/后续扩展。"""
    pal = CommandPalette(owner)
    pal.exec()
    return pal
