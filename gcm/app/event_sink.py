"""事件落盘（Rev8 C5-3）：`data/events/YYYYMMDD.jsonl`，按天轮转、≤20MB、入盘前已打码。

为什么需要：事件总线是**进程内、非持久**的（CLAUDE.md 已登记边界）——独立
`--serve` 进程看不到 GUI 进程的事件，崩溃后也无法审计"那批到底发生了什么"。
落盘后两进程对账同 seq 成为可能，也为 C5-2 契约导出提供真实样本。

设计约束（与项目红线对齐）：
- **永不阻塞、永不抛**：写失败静默丢弃（与 publish 同语义），绝不影响克隆。
- **打码前置**：`publish()` 已在入总线前 `redact()`，sink 拿到的就是安全文本；
  sink 自身再不做二次处理（单一事实源）。
- **按天轮转 + 大小上限**：`events_YYYYMMDD.jsonl`，单文件超过 `_MAX_BYTES`
  就换 `events_YYYYMMDD_N.jsonl`；启动时清理 7 天前的旧文件（保守默认）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_MAX_BYTES = 20 * 1024 * 1024   # 单文件 20MB
_KEEP_DAYS = 7                  # 保留最近 7 天


class EventSink:
    """把 EventBus 的事件追加写进按天轮转的 jsonl 文件。线程安全、永不抛。"""

    def __init__(self, data_dir: Path, keep_days: int = _KEEP_DAYS,
                 max_bytes: int = _MAX_BYTES):
        self._dir = Path(data_dir) / "events"
        self._keep_days = max(1, int(keep_days))
        self._max_bytes = max(1024, int(max_bytes))
        self._lock = threading.Lock()
        self._day = ""
        self._fh: Any = None
        self._path: Path | None = None
        self.written = 0
        self.errors = 0

    def append(self, event: Any) -> None:
        """追加一条事件（要求已 redact）。任何失败只计数不抛。"""
        try:
            day = time.strftime("%Y%m%d", time.localtime(event.ts))
            line = json.dumps(
                {"seq": event.seq, "ts": event.ts, "type": event.type,
                 "data": event.data},
                ensure_ascii=False, separators=(",", ":")) + "\n"
            with self._lock:
                if self._day != day or self._fh is None:
                    self._rotate(day)
                if self._fh is None:
                    self.errors += 1
                    return
                self._fh.write(line)
                self.written += 1
                if self._fh.tell() >= self._max_bytes:
                    self._rotate(day)
        except Exception:  # noqa: BLE001 - 落盘故障绝不影响主流程（含坏 event 对象）
            self.errors += 1

    def _rotate(self, day: str) -> None:
        """切到（可能的）新文件；顺带做一次旧文件清理。"""
        try:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
            self._dir.mkdir(parents=True, exist_ok=True)
            n = 0
            while True:
                suffix = f"_{n}" if n else ""
                p = self._dir / f"events_{day}{suffix}.jsonl"
                if not p.exists() or p.stat().st_size < self._max_bytes:
                    break
                n += 1
            self._path = p
            self._fh = open(p, "a", encoding="utf-8")  # noqa: SIM115 - 常驻句柄，close 在 _rotate
            self._day = day
            self._cleanup()
        except Exception:  # noqa: BLE001
            self._fh = None

    def _cleanup(self) -> None:
        """删 keep_days 之前的旧事件文件。"""
        try:
            cutoff = time.strftime(
                "%Y%m%d", time.localtime(time.time() - self._keep_days * 86400))
            for p in self._dir.glob("events_*.jsonl"):
                d = p.name[len("events_"):-len(".jsonl")].split("_")[0]
                if d < cutoff:
                    p.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:  # 关闭失败无害（进程退出也会释放句柄）
                    pass
                self._fh = None


def attach_sink(bus: Any, data_dir: Path) -> EventSink:
    """把 sink 挂到 EventBus（monkey-patch publish，保持零侵入）。"""
    sink = EventSink(data_dir)
    orig = bus.publish

    def publish_with_sink(event_type: str, data=None):
        ev = orig(event_type, data)
        if ev is not None:
            sink.append(ev)
        return ev

    try:
        bus.publish = publish_with_sink  # type: ignore[method-assign]
        bus._event_sink = sink  # noqa: SLF001 - 调试/测试取用
    except (AttributeError, TypeError):  # 非 EventBus 兼容对象 → 只跳过挂载
        pass
    return sink
