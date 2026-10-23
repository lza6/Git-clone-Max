"""更新检查流程（G77）：手动检查 / 结果槽 / 启动静默检查。

## 为什么从 `main_window.py` 抽出来
主窗已 **2750 行**（项目硬上限 800 的 3.4 倍，属已登记技术债）。更新检查这三段
（`check_update_now` / `_on_update_checked` / `_check_update_silent`）是**内聚的一套**：
同一个信号、同一套"能不能弹模态"的判据、同一份 SHA256 传递约定，合计约 100 行。
按项目既有先例（`engine_factory` / `download_layout` / `settings_actions` / `batch_overview`）
搬到独立模块，主窗只留同名薄方法转调 —— **对外名字与行为完全不变**，既有测试
（`test_update_auto`、`test_g66_patch.TestUpdateCheckThreading`、
`test_g68_settings_ui` 的模态阻塞守卫）照常覆盖。

## 三条不能破的约定（都是踩过坑换来的）
1. **网络请求必须在工作线程**，worker 只 `emit`，不碰任何控件（G67-9）：
   原先在按钮回调里同步跑 `check_latest()`，它内部失败会 `sleep(2)` 重试，
   点击后界面最长冻结 2s+。
2. **弹模态前必须问 `win._can_show_modal()`**（G68-4）：窗口不可见时
   `QMessageBox.exec()` 会永久阻塞进程（实测挂死 7 分钟）。
3. **`update_checked` 信号保持 4 参数**：既有测试与跨线程用例按 4 参断言，
   SHA256 走 `win._update_sha256` 属性回传，不扩签名。
"""
from __future__ import annotations

import threading

from .theme import LogLevel


def _msgbox():
    """取"弹窗类"：**故意**用 `gcm.ui.main_window` 里的那个名字。

    原因（不是随手写的间接层）：全项目与全部既有守卫都按
    `mock.patch("gcm.ui.main_window.QMessageBox")` 这一个补丁点来屏蔽模态
    （见 `tests/test_g68_settings_ui`、`tests/test_update_auto`、`tests/test_manage_ui`）。
    本模块从 `main_window` 抽出来后，如果自己再 `from PyQt6.QtWidgets import QMessageBox`，
    那些补丁就会**静默失效** —— 表现是测试卡在真实模态里挂死（本轮实测过）。
    在运行期取名字可以同时满足：单一补丁点不变 + 本模块无循环导入。
    """
    from . import main_window as _mw
    return _mw.QMessageBox


def check_now(win) -> None:
    """手动检查更新：网络请求在工作线程，结果经 `update_checked` 回 GUI 线程弹窗。"""
    if getattr(win, "_update_checking", False):
        return  # 防连点：worker 未回前不重复发起
    win._update_checking = True
    try:
        win.statusBar().showMessage("正在检查更新…", 3000)
    except Exception:  # noqa: BLE001 —— 状态栏不可用不该拦住检查
        pass

    def _worker():
        try:
            from ..app.updater import check_latest_with_sha
            # G76：带 sha256 的检查（`check_latest_with_sha` 此前零生产调用）
            has_new, ver, url, err, sha = check_latest_with_sha()
        except Exception as e:  # noqa: BLE001 —— 兜底成 err，绝不从线程抛出
            has_new, ver, url, err, sha = False, "", "", f"检查更新失败：{e}", ""
        win._update_sha256 = str(sha or "")
        # 跨线程 emit：Qt 自动走 QueuedConnection，槽在 GUI 线程执行
        win.update_checked.emit(bool(has_new), str(ver or ""), str(url or ""), str(err or ""))

    threading.Thread(target=_worker, daemon=True, name="gcm-update-check").start()


def on_checked(win, has_new: bool, ver: str, url: str, err: str,
               sha: str | None = None) -> None:
    """`update_checked` 槽（GUI 线程）：弹窗 + 需要时打开下载页；附 SHA256 校对行。"""
    from .main_window import _fmt_dt  # 延迟导入：避免 main_window ↔ update_flow 循环

    win._update_checking = False
    if sha is None:
        sha = str(getattr(win, "_update_sha256", "") or "")
    win._update_sha256 = ""  # 一次性：不要污染下一次检查的提示
    from .. import __version__
    msg = (err or (f"发现新版本 {ver}，可到设置页下载" if has_new
                   else f"已是最新版本（{__version__}）"))
    if not win._can_show_modal():
        # 没有可见窗口 → 不弹模态（会永久阻塞），改为写日志留痕
        try:
            win._emit_log(_fmt_dt(), LogLevel.INFO, f"检查更新：{msg}")
        except Exception:  # noqa: BLE001 —— 日志失败不该让检查流程崩
            pass
        return
    mb = _msgbox()
    try:
        if err:
            mb.information(win, "检查更新", err)
        elif has_new:
            extra = ""
            if sha:
                extra = (f"\n\n发布方公布的 SHA256：\n{sha}\n"
                         f"（下载后可用 `certutil -hashfile <文件> SHA256` 核对）")
            ret = mb.question(
                win, "发现新版本",
                f"当前版本 {__version__}，最新版本 {ver}。\n是否打开下载页面？{extra}",
                mb.StandardButton.Yes | mb.StandardButton.No)
            if ret == mb.StandardButton.Yes and url:
                import webbrowser
                webbrowser.open(url)
        else:
            mb.information(win, "检查更新", f"已是最新版本（{__version__}）")
    except Exception as e:  # noqa: BLE001 —— 弹窗失败只能降级成再弹一次提示
        mb.information(win, "检查更新", f"检查更新失败：{e}")


def check_silent(win) -> None:
    """启动后静默检查：不阻塞、不弹窗；失败留痕、有新版本写日志 + 托盘提示。"""
    from .main_window import _fmt_dt  # 延迟导入：避免循环

    def _worker():
        try:
            from .. import __version__
            from ..app.updater import check_latest
            has_new, ver, url, err = check_latest()
            if err:
                # G21-3：静默检查失败必须留痕（结构化 app.log），不再无痕
                try:
                    from ..app.applog import warn as _log_warn
                    _log_warn(f"check_update|silent_fail|{err}",
                              win.data_dir / "app.log")
                except Exception:  # noqa: BLE001 —— 日志不可用不该影响启动
                    pass
                return
            if has_new:
                # G67-9：**工作线程内只能走 emit 桥**，不得直接调 _emit_log
                win.emit_log_from_worker(
                    _fmt_dt(), LogLevel.INFO,
                    f"发现新版本 {ver}（当前 {__version__}），可到设置页检查更新。")
                try:
                    if getattr(win, "tray", None) is not None:
                        win.tray.notify("Git-clone-Max 有新版本",
                                        f"最新 {ver}，可下载更新")
                except Exception:  # noqa: BLE001 —— 托盘不可用不该影响检查结果
                    pass
        except Exception:  # noqa: BLE001 —— 静默检查绝不把异常抛到线程外
            pass

    threading.Thread(target=_worker, daemon=True, name="gcm-update-silent").start()


__all__ = ["check_now", "on_checked", "check_silent"]
