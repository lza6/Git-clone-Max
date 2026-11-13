"""Rev16 C4：自动重投批次的 UI 收口（从 progress_table 抽出，守 800 行红线）。

重投走 `engine.launch()`，engine index 是**新批次**的 0..n-1；表格里残留的
上一批行带着旧 index（UserRole+1），`_row_for` 反查会命中旧行把结果写到
别的仓库那一行（复审实测：row0 beta 收到 alpha 的结果），且收尾播报因
`owner.busy=False` 被跳过。这里把重投批次当"新批次"处理：重建行 + 置回
运行态 + 日志说明。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget


def on_retry_batch_started(owner: QWidget, n: int) -> None:
    """重投批次开始：按 engine.specs 重建表格行并置回运行态。

    `owner` 是 MainWindow；`n` 是本批调度数（engine.retry_batch_started 传来）。
    宿主能力用 LBYL（hasattr）判定，不加 except —— 异常预算棘轮只降不升。
    """
    engine = getattr(owner, "engine", None)
    panel = getattr(owner, "progress_panel", None)
    if engine is None or panel is None or n <= 0:
        return
    owner.busy = True
    owner._skipped_count = 0
    if hasattr(panel, "set_busy_preview"):
        panel.set_busy_preview(True)
        owner._skeleton_on = True
    if hasattr(panel, "_strip"):
        strip = panel._strip()
        if strip is not None:
            strip.begin(n)
    _bc = getattr(owner, "batch_conclusion", None)
    if _bc is not None:
        _bc.begin(n)
    owner.btn_start.setEnabled(False)
    owner.btn_cancel.setEnabled(True)
    owner.repo_input.setEnabled(False)
    owner.mode_combo.setEnabled(False)
    owner.depth_spin.setEnabled(False)
    n_specs = len(engine.specs)
    owner._pending_count = n_specs
    owner._prepare_table(n_specs)
    panel.begin_bulk()
    for i in range(n_specs):
        spec = engine.specs[i]
        owner._add_table_row(i, spec)
        owner.row_specs[i] = spec
    if hasattr(owner, "_emit_log") and hasattr(owner, "_fmt_dt"):
        from .main_window import LogLevel
        owner._emit_log(owner._fmt_dt(), LogLevel.SYSTEM,
                        owner.tr("自动重试：重投 {n} 个仓库（新批次）").format(n=n_specs))
    if hasattr(owner, "statusBar"):
        owner.statusBar().showMessage(
            owner.tr("并行同步中：{n} 个仓库…").format(n=n_specs))
