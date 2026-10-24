"""G71-6 诊断包导出：把「报障需要的上下文」打成一个可外发的 zip。

参考项目（FinceptTerminal / DeepSeekGUI / frpc-desktop）的共同做法：
- 诊断不是让用户截图，而是**一键导出**；
- 包内只放已打码的文本，不含 token / URL 凭据 / 仓库内容；
- 附一份 manifest（版本、时间、文件清单、截断说明），便于对方核对。

包含内容（全部过 `redact()`）：
- `report.txt`      调用方给的体检/状态报告
- `events.jsonl`    EventBus 最近事件（默认 500 条；黑匣子）
- `app.log`         `app.log` 末尾若干行
- `notify.log`      通知审计末尾若干行（若存在）
- `manifest.json`   版本/时间/文件清单/事件统计

**不包含**：settings.json（含加密凭据）、仓库内容、数据库文件。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from ..util.atomic_io import replace_with_retry
from ..util.redact import redact

_MAX_LOG_BYTES = 512 * 1024       # 单份日志最多带 512KB 尾部
_MAX_REPORT_BYTES = 2 * 1024 * 1024
_MAX_EVENTS = 500
_MAX_EVENT_BYTES = 2 * 1024 * 1024
_REDACTION_VERSION = "2"          # G72：路径变体 + 高置信 token 前缀


def _redact_text(text: str) -> str:
    """诊断包统一打码：凭据 + **本机 home 路径**（审查 R3）。

    `redact()` 的 userprofile 默认跟随全局开关，而该开关当前无生产调用点；
    诊断包承诺「可安全外发」，所以这里必须显式 `userprofile=True`。
    """
    return redact(text, userprofile=True)


def _redact_obj(value: Any, depth: int = 0) -> Any:
    """递归打码 manifest 的 extra（可能被调用方塞入路径/URL）。"""
    if depth > 4:
        return "<truncated>"
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(k): _redact_obj(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_obj(v, depth + 1) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(str(value))


def _tail_text(path: Path, max_bytes: int = _MAX_LOG_BYTES) -> str:
    """读取文件尾部并按行截断；缺失/不可读 → 空字符串（不抛）。"""
    try:
        if not path.exists() or not path.is_file():
            return ""
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()             # 丢弃可能被截半的第一行
            raw = f.read()
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _events_jsonl(bus: Any, limit: int = _MAX_EVENTS) -> tuple[str, int, bool]:
    """把最近事件序列化为 JSONL；返回 (文本, 条数, 是否因体积截断)。"""
    if bus is None:
        return "", 0, False
    try:
        events = bus.snapshot(limit=limit)
    except Exception:
        return "", 0, False
    lines: list[str] = []
    total = 0
    truncated = False
    for ev in events:
        try:
            line = _redact_text(ev.to_json())
        except Exception:
            continue
        total += len(line.encode("utf-8")) + 1
        if total > _MAX_EVENT_BYTES:
            truncated = True
            break
        lines.append(line)
    return ("\n".join(lines) + ("\n" if lines else "")), len(lines), truncated


def build_manifest(*, version: str, files: dict[str, int],
                   events: int, events_truncated: bool = False,
                   report_truncated: bool = False,
                   extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """诊断包 manifest（纯函数，便于测试）。"""
    return {
        "tool": "Git-clone-Max",
        "version": version,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": time.strftime("%z"),
        "redaction_version": _REDACTION_VERSION,
        "files": dict(files),
        "events": int(events),
        "events_truncated": bool(events_truncated),
        "report_truncated": bool(report_truncated),
        "note": "所有文本已 redact 打码；不含 settings.json / 仓库内容 / 数据库文件。",
        "extra": dict(extra or {}),
    }


def build_diag_bundle(out_path: str | Path, *,
                      data_dir: str | Path,
                      report_text: str = "",
                      event_bus: Any = None,
                      app_log_name: str = "app.log",
                      notify_log_name: str = "notify.log",
                      extra_manifest: Optional[dict[str, Any]] = None) -> Path:
    """生成诊断 zip 并**原子**落盘；返回最终路径。

    写入顺序：唯一临时文件 → `zipfile` 写完并关闭 → `os.replace`。
    任何一步失败都会清理临时文件并上抛（调用方负责提示用户）。
    """
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    data = Path(data_dir)
    raw_report = str(report_text or "")
    report_truncated = len(raw_report.encode("utf-8")) > _MAX_REPORT_BYTES
    if report_truncated:
        raw_report = raw_report.encode("utf-8")[:_MAX_REPORT_BYTES].decode(
            "utf-8", errors="ignore") + "\n[TRUNCATED: report exceeds 2MB]\n"
    report = _redact_text(raw_report)
    events_text, event_count, events_truncated = _events_jsonl(event_bus)
    app_log = _redact_text(_tail_text(data / app_log_name))
    notify_log = _redact_text(_tail_text(data / notify_log_name))

    payloads: dict[str, str] = {
        "report.txt": report,
        "events.jsonl": events_text,
        "app.log": app_log,
        "notify.log": notify_log,
    }
    # 空文件也保留（明确「没有」而不是「忘了放」）
    files = {name: len(text.encode("utf-8")) for name, text in payloads.items()}
    try:
        from .. import __version__
        version = __version__
    except Exception:
        version = "unknown"
    manifest = build_manifest(version=version, files=files,
                              events=event_count,
                              events_truncated=events_truncated,
                              report_truncated=report_truncated,
                              extra=_redact_obj(extra_manifest or {}))

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".zip")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, text in payloads.items():
                zf.writestr(name, text)
        replace_with_retry(tmp, target)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return target


def default_bundle_name(prefix: str = "gcm-diag") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.zip"


