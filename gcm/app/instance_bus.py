"""B2：第二启动唤醒首实例的进程间通道（纯 Python socket，零 Qt 依赖）。

背景：`__main__.py` 的注释一直写着「第二个实例被拦截并聚焦首个窗口」，实现却
只 print 后退出 —— 用户双击托盘/桌面图标看到的是"没反应"。文件锁
（`app/single_instance.py`）继续做互斥，本模块只承担"唤醒 + 参数转发"：

- 首实例（GUI）在文件锁之后启动 `WakeServer`：绑定 127.0.0.1 随机端口，
  把 `{port, token, pid}` 原子写入 `<数据目录>/instance.json`；
- 第二实例（拿锁失败）读该文件、校验 PID 存活、带 token 连上去，
  送 `{"argv": [...]}`，收到 ack 后退出 0 —— 全程不需要创建 QApplication；
- 首实例收到后经 `MainWindow.wake_requested` 信号（跨线程自动走 Queued）
  显示窗口，并把非旗标参数填进输入区。

安全边界：只绑回环、token 用 `hmac.compare_digest` 比较、消息限长 64 KiB、
监听线程 daemon 且任何异常都不外泄（唤醒失败只意味着退回旧行为：打印后退出）。
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..util.atomic_io import write_json_atomic
from .single_instance import _pid_alive

__all__ = ["WakeServer", "request_wake", "read_info", "info_path", "url_args"]

_INFO_NAME = "instance.json"
_MAX_MESSAGE = 64 * 1024


def info_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / _INFO_NAME


def url_args(argv: list[Any] | None) -> list[str]:
    """从启动参数里筛出可注入输入区的条目：跳过旗标与空串。"""
    out: list[str] = []
    for raw in argv or []:
        s = str(raw).strip()
        if s and not s.startswith("-"):
            out.append(s)
    return out


def read_info(data_dir: str | Path) -> dict[str, Any] | None:
    """读实例信息；不存在/损坏一律返回 None（绝不上抛）。"""
    try:
        data = json.loads(info_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        port = int(data.get("port") or 0)
        # pid 不做 `or 0`：`{}`/`[]` 这类假值容器不是合法 pid，应判信息损坏；
        # 缺失或 null（写方未填）才视为「未知 pid = 0」。
        raw_pid = data.get("pid")
        pid = 0 if raw_pid is None else int(raw_pid)
    except (TypeError, ValueError):
        return None
    token = str(data.get("token") or "")
    if not (0 < port < 65536) or not token:
        return None
    return {"port": port, "token": token, "pid": pid}


def request_wake(data_dir: str | Path, argv: list[Any] | None,
                 timeout: float = 3.0) -> bool:
    """第二实例：请求首实例显示窗口并转发参数。成功（收到 ack）返回 True。"""
    info = read_info(data_dir)
    if info is None:
        return False
    if info["pid"] > 0 and not _pid_alive(info["pid"]):
        return False  # 残留信息（上次崩溃）—— 不连死端口
    payload = json.dumps({"token": info["token"], "argv": url_args(argv)},
                         ensure_ascii=False).encode("utf-8") + b"\n"
    try:
        with socket.create_connection(("127.0.0.1", info["port"]),
                                      timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(payload)
            reply = b""
            while len(reply) < 4096 and b"\n" not in reply:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                reply += chunk
    except OSError:
        return False
    try:
        doc = json.loads(reply.split(b"\n", 1)[0].decode("utf-8", "replace"))
    except ValueError:
        return False
    return isinstance(doc, dict) and doc.get("ok") is True


class WakeServer:
    """首实例的唤醒监听器。`start()` 后监听线程独立运行，`stop()` 幂等。"""

    def __init__(self, data_dir: str | Path,
                 on_wake: Callable[[dict[str, Any]], None]):
        self._data_dir = Path(data_dir)
        self._on_wake = on_wake
        self._token = secrets.token_hex(16)
        # hmac.compare_digest 的 str 分支只接受 ASCII；对端 token 不可信，
        # 统一按 UTF-8 字节比较（非 ASCII 输入也就不会再抛 TypeError）。
        self._token_bytes = self._token.encode("utf-8")
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def port(self) -> int:
        if self._sock is None:
            return 0
        return int(self._sock.getsockname()[1])

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            sock.listen(4)
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            raise
        self._sock = sock
        info_file = info_path(self._data_dir)
        write_json_atomic(info_file, {
            "port": self.port, "token": self._token, "pid": os.getpid(),
        })
        if os.name != "nt":
            # token 是本机唤醒通道的凭据：POSIX 多用户机器上默认 umask 可能让
            # 同机其他用户读到 → 显式收紧到仅属主可读写。
            with suppress(OSError):
                os.chmod(info_file, 0o600)
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="gcm-wake-server")
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                break  # stop() 已把套接字摘除（先取局部引用，避免竞态）
            try:
                conn, _addr = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                self._handle(conn)
            except Exception:  # noqa: BLE001 —— 任何坏请求都不得终结监听线程
                # MAJOR-1 实锤：非 ASCII token 曾让 hmac.compare_digest 抛 TypeError，
                # 穿透到 while 循环把 daemon 线程杀死、唤醒通道永久失效且无日志。
                # 单连接级吞掉异常，继续 accept；下一连接照常工作。
                continue
            finally:
                with suppress(OSError):  # 对端已断开：关闭失败无补救意义
                    conn.close()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(2.0)
            raw = b""
            while len(raw) < _MAX_MESSAGE and b"\n" not in raw:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
            line = raw.split(b"\n", 1)[0]
            msg = json.loads(line.decode("utf-8", "replace"))
        except (OSError, ValueError):
            return
        if not isinstance(msg, dict):
            return
        token = str(msg.get("token") or "")
        # 字节比较：str 分支对非 ASCII 抛 TypeError（对端可控输入，不能信）
        if not hmac.compare_digest(token.encode("utf-8", "replace"),
                                   self._token_bytes):
            return
        argv = msg.get("argv")
        payload = {"argv": [str(x) for x in argv] if isinstance(argv, list) else []}
        try:
            self._on_wake(payload)
        except Exception:  # noqa: BLE001 —— 回调失败不能拖死监听线程
            return
        with suppress(OSError):  # 对端可能已放弃等待 ack
            conn.sendall(b'{"ok": true}\n')

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            with suppress(OSError):  # 监听套接字关闭失败不影响进程退出
                self._sock.close()
            self._sock = None
        info = read_info(self._data_dir)
        if info is not None and info["token"] == self._token:
            with suppress(OSError):  # 清理失败只留下无害的残留文件
                info_path(self._data_dir).unlink(missing_ok=True)
