"""G54-4 首次使用引导清单（first-run checklist）。

设计取舍：
- **不新建一套状态**：能推导的项（下载目录、令牌、已入库仓库）直接读现有设置/数据库；
  只有推导不出来的两项（跑过一次体检、打开过帮助）才记在 `settings.checklist_done`。
- **可跳过、不阻塞**：清单是「下一步做什么」的提示，不是门禁 —— 用户点「稍后」即可。
- 独立模块：`main_window.py` 已 2000+ 行，新 UI 逻辑一律落新文件。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from ..i18n import tr

# (键, 标题, 说明, 是否可自动推导)
ITEMS: tuple[tuple[str, str, str, bool], ...] = (
    ("download_dir", "设置下载目录",
     "决定仓库克隆到哪里；可在设置页随时修改。", True),
    ("token", "配置访问令牌（私有仓库）",
     "只有克隆私有仓库才需要；令牌加密落盘。", True),
    ("network", "跑一次网络体检",
     "确认 git / 网络 / 代理 / 磁盘都能用，避免第一次批量下载才发现问题。", False),
    ("import", "导入仓库清单或扫描本地",
     "支持粘贴地址、导入 GitHub Star 列表、扫描本机已有仓库。", True),
    ("help", "看一眼帮助与排错",
     "常见错误的「原因 → 该改哪个设置」都在这里。", False),
)


def compute_status(settings, repos_count: int = 0) -> dict[str, bool]:
    """推导每一项是否已完成。纯函数，便于单测。"""
    done_flag = getattr(settings, "checklist_done", None) or {}
    return {
        "download_dir": bool(str(getattr(settings, "download_dir", "") or "").strip()),
        "token": bool(str(getattr(settings, "token", "") or "").strip()),
        "network": bool(done_flag.get("network")),
        "import": int(repos_count or 0) > 0,
        "help": bool(done_flag.get("help")),
    }


def _dim_color() -> str:
    """取当前主题的次要文字色（避免写死 #888 在浅色主题下册对比度不足）。"""
    try:
        from . import theme as _th
        pal = _th.current_palette()
        # G69-18：四个调色板的实际键名是 `text_dim`。原先猜的 dim/muted/fg2/sub
        # 全都不存在 → 永远返回 #888888，「改用主题色」等于没改。
        for key in ("text_dim", "dim", "muted", "fg2", "sub"):
            if pal.get(key):
                return str(pal[key])
    except Exception:
        pass
    return "#888888"


class ChecklistDialog(QDialog):
    """首次引导清单：列出 5 项，已完成打勾，可关闭稍后再看。"""

    def __init__(self, status: dict[str, bool], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("开始使用清单"))
        from .layout import fit_dialog_to_screen
        fit_dialog_to_screen(self, 560, 420, 460, 320)
        lay = QVBoxLayout(self)

        done = sum(1 for k, _t, _d, _a in ITEMS if status.get(k, False))
        sum_lbl = QLabel(
            tr("按顺序做完这几件事，就能稳定地批量下载了（已完成 {done}/{total}）")
            .replace("{done}", str(done)).replace("{total}", str(len(ITEMS))))
        sum_lbl.setWordWrap(True)
        lay.addWidget(sum_lbl)

        for key, title, desc, _auto in ITEMS:
            row = QCheckBox(tr(title))
            row.setChecked(bool(status.get(key, False)))
            row.setEnabled(False)          # 状态是推导/回填的，不在这里改
            lay.addWidget(row)
            hint = QLabel("   " + tr(desc))
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color:{_dim_color()};")
            lay.addWidget(hint)

        btns = QDialogButtonBox()
        btns.addButton(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
