"""本地仓库扫描的后台线程 + 信号桥（避免阻塞 UI 线程）。

P-perf：多根并行 + 单根内候选并发（scanner 已无子进程依赖，纯文件读）；
progress 信号周期性回传 (done, total)；cancel() 可中途停止。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, pyqtSignal

from ..app.scanner import RepoInfo, scan_git_dirs


class ScanWorker(QObject):
    """后台扫描线程；通过信号把结果回传主线程。"""

    finished = pyqtSignal(list)     # List[RepoInfo]
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)  # done, total（候选仓库采集进度）

    def __init__(self, roots, max_depth: int = 2, workers: int = 8, parent=None):
        super().__init__(parent)
        self._roots = list(roots)
        self._max_depth = max_depth
        self._workers = workers
        self._cancelled = threading.Event()
        # G69-18：停止标志。线程与 QObject 生命周期不一致 —— 对话框被
        # 关闭/销毁后线程可能仍在跑，此时再 `emit` 就是对一个 C++ 侧已析构的
        # QObject 发信号，表现是**进程原生崩溃**（SIGSEGV / access violation），
        # 不是 Python 异常。所以收了停止信号后一律不再 emit。
        self._stopped = False
        self._thread: threading.Thread | None = None

    def cancel(self) -> None:
        """请求取消：扫描线程尽快返回已采集部分（finish 仍会触发）。"""
        self._cancelled.set()

    def stop(self, timeout: float = 5.0) -> None:
        """G69-18：取消**并等待**扫描线程收敛；调用后本对象不再发任何信号。

        对话框关闭/取消时必须调用 —— 否则线程会对着已析构的 QObject
        `emit`，导致进程原生崩溃（这正是本地扫描「点取消/关窗口偶发崩溃」的成因）。
        幂等：重复调用安全。
        """
        self._stopped = True
        self._cancelled.set()
        th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout=timeout)

    def _emit(self, signal, *args) -> None:
        """只在未停止时发信号，并对「C++ 对象已删除」兜底。"""
        if self._stopped:
            return
        try:
            signal.emit(*args)
        except RuntimeError:
            # wrapped C/C++ object has been deleted —— 宿主已析构，丢弃
            self._stopped = True

    def start(self):
        def _run():
            try:
                found: list[RepoInfo] = []

                def _scan_one(root):
                    return list(scan_git_dirs(
                        root, self._max_depth, workers=self._workers,
                        progress=lambda d, t: self._emit(self.progress, d, t),
                        cancelled=lambda: self._cancelled.is_set()))

                roots = self._roots
                with ThreadPoolExecutor(
                        max_workers=max(1, min(len(roots), 4))) as ex:
                    for lst in ex.map(_scan_one, roots):
                        found.extend(lst)
                uniq = _dedupe(found)
                self._emit(self.finished, uniq)
            except Exception as e:  # noqa
                self._emit(self.failed, str(e))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        # 持引用防止线程对象被 GC
        self._thread = t


def _dedupe(infos: list[RepoInfo]) -> list[RepoInfo]:
    seen = set()
    out = []
    for i in infos:
        if i.key in seen:
            continue
        seen.add(i.key)
        out.append(i)
    out.sort(key=lambda i: i.path)
    return out
