"""结构化应用日志（G22-5 / G21-3 依赖）。

- data/app.log，RotatingFileHandler 5MB x 3
- 格式: 时间|级别|模块|消息（可 grep）
- 与 UI 环形日志互不影响；任何失败静默降级（日志不可用不能影响主流程）
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..util.redact import redact

_logger: logging.Logger | None = None

# 只有出现这些「可能含凭据」的廉价特征才跑正则；避免每条日志都做 5 次 regex。
_REDACT_HINTS = (
    "://", "authorization", "private-token", "x-auth-token", "x-api-key",
    "job-token", "access_token", "private_token", "token=", "password=",
    "ghp_", "github_pat_", "glpat-", "xoxb-", "xoxp-", "akia", "aiza",
)


class RedactingFormatter(logging.Formatter):
    """日志落盘前统一打码（G72，审查调研 #7）。

    为什么放在 Formatter 而不是各调用点：`app.log` 可能被用户手动发送/被第三方
    采集工具读取；只在诊断包导出时打码无法覆盖这条路径。Formatter 能看到
    **最终格式化后的完整文本**（含 args 与异常堆栈），是最不容易漏的一层。
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        low = text.lower()
        if any(h in low for h in _REDACT_HINTS):
            return redact(text)
        return text


def get_logger(log_path: str | Path | None = None) -> logging.Logger:
    """获取应用级 logger（单例；文件 handler 惰性挂载）。

    首次调用若未带 path（如某些导入路径先打日志），后续带 path 的调用仍会
    挂上文件 handler——保证「先无文件后有文件」的场景也能落盘。
    """
    global _logger
    if _logger is None:
        logger = logging.getLogger("gcm.app")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _logger = logger
    if log_path is not None:
        # G50-3 多路径：显式给定不同 log_path 时也挂新 handler（避免单例夺吃新路径导致无法落盘）
        _target = str(Path(log_path).resolve())
        has_file_handler = any(
            isinstance(h, RotatingFileHandler)
            and str(Path(h.baseFilename).resolve()) == _target
            for h in _logger.handlers
        )
        if not has_file_handler:
            try:
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                fh = RotatingFileHandler(str(log_path), maxBytes=5 * 1024 * 1024,
                                         backupCount=3, encoding="utf-8")
                fh.setFormatter(RedactingFormatter(
                    "%(asctime)s|%(levelname)s|%(name)s|%(message)s"))
                _logger.addHandler(fh)
            except Exception:
                pass  # 磁盘不可写等：退化为无文件输出
    return _logger


def info(msg: str, log_path: str | Path | None = None) -> None:
    try:
        get_logger(log_path).info(msg)
    except Exception:
        pass


def warn(msg: str, log_path: str | Path | None = None) -> None:
    try:
        get_logger(log_path).warning(msg)
    except Exception:
        pass


def error(msg: str, log_path: str | Path | None = None) -> None:
    try:
        get_logger(log_path).error(msg)
    except Exception:
        pass


def reset_for_tests() -> None:
    """测试用：重置单例（换临时目录时）。"""
    global _logger
    if _logger is not None:
        try:
            for h in list(_logger.handlers):
                h.close()
                _logger.removeHandler(h)
        except Exception:
            pass
    _logger = None
