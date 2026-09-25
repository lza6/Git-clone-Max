"""G53-5 定时报表：后台按间隔生成 CSV/Markdown 报表到指定目录（失败仅日志）。"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("gcm.sched_reports")


class ReportScheduler:
    """定时报表调度器：按 interval 调用 report_fn，写入 out dir，失败仅日志。"""

    def __init__(self, report_fn: Callable[[Path, str], int],
                 interval_min: float = 1440.0,
                 out_dir: Optional[Path] = None,
                 fmt: str = "csv",
                 now_fn: Optional[Callable[[], datetime]] = None):
        self._report_fn = report_fn
        self._interval = max(1.0, float(interval_min)) * 60.0
        self._out_dir = Path(out_dir) if out_dir else Path.cwd()
        self._fmt = fmt if fmt in ("csv", "md", "markdown") else "csv"
        self._now_fn = now_fn or datetime.now
        self._stop_evt: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None

    def run_once(self) -> Optional[Path]:
        """立即生成一次报表；返回输出文件路径（失败返回 None）。"""
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            ts = self._now_fn().strftime("%Y%m%d-%H%M%S")
            ext = "csv" if self._fmt == "csv" else "md"
            dest = self._out_dir / f"Git-clone-Max-报表-{ts}.{ext}"
            n = self._report_fn(dest, self._fmt)
            logger.info("定时报表已生成：%s（%s 行）", dest, n)
            return dest
        except Exception as e:  # noqa: BLE001
            logger.warning("定时报表生成失败：%s", e)
            return None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="gcm-sched-reports")
        self._thread.start()

    def _loop(self) -> None:
        evt = self._stop_evt
        while evt is not None and not evt.wait(self._interval):
            self.run_once()

    def stop(self) -> None:
        if self._stop_evt:
            self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self._stop_evt = None
