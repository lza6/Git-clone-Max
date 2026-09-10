# -*- coding: utf-8 -*-
"""G09-1 剪贴板监听：检测到 git URL → 回调提示「加入队列」。

- check_once()：取剪贴板文本，正则提取受支持平台 URL，去重后回调 on_url。
- enabled=False 时完全不回调（设置开关）。
- 独立模块便于测试注入（不直接弹 UI，由 MainWindow 决定交互方式）。
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, QTimer

# 提取受支持平台 URL（https / ssh / 短格式从文本中抽出）
_URL_RE = re.compile(
    r"(?:https?://(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}/[^\s\"'<>]+|"
    r"git@[A-Za-z0-9.-]+:[^\s\"'<>]+)",
    re.IGNORECASE,
)


class ClipboardWatcher(QObject):
    """剪贴板变化轮询器：发现 git URL → on_url 回调（去重）。"""

    def __init__(self, parent=None, enabled: bool = True,
                 on_url: Optional[Callable[[str], None]] = None,
                 interval_ms: int = 1500):
        super().__init__(parent)
        self._enabled_flag = bool(enabled)
        self.on_url = on_url or (lambda u: None)
        self._last_seen: List[str] = []
        self._timer = QTimer(self)
        self._timer.setInterval(max(500, interval_ms))
        self._timer.timeout.connect(self.check_once)

    @property
    def enabled(self) -> bool:
        """开关状态：布尔属性（True/False）。"""
        return self._enabled_flag

    @enabled.setter
    def enabled(self, v: bool):
        self._enabled_flag = bool(v)

    def start(self):
        if self.enabled:
            self._timer.start()

    def stop(self):
        self._timer.stop()

    def check_once(self):
        """取剪贴板 → 提取 URL → 去重 → 回调。"""
        if not self._enabled_flag:
            return
        try:
            from PyQt6.QtWidgets import QApplication
            cb = QApplication.clipboard()
            text = cb.text() or ""
        except Exception:
            return
        if not text.strip():
            return
        for m in _URL_RE.finditer(text):
            url = m.group(0)
            # 过滤图片/网页资源类误报（以常见静态后缀结尾的忽略）
            low = url.lower().split("?")[0]
            if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".ico")):
                continue
            # 验证是受支持的 git 仓库地址
            try:
                from ..app.url_lib import parse_any_repo_url
                if parse_any_repo_url(url) is None:
                    continue
            except Exception:
                continue
            if url in self._last_seen:
                continue
            self._last_seen.append(url)
            if len(self._last_seen) > 50:
                self._last_seen = self._last_seen[-50:]
            try:
                self.on_url(url)
            except Exception:
                pass
            break  # 每次只提示最新一条
