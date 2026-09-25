"""看门狗续跑（G52-3）。

定时检测「续跑清单非空且上次未正常完成」→ 自动续跑（复用 engine.launch）。
要点：
- 幂等：同一时刻只允许一次续跑派发（引擎忙 / 已派发 → 跳过，下一 tick 再试）；
- 间隔：1/5/15/30 分钟（设置页约束），QTimer 驱动；
- 静默开关：silent=True 只日志不弹窗；False 由 main_window 弹提示；
- 不重复投：续跑后清单由 engine 结果回调更新，完成即 finished_ok → 不再续跑。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..db.runtime_sessions import RuntimeSessionStore

# 看门狗允许的间隔（分钟）
VALID_INTERVALS_MIN = (1, 5, 15, 30)
_DEFAULT_INTERVAL_MIN = 5


def clamp_interval(minutes: int) -> int:
    """把间隔收敛到合法集合 {1,5,15,30}（非法值回退默认 5）。"""
    try:
        m = int(minutes)
    except Exception:
        return _DEFAULT_INTERVAL_MIN
    if m in VALID_INTERVALS_MIN:
        return m
    # 就近收敛（0/负/未列出的值 → 默认 5）
    return _DEFAULT_INTERVAL_MIN


class Watchdog(QObject):
    """看门狗：engine 空闲时按间隔检查续跑清单并请求续跑。"""

    resume_requested = pyqtSignal(list)   # list[RepoSpec] 交给 main_window 调度

    def __init__(self, sessions_path: str | Path,
                 interval_min: int = _DEFAULT_INTERVAL_MIN,
                 silent: bool = False,
                 engine: Optional[Any] = None,
                 parent: Optional[QObject] = None,
                 store: Optional[RuntimeSessionStore] = None):
        super().__init__(parent)
        self.sessions_path = Path(sessions_path)
        self.store = store or RuntimeSessionStore(sessions_path)
        self.interval_min = clamp_interval(interval_min)
        self.silent = bool(silent)
        self.engine = engine                # 可选的 SyncEngine（busy 判定）
        self._dispatching = False           # 幂等闸门：同一时刻只派发一次
        self._timer = QTimer(self)
        self._timer.setInterval(self.interval_min * 60 * 1000)
        self._timer.timeout.connect(self.check)

    def start(self) -> None:
        """启动定时检测（幂等）。"""
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """停止定时检测（幂等）。"""
        try:
            self._timer.stop()
        except Exception:
            pass

    @property
    def interval_min(self) -> int:
        return self._interval_min

    @interval_min.setter
    def interval_min(self, value: int) -> None:
        self._interval_min = clamp_interval(value)

    def is_active(self) -> bool:
        return self._timer.isActive()

    def set_engine(self, engine: Any) -> None:
        """引擎重建后重新绑定（MainWindow._launch 会重建 SyncEngine）。"""
        self.engine = engine

    def check(self) -> bool:
        """检测并请求续跑；返回是否发出 resume_requested。

        幂等条件：引擎忙 / 清单空 / 上次已正常完成 / 已派发未复位 → False。
        """
        try:
            if self._dispatching:
                return False
            if self.engine is not None and getattr(self.engine, "busy", False):
                return False
            if not self.store.is_resumable():
                return False
            specs = self.store.to_repo_specs()
            if not specs:
                return False
            self._dispatching = True
            self.resume_requested.emit(specs)
            return True
        except Exception:
            return False

    def reset(self) -> None:
        """续跑派发完成（引擎空闲）后复位幂等闸门。"""
        self._dispatching = False
