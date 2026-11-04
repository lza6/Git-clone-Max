"""下载中心辅助动作（G33-8 拆分）：从 MainWindow 提取的独立小动作。

- choose_target / open_target：下载目录选择与打开
- repo_input_menu / clear_url_history：输入框右键历史菜单
- save_log：黑匣子日志导出

全部以 owner（MainWindow）组合调用，保持控件名与回调不变，行为零变化。
"""
from __future__ import annotations

import os
from pathlib import Path

from ..i18n import tr  # G49-1
from ..util.openfolder import open_folder
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
        owner, tr("导出日志"), str(owner.data_dir / "clone_log.txt"), "文本文件 (*.txt)")
    if not path:
        return
    try:
        Path(path).write_text(owner.log.to_plain_text(), encoding="utf-8")
        owner._emit_log(fmt_dt(), LogLevel.INFO, f"日志已导出：{path}")
    except Exception as e:
        _msgbox().critical(owner, tr("导出失败"), str(e))


def export_report(owner, fmt_dt) -> None:
    """G07-3 导出报表（弹文件选择；CSV 为 Excel 友好 utf-8-sig）。

    G67-5：新增 **xlsx** 选项 —— `reports.export_xlsx` 早已实现（含自动列宽/冻结首行），
    但 UI 只给 CSV/Markdown → 用户实际不可达（changelog 却宣称已落地）。
    xlsx 依赖可选包 openpyxl，缺失时 `export_xlsx` 会抛带安装提示的 RuntimeError，
    这里按普通错误路径展示（不静默降级）。
    """
    path, _ = _fd().getSaveFileName(
        owner, tr("导出报表"), str(owner.data_dir / "sync_report.csv"),
        "CSV (*.csv);;Excel (*.xlsx);;Markdown (*.md)")
    if not path:
        return
    try:
        from ..reports import export_csv, export_markdown, export_xlsx
        from .busy import busy as _busy
        lower = str(path).lower()
        # C2-3：几千仓库导 xlsx 要数百毫秒到数秒，置忙并禁用「导出」入口
        with _busy(owner, "export_report", buttons=("btn_export",)):
            if lower.endswith(".xlsx"):
                n = export_xlsx(owner.db, path)
            elif lower.endswith(".md"):
                n = export_markdown(owner.db, path)
            else:
                n = export_csv(owner.db, path)
        owner._emit_log(fmt_dt(), LogLevel.INFO,
                        f"报表已导出：{path}（{n} 行）")
        owner.statusBar().showMessage(f"报表已导出：{path}（{n} 行）")
    except Exception as e:
        _msgbox().critical(owner, tr("导出失败"), str(e))


def repo_input_menu(owner, pos, fmt_dt, extra_actions=None) -> None:
    """输入框右键菜单：从最近 URL 历史回填 / 清空历史（G02-4）；extra_actions 可选追加。"""
    try:
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(owner)
        items = getattr(owner, "url_history", None).items() if hasattr(owner, "url_history") else []
        if items:
            sub = menu.addMenu(tr("从历史粘贴…"))
            for u in items[:15]:
                act = sub.addAction(u)
                act.triggered.connect(
                    lambda _=False, url=u: owner.repo_input.appendPlainText(url + "\n"))
            menu.addSeparator()
            act_clear = menu.addAction(tr("清空历史"))
            act_clear.triggered.connect(
                lambda: clear_url_history(owner, fmt_dt))
        else:
            menu.addAction(tr("（暂无历史）"))
        if extra_actions is not None:
            try:
                menu.addSeparator()
                extra_actions(menu)
            except Exception:
                pass
        menu.exec(owner.repo_input.mapToGlobal(pos))
    except Exception:
        pass


def clear_url_history(owner, fmt_dt) -> None:
    try:
        owner.url_history.clear()
        owner._emit_log(fmt_dt(), LogLevel.INFO, "URL 历史已清空。")
    except Exception:
        pass


def _is_sensitive_dir(d: str) -> bool:
    """G44-2：是否位于敏感系统目录（Windows 系统根 / Program Files）。"""
    import os
    dd = (d or "").strip().rstrip("\\/").lower()
    if not dd:
        return False
    sensitive = []
    windir = os.environ.get("WINDIR") or r"C:\Windows"
    pf = os.environ.get("PROGRAMFILES") or r"C:\Program Files"
    pfx86 = os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"
    sensitive.extend([windir.lower(), pf.lower(), pfx86.lower()])
    return any(dd == s or dd.startswith(s + "\\") for s in sensitive)


def _classify_mkdir_error(e: BaseException) -> str:
    """G44-2：mkdir 失败给人类可读分类（权限/占用/盘符不存在/其它）。"""
    import errno
    eno = getattr(e, "errno", None)
    if eno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return tr("权限不足：无法在当前位置创建目录（请换用用户目录）")
    if eno == errno.ENOTDIR:
        return tr("路径无效：上级路径不是目录")
    if eno == errno.ENOENT:
        return tr("盘符或路径不存在：请检查目标路径")
    if eno == errno.EMFILE or eno == errno.ENFILE:
        return tr("系统文件句柄已用尽：请稍后重试")
    return f"目录创建失败：{e}"


