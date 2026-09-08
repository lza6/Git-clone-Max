# -*- coding: utf-8 -*-
"""本地仓库扫描的后台线程 + 信号桥（避免阻塞 UI 线程）。"""
from __future__ import annotations

import threading
from typing import Callable, List

from PyQt6.QtCore import QObject, pyqtSignal

from ..app.scanner import RepoInfo, scan_git_dirs


class ScanWorker(QObject):
    """后台扫描线程；通过信号把结果回传主线程。"""

    finished = pyqtSignal(list)     # List[RepoInfo]
    failed = pyqtSignal(str)

    def __init__(self, roots, max_depth: int = 2, parent=None):
        super().__init__(parent)
        self._roots = list(roots)
        self._max_depth = max_depth

    def start(self):
        def _run():
            try:
                found: List[RepoInfo] = []
                for r in self._roots:
                    found.extend(scan_git_dirs(r, self._max_depth))
                # 去重（按 key）
                seen = set()
                uniq: List[RepoInfo] = []
                for i in found:
                    if i.key in seen:
                        continue
                    seen.add(i.key)
                    uniq.append(i)
                uniq.sort(key=lambda i: i.path)
                self.finished.emit(uniq)
            except Exception as e:  # noqa
                self.failed.emit(str(e))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        # 持引用防止线程对象被 GC
        self._thread = t


def _dedupe(infos: List[RepoInfo]) -> List[RepoInfo]:
    seen = set()
    out = []
    for i in infos:
        if i.key in seen:
            continue
        seen.add(i.key)
        out.append(i)
    out.sort(key=lambda i: i.path)
    return out
