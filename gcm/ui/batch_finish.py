"""批次收尾的唯一播报出口（Rev14 F1/F2 拆出）。

**为什么独立成模块**：这段逻辑（状态栏小结 / 系统日志 / 托盘通知 / 完成 Webhook）
原本长在 `progress_table._on_engine_finished` 的尾部，随着「跳过 N」的加入把那个
文件顶到了 814 行（超过 800 硬上限，`test_g79_repo_hygiene` 的集合相等断言会红）。
它本身也不是"进度表"的职责 —— 它是**整批结果的播报**，谁跑完都能用。

**唯一口径**：`成功 / 冲突 / 失败` 三个数一直来自表格 UserRole 的终态统计，
「跳过」在 Rev14 F2 之后才出现，所以没有跳过时文案保持三段式（不制造噪音）。
"""
from __future__ import annotations

from typing import Any

from ..i18n import tr


def announce_finished(owner: Any, *, ok: int, conflict: int, fail: int,
                      skip: int = 0, table: Any = None) -> str:
    """批次结束后的全部对外播报，返回可复用的 `completed` 文案。

    顺序固定：状态栏 → 系统日志 → 托盘通知 → 完成 Webhook（后台线程）。
    每一步都**不得**让主流程失败：播报失败不该影响"批次已经结束"这个事实。
    """
    # Rev14 F2：全部被跳过时表格可能一行都没有（既有管线会按 engine.specs 重建），
    # 此时用引擎回报的计数兜底 —— 「跳过 N」不能因为这个就消失。
    skip = max(int(skip), int(getattr(owner, "_skipped_count", 0)))
    skip_part = tr(" · 跳过 {skip}").format(skip=skip) if skip else ""
    completed = tr("成功 {ok} · 冲突 {conflict} · 失败 {fail}{skip_part}").format(
        ok=ok, conflict=conflict, fail=fail, skip_part=skip_part)

    try:
        owner.statusBar().showMessage(tr("完成：{completed}").format(completed=completed))
    except Exception:      # noqa: BLE001 —— 播报失败不影响批次结论
        pass

    try:
        from . import main_window as _mw
        from .theme import LogLevel as _LL
        owner._emit_log(_mw._fmt_dt(), _LL.SYSTEM, tr("全部任务结束：{completed}").format(completed=completed))
    except Exception:      # noqa: BLE001
        pass

    # 托盘气泡（没装托盘时静默跳过）
    try:
        tray = getattr(owner, "tray", None)
        if tray is not None:
            tray.notify(tr("全部任务完成"), completed)
    except Exception:      # noqa: BLE001
        pass

    _maybe_send_webhook(owner, ok=ok, conflict=conflict, fail=fail, table=table)
    return completed


def _maybe_send_webhook(owner: Any, *, ok: int, conflict: int, fail: int,
                        table: Any) -> None:
    """完成通知 Webhook（G49-7）：显式开启 + URL 非空才发，后台线程，失败不影响主流程。"""
    try:
        failed_names: list[str] = []
        if table is not None:
            for i in range(table.rowCount()):
                it0 = table.item(i, 0)
                it2 = table.item(i, 2)
                if it2 is None or it0 is None:
                    continue
                state = it2.data(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt
                                 .ItemDataRole.UserRole) or it2.text()
                if state == "failed":
                    failed_names.append(it0.text())
        settings = getattr(owner, "settings", None)
        if settings is None:
            return
        params = (ok, fail, conflict, 0, ok + fail + conflict, failed_names)

        def _job() -> None:
            try:
                from ..app.notify import maybe_notify as _mn
                if _mn(settings, *params, data_dir=getattr(owner, "data_dir", None)):
                    # 工作线程不得直接碰 QWidget：走信号回 GUI 线程更新状态栏
                    try:
                        owner.notify_sent.emit(tr("完成通知 Webhook 已发送"))
                    except Exception:      # noqa: BLE001
                        pass
            except Exception:      # noqa: BLE001 —— 完成播报绝不影响主流程
                pass

        import threading
        threading.Thread(target=_job, daemon=True).start()
    except Exception:      # noqa: BLE001
        pass


__all__ = ["announce_finished"]
