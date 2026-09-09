# -*- coding: utf-8 -*-
"""G02-4 URL 历史：最近同步地址留档 + 去重 + 上限裁剪 + 右键回填。

存储 data/history.json，原子写，损坏兜底。上限 200 条（最新在前）。
同步成功后由 MainWindow 调用 add()，输入框右键菜单读 items() 回填。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

MAX_HISTORY = 200


class UrlHistory:
    """最近同步 URL 历史（线程安全、原子写、损坏兜底）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._items: list[str] = []
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._items = [str(x) for x in data if x][:MAX_HISTORY]
        except Exception:
            self._items = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def add(self, url: str) -> None:
        """把地址加入历史（去重、最新在前、裁剪上限）。"""
        url = (url or "").strip()
        if not url:
            return
        with self._lock:
            self._items = [u for u in self._items if u != url]
            self._items.insert(0, url)
            if len(self._items) > MAX_HISTORY:
                self._items = self._items[:MAX_HISTORY]
            self._save()

    def items(self) -> list[str]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items = []
            try:
                self.path.unlink(missing_ok=True)
            except Exception:
                pass
