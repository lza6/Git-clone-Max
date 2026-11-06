"""半成品目录的判定与清理（G80-B4-1b）。

单独成模块的原因：`progress_table.py` 已贴 800 行上限（800 是硬守卫），
而"半成品"是一个**跨界面**的概念 —— 下载页进度表要用、本地扫描要用、
将来统计页也可能要用，放 UI 工具模块里最合适（不依赖任何具体面板）。

安全边界（**宁可少删，不能错删**）
----------------------------------
- 判"半成品"要求两条同时成立：①有 `.git`；②工作树里除 `.git` 外**零条目**。
  只要目录里还有一个文件/子目录，就**不是**半成品（那可能是用户的真实仓库）。
- 清理走 `GitService._safe_rmtree_partial`（语义同样保守：只删含 `.git` 或空目录），
  并且**必须**由调用方（UI）完成二次确认后才调用本模块的 delete。
"""
from __future__ import annotations

from pathlib import Path

# Rev8 P1-1：判定**唯一事实源**下沉到 `app/halfclone.py`（两级分类 + 修复）。
# 本模块只保留删除与行解析（真正含 UI 语义的部分）；`looks_halfcloned` 变成
# 委托别名，调用方无感。
from ..app.halfclone import (  # noqa: F401
    classify_repo as classify_repo,
)
from ..app.halfclone import (
    is_halfcloned as looks_halfcloned,
)
from ..app.halfclone import (
    repair_halfclone as repair_halfclone,
)
from ..i18n import tr


def delete_halfcloned(path: Path) -> bool:
    """删除半成品目录。返回是否执行了删除（判定不通过则不动手）。"""
    if not looks_halfcloned(path):
        return False
    from gcm.git.service import GitService

    GitService(path.parent)._safe_rmtree_partial(path)
    return not path.exists()


def row_repo_dir(panel, row: int):
    """进度表某行对应的本地仓库目录（排序/过滤后仍精确）。

    归并到这里是因为三处调用（打开目录 / 判半成品 / 删除半成品）都需要同一个解析，
    而解析必须**先经 `_row_engine_index`**，否则排序后会拿到错行的目录。
    """
    from pathlib import Path as _P

    idx = panel._row_engine_index(row)
    spec = panel.owner.row_specs.get(idx)
    if spec is None:
        return None
    if spec.local_path:
        return _P(spec.local_path)
    return (_P(panel.owner.target_edit.text().strip()
               or str(panel.owner.data_dir / "clones")) / spec.folder_name)


def repair_halfcloned_row(panel, row: int) -> None:
    """Rev8 P1-1：检出修复（`git checkout -f HEAD`，零网络、不动引用）。

    只对 not_checked_out 生效；判定与修复在 `app/halfclone.py` 单一事实源。
    逻辑放本模块（progress_table 已贴 800 线硬上限），面板只做一行转发。
    """
    from ..app.halfclone import repair_halfclone

    p = row_repo_dir(panel, row)
    ok, msg = repair_halfclone(p)
    if ok:
        panel.owner.statusBar().showMessage(f"已检出修复：{p.name}")
        from . import main_window as _mw
        panel.owner._emit_log(_mw._fmt_dt(), _level_info(),
                              f"半成品检出修复成功：{p}（{msg}）")
    else:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(panel.owner, tr("无法修复"), f"{p}\n\n{msg}")


def delete_halfcloned_row(panel, row: int) -> None:
    """删除半成品目录（**二次确认**；判定不过则不动手）。

    从 progress_table 迁入（800 线瘦身）：判定与删除本就归本模块，
    UI 只负责「让用户点两次确认」。
    """
    from . import main_window as _mw
    from .theme import LogLevel
    QMessageBox = _mw.QMessageBox  # 兼容既有测试经 main_window.QMessageBox mock

    p = row_repo_dir(panel, row)
    if not looks_halfcloned(p):
        QMessageBox.information(panel.owner, tr("无需清理"),
                                tr("该目录不是半成品（有工作树内容）。"))
        return
    sure = QMessageBox.question(
        panel.owner, tr("删除半成品目录"),
        f"将删除该仓库的整个目录（其中只有 .git，没有工作树内容）：\n{p}\n\n继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    if sure != QMessageBox.StandardButton.Yes:
        return
    delete_halfcloned(p)
    panel.owner.statusBar().showMessage(f"已删除半成品目录：{p.name}")
    panel.owner._emit_log(_mw._fmt_dt(), LogLevel.WARN,
                          f"用户确认删除半成品目录：{p}")


def _level_info():
    from .theme import LogLevel
    return LogLevel.INFO
