"""下载中心辅助动作（G33-8 拆分）：从 MainWindow 提取的独立小动作。

- choose_target / open_target：下载目录选择与打开
- repo_input_menu / clear_url_history：输入框右键历史菜单
- save_log：黑匣子日志导出

全部以 owner（MainWindow）组合调用，保持控件名与回调不变，行为零变化。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .theme import LogLevel


def _mw():
    """取 main_window 模块（其 QFileDialog/QMessageBox 是测试 mock 目标）。"""
    from gcm.ui import main_window as _m
    return _m


def _fd():
    """取 QFileDialog：经 main_window 模块引用（测试 mock 该模块属性）。"""
    return _mw().QFileDialog


def _msgbox():
    """取 QMessageBox：经 main_window 模块引用（测试 mock 该模块属性）。"""
    return _mw().QMessageBox


def save_log(owner, fmt_dt) -> None:
    """导出黑匣子日志到文本文件。"""
    path, _ = _fd().getSaveFileName(
        owner, "导出日志", str(owner.data_dir / "clone_log.txt"), "文本文件 (*.txt)")
    if not path:
        return
    try:
        Path(path).write_text(owner.log.to_plain_text(), encoding="utf-8")
        owner._emit_log(fmt_dt(), LogLevel.INFO, f"日志已导出：{path}")
    except Exception as e:
        _msgbox().critical(owner, "导出失败", str(e))


def export_report(owner, fmt_dt) -> None:
    """G07-3 导出 CSV/Markdown 报表（弹文件选择；CSV 为 Excel 友好 utf-8-sig）。"""
    path, _ = _fd().getSaveFileName(
        owner, "导出报表", str(owner.data_dir / "sync_report.csv"),
        "CSV (*.csv);;Markdown (*.md)")
    if not path:
        return
    try:
        from ..reports import export_csv, export_markdown
        if str(path).lower().endswith(".md"):
            n = export_markdown(owner.db, path)
        else:
            n = export_csv(owner.db, path)
        owner._emit_log(fmt_dt(), LogLevel.INFO,
                        f"报表已导出：{path}（{n} 行）")
        owner.statusBar().showMessage(f"报表已导出：{path}（{n} 行）")
    except Exception as e:
        _msgbox().critical(owner, "导出失败", str(e))


def repo_input_menu(owner, pos, fmt_dt) -> None:
    """输入框右键菜单：从最近 URL 历史回填 / 清空历史（G02-4）。"""
    try:
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(owner)
        items = getattr(owner, "url_history", None).items() if hasattr(owner, "url_history") else []
        if items:
            sub = menu.addMenu("从历史粘贴…")
            for u in items[:15]:
                act = sub.addAction(u)
                act.triggered.connect(
                    lambda _=False, url=u: owner.repo_input.appendPlainText(url + "\n"))
            menu.addSeparator()
            act_clear = menu.addAction("清空历史")
            act_clear.triggered.connect(
                lambda: clear_url_history(owner, fmt_dt))
        else:
            menu.addAction("（暂无历史）")
        menu.exec(owner.repo_input.mapToGlobal(pos))
    except Exception:
        pass


def clear_url_history(owner, fmt_dt) -> None:
    try:
        owner.url_history.clear()
        owner._emit_log(fmt_dt(), LogLevel.INFO, "URL 历史已清空。")
    except Exception:
        pass


def choose_target(owner) -> None:
    d = _fd().getExistingDirectory(owner, "选择下载目录", owner.target_edit.text())
    if d:
        owner.target_edit.setText(d)
        # 记住用户选择，下次启动恢复
        owner.settings.download_dir = d
        owner.settings_store.save(owner.settings)


def open_target(owner) -> None:
    path = owner.target_edit.text().strip()
    if not os.path.isdir(path):
        _msgbox().warning(owner, "目录不存在", path)
        return
    if sys.platform == "win32":
        os.startfile(path)  # noqa
    else:
        import shutil
        openers = ("xdg-open", "open")
        for op in openers:
            if shutil.which(op):
                import subprocess
                subprocess.Popen([op, path])
                break
