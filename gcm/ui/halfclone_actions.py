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


def looks_halfcloned(path: Path) -> bool:
    """是否疑似半成品仓库（有 .git 但工作树除 .git 外零条目）。"""
    try:
        if path is None or not path.is_dir() or not (path / ".git").exists():
            return False
        return not any(c.name != ".git" for c in path.iterdir())
    except OSError:
        return False


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
