"""结构化应用日志（G22-5 / G21-3 依赖）。

- data/app.log，RotatingFileHandler 5MB x 3
- 格式: 时间|级别|模块|消息（可 grep）
- 与 UI 环形日志互不影响；任何失败静默降级（日志不可用不能影响主流程）
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_logger: logging.Logger | None = None


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
                fh.setFormatter(logging.Formatter(
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
