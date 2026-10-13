"""progress.json 断点续传读写（G67-14：从 `repo_db.py` 拆出）。

拆分理由：`repo_db.py` 已达 800 行上限，而这段是**与 SQLite 无关**的纯文件
持久化 —— 混在 DB 模块里既撑大了文件，也逼出一个「模块中部 import」的
变通写法。所有公开符号仍在 `gcm/db/repo_db.py` 里**原样再导出**，
调用方（engine / worker / UI / 测试）无需任何改动。
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..models import RepoSpec
from ..util.filelock import cross_process_lock

# 空进度文件的标准骨架（补齐新键，旧文件无痛升级）
_EMPTY = {"finished": [], "in_progress": {}, "failed": []}


@contextmanager
def progress_guard(path: Path) -> Iterator[None]:
    """线程锁 + 进程锁双层互斥，保护对 progress.json 的读改写。

    实现共用 `gcm/util/filelock.cross_process_lock`，与 `SettingsStore`
    （settings.json）是同一套加锁/降级/fd 治理逻辑 —— 避免两处各写一份，
    再次出现「一处修了另一处没修」。
    """
    with cross_process_lock(path, label="progress.json"):
        yield


# 兼容别名：原 `repo_db._progress_guard` 的内部名
_progress_guard = progress_guard


def load_progress(path: Path) -> dict[str, Any]:
    """读取 progress.json；不存在或损坏 → 返回空骨架（绝不抛）。"""
    if not path.exists():
        return dict(_EMPTY)
    try:
        with progress_guard(path), open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("finished", [])
        data.setdefault("in_progress", {})
        data.setdefault("failed", [])
        return data
    except Exception:
        return dict(_EMPTY)


def save_progress(path: Path, data: dict[str, Any]) -> None:
    """原子写 progress.json（tmp + os.replace），全程持锁。"""
    tmp = path.with_suffix(".json.tmp")
    with progress_guard(path):
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # 原子写


def mark_finished(path: Path, spec: RepoSpec, result: dict[str, Any]) -> None:
    """把某仓库标记为已完成（读-改-写）。

    G67-13 修复：原实现是 `load_progress()` + `save_progress()` 两次独立加锁 ——
    读与写之间可被其它进程/线程插入，造成**丢失更新**（并发大批量时偶发）。
    现改为在**同一个临界区**内完成读-改-写。
    """
    key = f"{spec.owner}/{spec.repo}"
    tmp = path.with_suffix(".json.tmp")
    with progress_guard(path):
        data: dict[str, Any]
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        else:
            data = {}
        data.setdefault("finished", [])
        data.setdefault("in_progress", {})
        data.setdefault("failed", [])
        data["finished"] = [x for x in data["finished"] if x.get("key") != key]
        data["finished"].append({"key": key, **result})
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
