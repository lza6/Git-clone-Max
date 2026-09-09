# -*- coding: utf-8 -*-
"""设置持久化：并发数 / 深浅克隆 / 自动清空 / 代理 / 超时 / 自动化开关 等。

存储为 data/settings.json，原子写入（temp + os.replace），损坏时兜底为默认值。
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Settings:
    """应用级可持久化配置。"""

    concurrency: int = 8              # 并行线程数 1..16
    shallow_default: bool = False     # 是否默认浅克隆
    depth: int = 1                    # 浅克隆深度
    auto_clear: bool = True           # 下载完成自动清空输入框
    proxy: str = ""                   # HTTP 代理，形如 http://127.0.0.1:7890
    fetch_timeout: int = 300          # 单次 fetch 超时秒数
    clone_timeout: int = 600          # 单次 clone 超时秒数
    retries: int = 2                  # 网络失败自动重试次数
    auto_init_empty: bool = False     # 空仓库自动初始化 main 并推送
    minimize_to_tray: bool = True     # 最小化时收进系统托盘
    language: str = "zh"              # 保留：zh / en（i18n 备用）
    token: str = ""                   # GitHub token（私有仓库认证，P3-3）
    fetch_unshallow: bool = False     # 浅克隆仓库增量 fetch 时拉全量历史（V3-P0-3）


_DEFAULTS: dict = asdict(Settings())


class SettingsStore:
    """线程安全、原子写、损坏兜底 的 settings.json 封装。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Settings:
        with self._lock:
            raw: dict = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        raw = data
                except Exception:
                    # 损坏兜底：保留默认值，不崩
                    raw = {}
            # 只取已知字段的类型安全合并
            merged = dict(_DEFAULTS)
            for k in _DEFAULTS:
                if k in raw:
                    try:
                        merged[k] = _coerce(k, raw[k])
                    except Exception:
                        pass
            return Settings(**merged)

    def save(self, s: Settings) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(asdict(s), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)  # 原子写


def _coerce(key: str, val):
    """按默认值类型强制转换，失败抛异常交由调用方兜底。"""
    default = _DEFAULTS[key]
    if isinstance(default, bool):
        return bool(val)
    if isinstance(default, int):
        return int(val)
    if isinstance(default, str):
        # None（如用户清空代理）→ 空字符串，避免出现字面 "None"
        return str(val) if val is not None else ""
    return val