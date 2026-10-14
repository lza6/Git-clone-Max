"""JSON 原子写（G69-6）：唯一 tmp 名 + 可选跨进程锁 + 替换退避重试。

**为什么单独抽出来**：项目里有多处「读 JSON → 改 → 原子替换」的持久化
（`settings.json` / `progress.json` / `retry_queue.json` / `session_runtime.json`）。
同一个 bug 已经在至少两轮里被独立发现过两次：

- G67-23：`SettingsStore.save` 用固定 `settings.json.tmp` → 并发保存时两个线程
  同时打开同一 tmp（后者截断前者），先完成者 `os.replace` 走了它，后完成者撞
  `PermissionError [WinError 5]`（Windows 不允许替换被占用文件），**那一次保存被静默丢弃**。
- G69-6：同样的写法还留在 `retry_queue.py` / `runtime_sessions.py` / `progress_gc.py`。
  实测（同进程两个实例各写 150 条）：最终只剩 **16 条**、87 次 `PermissionError`；
  跨进程（各 120 条）：最终 **61 条**、226 个 tmp 快照里 **76 个非法 JSON** ——
  而这些异常都被调用方的 `except Exception` 吞掉，用户完全看不到。

**结论**：不能再靠「每个模块各自记得写对」。所有 JSON 持久化统一走这里。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .filelock import cross_process_lock

# 替换退避重试：Windows 上目标文件被**任何人**打开着（不共享删除）时
# `os.replace` 会 `PermissionError [WinError 5]`。持有者不限于本进程的读者 ——
# 杀毒软件扫描、Windows Search 索引、备份/同步工具都会对刚写完的文件短暂持有句柄。
# 实测：4 进程 × 150 次并发写中仍有 1 次落在 0.2s 的旧预算之外；
# 预算抬到约 3.9s（12 次，线性退避）后可覆盖这类瞬时占用。
# 这是**罕见**路径，最坏多等几秒，好过整次保存失败。
_REPLACE_RETRIES = 12
_REPLACE_BACKOFF_SEC = 0.05


def _unique_tmp(path: Path) -> Path:
    """与目标同名但含 pid + 线程 id 的临时文件。

    **必须唯一**：同进程多线程/多实例并发写同一目标时，共用一个 tmp 名会互相截断。
    """
    return path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")


def write_json_atomic(path: str | Path, payload: Any, *,
                      lock: bool = True, label: str = "文件",
                      indent: int = 2) -> None:
    """把 `payload` 序列化为 JSON 并**原子**写到 `path`。

    - `lock=True`（默认）：全程持 `cross_process_lock`，读-改-写临界区由调用方负责，
      但写入本身也需在锁内完成，否则两个进程的 replace 会互相覆盖。
    - 失败**上抛**（不静默吞）：调用方要么给用户可见的错误，要么显式记录。
      本项目历史上正是「吞掉异常」让这类数据丢失长期不可见。
    """
    target = Path(path)
    tmp = _unique_tmp(target)

    def _do_write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=indent),
                       encoding="utf-8")
        last: Exception | None = None
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp, target)
                return
            except PermissionError as e:
                last = e
                time.sleep(_REPLACE_BACKOFF_SEC * (attempt + 1))
        # 退避用尽：清理 tmp，避免留下垃圾，然后如实上抛
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        if last is not None:
            raise last

    if lock:
        with cross_process_lock(target, label=label):
            _do_write()
    else:
        _do_write()
