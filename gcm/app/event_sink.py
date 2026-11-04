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
import os
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
        # R6（审查发现）：GUI 进程 + N 个 `gcm --cli` 子进程会同时 append 同一个
        # 文件，而这里只有线程锁 —— 跨进程交错写会产生半行 JSON，正好毁掉这个
        # 功能存在的唯一理由（可审计）。修法不是加跨进程锁（逐事件加锁代价高），
        # 而是**每个进程写自己的文件**（文件名带 pid），单写者天然无竞态。
        self._pid = os.getpid()
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
                p = self._dir / f"events_{day}_{self._pid}{suffix}.jsonl"
                if not p.exists() or p.stat().st_size < self._max_bytes:
                    break
                n += 1
            self._path = p
            # buffering=1 → 行缓冲：每写完一行就 flush，崩溃时最多丢半行而不是整段
            self._fh = open(p, "a", encoding="utf-8",  # noqa: SIM115 - 常驻句柄，close 在 _rotate
                            buffering=1)
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
                # 形如 events_20261104_<pid>[_n].jsonl → 第一段是日期
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


# ---------------------------------------------------------------- G84-5 生产接线
# 背景（本轮实测）：`attach_sink` 写好了、测试也过了，但**全仓没有任何生产调用点**
# （`grep -rn "attach_sink" gcm/` 只命中本文件与 `tests/test_c5_agent_contract.py`）。
# 于是 v9.16.0 changelog 宣称的「事件落盘」在用户机器上从未发生 ——
# 撞 CLAUDE.md §5「新增模块/函数必须有生产调用点」与「禁止伪闭环」两条红线。
# 修法：加一个**唯一的幂等入口**，由 `gcm/__main__.py` 在 GUI / CLI / --serve
# 三种形态启动时统一调用（MCP 是独立进程且不发布引擎事件，故不挂）。

_ENABLED: EventSink | None = None


def enable_event_sink(data_dir: Any) -> EventSink | None:
    """把落盘 sink 挂到全局事件总线。**幂等、永不抛**（与 publish 同语义）。

    返回已挂载的 sink；任何失败（目录不可写 / 总线不可用）只记一条 warn 并
    返回 None —— 事件落盘是**观测增强**，绝不允许它影响克隆。
    """
    global _ENABLED
    if _ENABLED is not None:
        return _ENABLED
    try:
        from .event_bus import get_event_bus

        bus = get_event_bus()
        existing = getattr(bus, "_event_sink", None)
        if existing is not None:      # 已被别的入口挂过（GUI 内嵌 serve 场景）
            _ENABLED = existing
            return _ENABLED
        _ENABLED = attach_sink(bus, Path(data_dir))
        # R6：进程退出时把尾部事件 flush 掉（原来没人调用 close()）
        try:
            import atexit
            atexit.register(_ENABLED.close)
        except Exception as e:      # noqa: BLE001 —— 注册失败不影响落盘本身
            try:
                from .applog import warn as _warn
                _warn(f"事件落盘退出时关闭注册失败（尾部事件可能丢）：{e}")
            except Exception:
                pass
        return _ENABLED
    except Exception as e:            # noqa: BLE001 —— 观测增强失败不得拖垮启动
        try:
            from .applog import warn as _warn
            _warn(f"事件落盘未启用（不影响克隆）：{e}")
        except Exception:             # pragma: no cover - 日志系统自身不可用
            return None
        return None


def reset_event_sink_for_tests() -> None:
    """仅测试使用：清掉模块级「已启用」标记，让下一条总线能重新挂载。"""
    global _ENABLED
    _ENABLED = None