def choose_target(owner) -> None:
    d = _fd().getExistingDirectory(owner, tr("选择下载目录"), owner.target_edit.text())
    if d:
        # G44-2 敏感目录警告：改到系统根/Program Files 会污染系统区，弹确认
        if _is_sensitive_dir(d):
            ret = _msgbox().warning(
                owner, tr("敏感目录"),
                tr("你选择的是 Windows 系统目录（%SystemRoot%/Program Files），")
                + tr("在此下载可能触发权限/杀软问题。\n\n仍要继续吗？"),
                _msgbox().StandardButton.Yes | _msgbox().StandardButton.No,
                _msgbox().StandardButton.No)
            if ret != _msgbox().StandardButton.Yes:
                return
        owner.target_edit.setText(d)
        # 记住用户选择，下次启动恢复
        owner.settings.download_dir = d
        owner.settings_store.save(owner.settings)
        # G44-2 mkdir 失败分类提示（尽力而为，不阻塞保存设置）
        try:
            from pathlib import Path
            Path(d).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            _msgbox().warning(owner, tr("目录不可用"), _classify_mkdir_error(e))


def open_target(owner) -> None:
    path = owner.target_edit.text().strip()
    if not os.path.isdir(path):
        _msgbox().warning(owner, tr("目录不存在"), path)
        return
    if not open_folder(path):
        _msgbox().warning(owner, tr("无法打开目录"), path)


def _read_text_any(path: str) -> str:
    """按 utf-8 → gb18030 → latin-1 依次尝试读取文本。

    审查发现：只按 `utf-8 errors=replace` 读，遇到常见的中文 GBK README 会变成
    满屏替换符（然后"识别出 0 个链接"，用户以为功能坏了）。gb18030 是 GBK 的超集，
    覆盖绝大多数中文文本；latin-1 保证任何字节都能读出（最后兜底，不抛）。
    """
    last: Exception | None = None
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            with open(path, encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError as e:
            last = e
            continue
    raise OSError(f"无法解码该文件：{last}")


def import_readme_list(owner) -> None:
    """G84-18：从 README / awesome 清单 / 收藏页文本里批量导入仓库地址。

    为什么值得做：本产品的核心场景是「挂着刷几百个仓库」，而用户的第一批
    仓库地址几乎都躺在某个 README 或清单里 —— 手抄是最劝退的一步。
    实现只读本地文件、不联网（`extract_repo_urls` + `url_lib.parse_urls`），
    风险面为零；徽章/图片地址会被 `parse_urls` 判为无效并**明确回显条数**，
    不静默丢弃。
    """
    from ..app.url_lib import extract_repo_urls, parse_urls
    QFileDialog = _fd()
    mb = _msgbox()
    path, _ = QFileDialog.getOpenFileName(
        owner, tr("从清单导入仓库地址"), "",
        tr("清单/文本 (*.md *.markdown *.txt *.rst);;所有文件 (*.*)"))
    if not path:
        return
    try:
        text = _read_text_any(path)
    except OSError as e:
        mb.warning(owner, tr("导入清单"), tr("读取失败：{err}").format(err=e))
        return
    urls = extract_repo_urls(text)
    specs, invalid = parse_urls("\n".join(urls))
    if not specs:
        mb.information(
            owner, tr("导入清单"),
            tr("这份文件里没有识别出仓库地址（共发现 {n} 个链接）。").format(n=len(urls)))
        return
    # 去重口径与执行器一致：按 **folder_name**（目标目录）比，而不是按原始 URL 文本。
    # 实测坑：输入区里的 `.../skills` 与解析出的 `.../skills.git` 是同一个仓库，
    # 按文本比会重复导入一条，用户看到"导入了但没变化"。
    existing = owner.repo_input.toPlainText()
    seen_names = {sp.folder_name for sp in parse_urls(existing)[0]}
    add = [sp.url_https for sp in specs if sp.folder_name not in seen_names]
    if not add:
        mb.information(owner, tr("导入清单"), tr("这些地址已经在输入区里了，无需重复导入。"))
        return
    owner.repo_input.appendPlainText("\n".join(add) + "\n")
    msg = tr("已导入 {n} 个仓库地址").format(n=len(add))
    if invalid:
        msg += tr("（另有 {k} 个链接不是仓库，已跳过）").format(k=len(invalid))
    try:
        owner.statusBar().showMessage(msg)
    except Exception as e:      # noqa: BLE001 —— 状态栏提示失败不影响导入结果
        from ..app.applog import warn as _warn
        _warn(f"导入清单：状态栏提示失败（导入已成功）：{e}")
    mb.information(owner, tr("导入清单"), msg)
