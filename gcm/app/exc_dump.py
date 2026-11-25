"""G06-3 全局异常兜底：sys.excepthook + threading.excepthook 捕获 + Qt 消息拦截 → data/error.log。

- 未捕获异常：写 traceback + 环境信息到 data/error.log，不白屏静默。
- 线程内未捕获异常同样落盘：windowed 打包（无控制台）时默认 hook 的 stderr
  输出不可见，线程里逃逸的异常等于无声消失（2026-11-26 排查 0xC0000409 时确认的盲区）。
- Qt 消息（qWarning/qCritical/qFatal）经注入式 `install_qt_message_handler`
  写入同一 error.log：`gcm/app` 不 import Qt（G80 边界），Qt 顶层函数由
  GUI 入口注入（见 `gcm/__main__.py` 安装点）。
- 幂等：重复安装不覆盖已装的 hook。
"""
from __future__ import annotations

import contextlib
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Optional


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
    """安装全局 sys.excepthook + threading.excepthook（幂等）。

    log_path 默认 data/error.log 同级由调用方决定。
    """
    if _installed["hook"]:
        return
    target = log_path or Path("error.log")
    _prev = sys.excepthook
    _prev_thread = threading.excepthook

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

    def _thread_hook(args):
        """线程内未捕获异常 → 同一 error.log（附线程名），再交还原 hook。

        SystemExit 按 CPython 官方默认语义**静默忽略**（`threading.excepthook`
        文档：「If exc_type is SystemExit, the exception is silently ignored」）——
        正常线程退出不该在 error.log 里伪装成错误噪音。
        不额外包 try：write_error_log 自身绝不抛；若链上的原 hook 抛异常，
        CPython 会按官方语义转交 sys.excepthook —— 即上面的 _hook，链路闭环。
        """
        if args.exc_type is SystemExit:
            return
        write_error_log(target, args.exc_value,
                        context={"thread": getattr(args.thread, "name", "?")})
        _prev_thread(args)

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
    _installed["hook"] = True


#: QtMsgType 成员名 → 日志级别标签（跨 Qt 版本按名字取，不硬依赖枚举值）。
_QT_LEVEL_NAMES = {
    "QtDebugMsg": "DEBUG",
    "QtInfoMsg": "INFO",
    "QtWarningMsg": "WARN",
    "QtCriticalMsg": "CRITICAL",
    "QtFatalMsg": "FATAL",
}


def install_qt_message_handler(q_install: Any,
                               log_path: Optional[Path] = None) -> None:
    """把 Qt 层消息（qWarning/qCritical/qFatal）接进 error.log（幂等）。

    **G80 边界**：`gcm/app` 不 import Qt —— `qInstallMessageHandler`
    由 GUI 入口注入（见 `gcm/__main__.py` 安装点）。

    windowed 打包没有控制台，Qt 默认 handler 的输出全部不可见；而 `qFatal`
    在 handler 返回后**立即 abort** —— 本条日志就是 0xC0000409 类闪退唯一
    能留下的「遗言」。handler 必须可重入（Qt 文档要求，可能被多线程并行
    调用；逐条独立 open/append 满足）；内部异常一律吞掉，观测不拖垮进程。
    文件内容过 `redact`（Qt 消息天然带用户路径）；控制台存在时同文案双写。
    """
    if _installed.get("qt"):
        return
    target = log_path or Path("error.log")
    prev = q_install(None)   # Qt 语义：传 None 摘回默认 handler 并返回旧 handler

    def _handler(mode: Any, _context: Any, message: str) -> None:
        level = _QT_LEVEL_NAMES.get(getattr(mode, "name", ""), "OTHER")
        with contextlib.suppress(Exception):
            # 文件与控制台共用同一份打码文本（出站即打码，Qt 消息天然带用户路径）
            line = _redact_text(f"qt[{level}] {message}".replace("\x00", ""))
            with open(target, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 60 + "\n" + _fmt_env() + "\n" + line + "\n")
            # 控制台开发时保留可见性（windowed 打包下 sys.stderr 为 None）
            if sys.stderr is not None:
                print(line, file=sys.stderr)
        if prev is not None:
            with contextlib.suppress(Exception):
                prev(mode, _context, message)

    q_install(_handler)
    _installed["qt"] = True
