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
import tempfile
import time
from pathlib import Path
from typing import Any

from .filelock import cross_process_lock

# 替换退避重试：Windows 上目标文件被**任何人**打开着（不共享删除）时
# `os.replace` 会 `PermissionError [WinError 5]`。持有者不限于本进程的读者 ——
# 杀毒软件扫描、Windows Search 索引、备份/同步工具都会对刚写完的文件短暂持有句柄。
# 实测：4 进程 × 150 次并发写中仍有 1 次落在 0.2s 的旧预算之外；
# 预算抬到约 3.9s（12 次，线性退避）后可覆盖这类瞬时占用。
_REPLACE_RETRIES = 12
_REPLACE_BACKOFF_SEC = 0.05
# G72：Windows 上共享冲突/锁冲突不一定表现为 PermissionError；仅对
# 「瞬时占用」的 winerror 重试，真正的没权限（如 5 之外的 ACL）立即上抛。
_TRANSIENT_WINERRORS = frozenset({5, 32, 33, 1224})


def _is_transient_replace_error(exc: OSError) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS


def replace_with_retry(tmp: str | Path, target: str | Path, *,
                       retries: int | None = None) -> None:
    """把 `tmp` 原子替换为 `target`（Windows 瞬时占用退避）。

    G72：提取为公共函数，供 `write_text_atomic` 与 `diag_bundle` 的 zip 共用，
    避免两处各写一份退避/清理逻辑。失败时清理 tmp 并上抛。
    """
    tmp_p = Path(tmp)
    target_p = Path(target)
    attempts = _REPLACE_RETRIES if retries is None else max(1, int(retries))
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            os.replace(tmp_p, target_p)
            _fsync_dir(target_p.parent)
            return
        except OSError as e:
            if not _is_transient_replace_error(e):
                try:
                    tmp_p.unlink(missing_ok=True)
                except Exception:
                    pass
                raise
            last = e
            time.sleep(_REPLACE_BACKOFF_SEC * (attempt + 1))
    try:
        tmp_p.unlink(missing_ok=True)
    except Exception:
        pass
    raise last if last is not None else OSError("原子替换失败")


def _fsync_dir(path: Path) -> None:
    """把目录项也刷到磁盘（POSIX）。

    只 `fsync` 文件不够：`os.replace` 是**目录项**变更，掉电后可能出现
    「新内容已落盘但目录仍指向旧 inode」或「rename 丢失」。Windows 不支持
    以目录句柄 `fsync`，跳过即可（NTFS 的元数据日志另有保障）。
    """
    if os.name == "nt":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def write_text_atomic(path: str | Path, text: str, *,
                      lock: bool = True, label: str = "文件",
                      encoding: str = "utf-8",
                      retries: int | None = None) -> None:
    """把文本**原子**写到 `path`：唯一 tmp → flush+fsync → replace（带退避）。

    与 `write_json_atomic` 共用同一套替换/退避/清理逻辑；`lock=False` 供
    已经持有跨进程锁的调用方使用（避免同路径重复加锁）。

    `retries`：替换退避次数（默认 12，约 3.9s 预算）。**UI 热路径**
    （history/任务清单）可传更小值，避免杀软短暂占用时拖住界面。
    """
    target = Path(path)
    attempts = _REPLACE_RETRIES if retries is None else max(1, int(retries))

    def _do_write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        # G72：mkstemp 保证 O_EXCL + 随机名；同 pid/tid 串行复用也不会撞名，
        # 崩溃残留的旧 tmp 不会被静默截断。
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
                f.write(text)
                f.flush()
                try:
                    os.fsync(f.fileno())   # 内容先落盘，再替换目录项
                except OSError:
                    pass                    # 某些文件系统不支持 fsync；不阻断写入
            replace_with_retry(tmp, target, retries=attempts)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    if lock:
        with cross_process_lock(target, label=label):
            _do_write()
    else:
        _do_write()


def write_json_atomic(path: str | Path, payload: Any, *,
                      lock: bool = True, label: str = "文件",
                      indent: int = 2, retries: int | None = None) -> None:
    """把 `payload` 序列化为 JSON 并**原子**写到 `path`。

    - `lock=True`（默认）：全程持 `cross_process_lock`，读-改-写临界区由调用方负责，
      但写入本身也需在锁内完成，否则两个进程的 replace 会互相覆盖。
    - 失败**上抛**（不静默吞）：调用方要么给用户可见的错误，要么显式记录。
      本项目历史上正是「吞掉异常」让这类数据丢失长期不可见。
    """
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    write_text_atomic(path, text, lock=lock, label=label, encoding="utf-8",
                      retries=retries)
