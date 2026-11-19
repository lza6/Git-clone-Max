"""系统电源状态（批次③ / 对标批次③"桌面集成"）。

两个独立能力，**全部默认关**（用户在设置页手动开启）：
1. `prevent_sleep_during_batch`：批次进行期间阻止系统休眠
   （Windows `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`；
   POSIX 无对应语义，空实现）。长批次跑到一半机器睡眠会中断网络与子进程，
   这是"挂着刷几百个仓库"场景下用户明确要防的故障。
2. `sleep_after_batch`：批次完成后延迟 N 秒让系统睡眠
   （Windows `SetThreadExecutionState(ES_SYSTEM_REQUIRED)` 一次性解除 +
   `PowrProf.dll!SetSuspendState`；POSIX 空实现）。**关机/重启不做**——
   只请求睡眠（S4），可被任意输入/鼠标活动唤醒，破坏性远小于关机。

边界：
- 全部调用**永不抛**：电源能力缺失（非 Windows / 权限不足）时返回 False，
  上层按"功能不可用"提示，不阻塞克隆主流程。
- 睡眠倒计时由 UI 层 QTimer 驱动（可取消），本模块只提供原子动作。
"""
from __future__ import annotations

import sys

# Windows 电源请求常量（winbase.h）
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _is_windows() -> bool:
    return sys.platform == "win32" or sys.platform == "cygwin"


def prevent_sleep_start() -> bool:
    """开始"批次期间防休眠"。成功返回 True；平台不支持/失败返回 False。

    `SetThreadExecutionState` 是**每线程**状态，必须在调用线程持续有效；
    GUI 主线程持有整个批次周期，语义正好匹配。重复调用以最后一次为准（幂等）。
    """
    if not _is_windows():
        return False
    try:
        import ctypes
        flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        prev = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        return bool(prev)  # 0 = 失败
    except Exception as e:  # noqa: BLE001 —— 能力探测失败按"不可用"处理
        from .applog import warn as _warn
        _warn(f"SetThreadExecutionState 不可用：{e}")
        return False


def prevent_sleep_end() -> None:
    """结束防休眠（恢复系统默认睡眠策略）。幂等、永不抛。"""
    if not _is_windows():
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception as e:  # noqa: BLE001
        from .applog import warn as _warn
        _warn(f"SetThreadExecutionState 恢复失败：{e}")


def request_sleep() -> bool:
    """请求系统进入睡眠（S4 低功耗状态）。成功 True；不支持/失败 False。

    用 `SetSuspendState(FALSE, FALSE, FALSE)`：hibernate=FALSE（睡眠非休眠）、
    force=FALSE（尊重应用禁用）、disableWakeEvent=FALSE（可被唤醒）。
    """
    if not _is_windows():
        return False
    try:
        import ctypes
        powrprof = ctypes.windll.LoadLibrary("PowrProf.dll")
        # BOOL SetSuspendState(BOOL bHibernate, BOOL bForce, BOOL bDisableWakeEvent)
        return bool(powrprof.SetSuspendState(False, False, False))
    except Exception as e:  # noqa: BLE001 —— 能力探测失败按"不可用"处理
        from .applog import warn as _warn
        _warn(f"SetSuspendState 不可用：{e}")
        return False
