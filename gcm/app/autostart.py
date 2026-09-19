"""G49-6 开机自启（Windows 任务计划）真实写入。

说明：v7.8 之前设置页/托盘只有「开机自启」勾选 UI 与联动，从未真正写
任务计划（schtasks）。本模块补齐真实写入：
- set_autostart(enabled, minimized)：schtasks /Create|/Delete（幂等，/F 覆盖）
- 自启命令 = <可执行入口> + 可选 --minimized（start_minimized 开启时）
- 非 Windows / schtasks 缺失：返回 (False, 原因)，绝不抛异常（CI 三平台安全）
- 测试经 mock subprocess 断言命令含 --minimized；真实写入留给用户显式勾选
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "Git-clone-Max"


def launch_command(minimized: bool = False) -> list[str]:
    """返回自启入口命令行（list，便于断言/执行）。

    - 打包（frozen）：sys.executable（Git-clone-Max.exe）
    - 源码：python -m gcm（避免依赖 .venv/Scripts/python 具体路径）
    源码模式下 from __main__ import main 可保持入口一致。
    """
    if getattr(sys, "frozen", False):
        cmd = [str(Path(sys.executable).resolve())]
    else:
        cmd = [sys.executable, "-m", "gcm"]
    if minimized:
        cmd.append("--minimized")
    return cmd


def _quote_cmd(cmd: list[str]) -> str:
    """Windows 命令行引号化；首项可执行文件必须引号包裹。"""
    parts = [f'"{c}"' for c in cmd]
    return " ".join(parts)


def set_autostart(enabled: bool, minimized: bool = False) -> tuple[bool, str]:
    """写入/删除 Windows 任务计划（登录时启动一次，限权限）。

    返回 (是否成功, 提示文案)。任何异常都不上抛（调用方 UI 仅展示提示）。
    """
    if os.name != "nt":
        return False, "开机自启仅支持 Windows 任务计划（当前平台跳过）"
    try:
        if enabled:
            tr_cmd = _quote_cmd(launch_command(minimized=minimized))
            # /SC ONLOGON 登录时启动一次；/RL LIMITED 最低权限；/F 覆盖已有
            cmd = ["schtasks", "/Create", "/F", "/TN", TASK_NAME,
                   "/TR", tr_cmd, "/SC", "ONLOGON", "/RL", "LIMITED"]
        else:
            cmd = ["schtasks", "/Delete", "/F", "/TN", TASK_NAME]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True, ("已写入开机自启任务计划" if enabled else "已移除开机自启任务计划")
        detail = (r.stderr or r.stdout or "").strip()[:200]
        return False, f"任务计划操作失败（rc={r.returncode}）：{detail}"
    except FileNotFoundError:
        return False, "未找到 schtasks（任务计划工具不可用）"
    except Exception as e:  # noqa: BLE001
        return False, f"任务计划操作异常：{e}"


def autostart_enabled() -> bool:
    """查询任务计划是否存在（GUI 初始化回填勾选态）。失败保守返回 False。"""
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False
