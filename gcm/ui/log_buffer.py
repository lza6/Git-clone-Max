"""日志批量渲染缓冲（G33-8 拆分）：从 MainWindow._emit_log/_flush_log_batch 提取。

职责：把高频 git 输出（32 并发下每 worker 每行）合并为 120ms 批量刷新，
避免主线程被刷屏拖慢。持有渲染目标（QPlainTextEdit）与日志计数，行为零变化。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QObject, QTimer

if TYPE_CHECKING:  # 仅类型标注（避免循环导入）
    from PyQt6.QtWidgets import QPlainTextEdit

    from .theme import LogEvent


class LogBuffer(QObject):
    """节流日志渲染缓冲。

    - owner 提供 `log_view`（QPlainTextEdit）与 `log_count`（QLabel）引用；
    - `append(ev)` 入缓冲，QTimer 每 120ms 调 `flush()` 批量写屏；
    - 与 LogModel（模型）解耦：模型仍由 MainWindow 持有，此处只做 UI 渲染节流。
    """

    def __init__(self, parent: Optional[QObject] = None,
                 log_view=None, log_count=None, interval_ms: int = 120):
        super().__init__(parent)
        self._batch: list = []
        self.log_view: Optional[QPlainTextEdit] = log_view
        self.log_count = log_count
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        # G69-18 修复：改为**单次**定时器 + 有积压才武装。
        #
        # 原实现 `self.timer.start()` 常驻循环，整个进程生命周期内每 120ms
        # 触发一次。窗口被销毁（测试里大量构造后直接丢引用）时，若 Qt 事件
        # 队列里已排着一个指向该定时器的 `QTimerEvent`，随后的
        # `processEvents()` 会在 `QTimerInfoList::activateTimers` 里解引用
        # **已释放**的定时器 → 进程原生崩溃（SIGSEGV，本机/Linux/macOS 实测，
        # C 栈：activateTimers → QCoreApplication::notifyInternal2）。
        #
        # 改成「有日志才武装、flush 后自动停」：
        #   ① 空转时不再唤醒事件循环（省电、少噪声）；
        #   ② 没有积压就不存在「等待触发的定时器」，从根上消掉这类时序崩溃；
        #   ③ 120ms 批量渲染的语义**完全不变**（首个 append 起算）。
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.flush)

    # ------------------------------------------------------------ 缓冲
    def append(self, ev: LogEvent) -> None:
        self._batch.append(ev)
        if not self.timer.isActive():
            self.timer.start()          # 武装：interval_ms 后批量刷一次

    def stop(self) -> None:
        """停止渲染节流定时器（窗口关闭/销毁前调用，幂等）。"""
        try:
            self.timer.stop()
        except Exception:
            pass

    def flush(self) -> None:
        """批量渲染当前积压日志到 QPlainTextEdit（含滚动跟随）。"""
        if not self._batch:
            return
        batch, self._batch = self._batch, []
        cur = self.log_view
        if cur is None:
            return
        # 优先级前缀（与历史 UI 文案一致）
        from .theme import LogLevel
        for ev in batch:
            prefix = {
                LogLevel.WARN: "[警告] ",
                LogLevel.ERROR: "[错误] ",
                LogLevel.SYSTEM: "[系统] ",
            }.get(ev.level, "")
            cur.appendPlainText(f"[{ev.time}] {prefix}{ev.text}")
        # 滚动跟随（仅在用户已处于底部时）
        sb = cur.verticalScrollBar()
        if sb.value() >= sb.maximum() - 40:
            cur.verticalScrollBar().setValue(cur.verticalScrollBar().maximum())

    def clear_pending(self) -> None:
        """清空积压（供 clear_log 使用，避免清空后 ≤120ms 幽灵追加）。"""
        self._batch.clear()

    def has_pending(self) -> bool:
        return bool(self._batch)
