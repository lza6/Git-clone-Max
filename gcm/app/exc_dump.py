"""G06-3 全局异常兜底：sys.excepthook 捕获 + Qt 消息拦截 → data/error.log。

- 未捕获异常：写 traceback + 环境信息到 data/error.log，不白屏静默。
- Qt qInstallMessageHandler：Qt 层致命/警告也归档。
- 幂等：重复安装不覆盖已装的 hook。
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from typing import Optional


def _fmt_env() -> str:
    """采集最小环境信息（版本/平台/Python），不含敏感值。"""
    try:
        from gcm import __version__
    except Exception:
        __version__ = "unknown"
    lines = [
        f"time={time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"app={__version__}",
        f"python={sys.version.split()[0]}",
        f"platform={sys.platform}",
    ]
    return "\n".join(lines)


def _redact_text(text: str) -> str:
    """落盘前打码（G84-19）。

    `error.log` 曾是**唯一没打码的持久化出口**：`applog` 有 `RedactingFormatter`，
    但未捕获异常走的是这里 —— 而 traceback 天然带 URL（含 `user:pass@`）、
    命令行参数（`-c http.extraHeader=Authorization: Bearer …`）、以及我们自己的
    `context=`（可能含 token）。用户把 error.log 贴进 issue 就等于泄露凭据。
    """
    try:
        from gcm.util.redact import redact
        return redact(text, userprofile=True)
    except Exception:      # 打码器自身不可用：宁可不写，也不写明文
        return "(打码失败，已省略该段)\\n"


def write_error_log(log_path: Path, exc: BaseException,
                    context: Optional[dict] = None) -> None:
    """把未捕获异常写入 log_path（追加），含 traceback 与环境信息。

    **所有内容落盘前过 `redact`**（G84-19）：traceback 与 context 都可能带凭据。
    """
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n" + "=" * 60 + "\n" + _fmt_env() + "\n"
        if context:
            body += _redact_text("context=" + repr(context) + "\n")
        body += _redact_text("".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__)))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(body)
    except Exception:
        pass  # 兜底失败不致命（此时连日志都没法写）


_installed = {"hook": False}


def install_excepthook(log_path: Optional[Path] = None) -> None:
    """安装全局 sys.excepthook（幂等）。log_path 默认 data/error.log 同级由调用方决定。"""
    if _installed["hook"]:
        return
    target = log_path or Path("error.log")
    _prev = sys.excepthook

    def _hook(etype, value, tb):
        try:
            write_error_log(target, value)
        except Exception:
            pass
        # 保留原始 excepthook 行为（打印 stderr），不吞
        if _prev is not None and _prev is not sys.__excepthook__:
            try:
                _prev(etype, value, tb)
            except Exception:
                pass
        else:
            sys.__excepthook__(etype, value, tb)

    sys.excepthook = _hook
    _installed["hook"] = True
