"""统一「在文件管理器中打开目录」（G80-B2 / 差距 G-06）。

为什么合并成一处
----------------
此前三处各写一遍，且**平台判断与打开器顺序都不一致**：

- `ui/download_tools.py`：`sys.platform == "win32"` → 否则 `xdg-open, open` 顺序试；
- `ui/progress_table.py`：同上（复制粘贴）；
- `ui/repo_detail_dialog.py`：`os.name == "nt"`（另一套判断）→ 否则 `open, xdg-open`
  （**顺序相反**，在同时装了两种打开器的 Linux 上行为不同）。

结果：同一个「打开目录」按钮，从下载页点、从进度表右键点、从仓库详情点，
在边界环境下可能表现不一致，且三份实现各自演化。

本模块的语义（刻意保守）
------------------------
- **不 import Qt**（`util` 层不依赖 Qt，CLAUDE.md §2）；提示/弹窗由调用方决定；
- 目录不存在 → 返回 False，不抛异常；
- 打开器缺失或启动失败 → 返回 False，不抛异常；
- 返回 True 只表示「已发起」，不保证文件管理器真的弹出来。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def open_folder(path: str | Path) -> bool:
    """在系统文件管理器中打开目录；成功发起返回 True。"""
    try:
        target = Path(path)
        if not target.is_dir():
            return False
    except OSError:
        return False

    if sys.platform == "win32":
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606 - Windows 专用
            return True
        except OSError:
            return False

    # POSIX：macOS 用 open，Linux 用 xdg-open；两者都按同一优先级探测。
    opener = shutil.which("open") or shutil.which("xdg-open")
    if not opener:
        return False
    try:
        subprocess.Popen([opener, str(target)])  # noqa: S603 - 受控参数，无 shell
        return True
    except OSError:
        return False
