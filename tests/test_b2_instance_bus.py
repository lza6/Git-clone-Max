# -*- coding: utf-8 -*-
"""B2 实例唤醒通道测试：url_args 过滤 + 真实 loopback 往返 + 失效信息降级。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.instance_bus import (WakeServer, info_path, read_info,
                                  request_wake, url_args)


class TestUrlArgs(unittest.TestCase):
    def test_filters_flags_and_blanks(self):
        self.assertEqual(
            url_args(["--minimized", "", "  ", "https://github.com/a/b",
                      "--portable", "owner/repo"]),
            ["https://github.com/a/b", "owner/repo"])

    def test_none_and_non_strings(self):
        self.assertEqual(url_args(None), [])
        self.assertEqual(url_args([1, None, " x "]), ["1", "None", "x"])


class TestWakeRoundtrip(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gcm-wake-"))
        self.got: list[dict] = []
        self.server = WakeServer(self.dir, lambda p: self.got.append(p))
        self.server.start()

    def tearDown(self):
        self.server.stop()

    def test_info_written_and_removed(self):
        info = read_info(self.dir)
        self.assertIsNotNone(info)
        self.assertEqual(info["pid"], os.getpid())
        self.assertGreater(info["port"], 0)
        self.server.stop()
        self.assertFalse(info_path(self.dir).exists(), "stop 后应清理 instance.json")

    def test_binds_loopback_only(self):
        self.assertEqual(self.server._sock.getsockname()[0], "127.0.0.1",
                         "唤醒通道必须只绑回环（不得 0.0.0.0）")

    def test_wake_delivers_argv(self):
        ok = request_wake(self.dir, ["--minimized", "https://github.com/a/b"])
        self.assertTrue(ok, "带正确 token 的唤醒应收到 ack")
        deadline = time.time() + 3
        while not self.got and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.got, [{"argv": ["https://github.com/a/b"]}])

    def test_wrong_token_rejected(self):
        info = read_info(self.dir)
        import socket
        payload = json.dumps({"token": "bad", "argv": ["x"]}).encode() + b"\n"
        with socket.create_connection(("127.0.0.1", info["port"]), timeout=3) as s:
            s.sendall(payload)
            s.settimeout(1.0)
            try:
                reply = s.recv(64)
            except OSError:
                reply = b""
        self.assertEqual(reply, b"", "错误 token 不应收到 ack")
        time.sleep(0.1)
        self.assertEqual(self.got, [], "错误 token 不应触发回调")

    def test_malformed_requests_do_not_kill_server(self):
        """MAJOR-1 回归：畸形请求（含非 ASCII token）后，合法唤醒仍须成功。"""
        info = read_info(self.dir)
        import socket

        def _send(raw: bytes) -> bytes:
            with socket.create_connection(("127.0.0.1", info["port"]),
                                          timeout=3) as s:
                try:
                    s.sendall(raw)
                except OSError:  # 服务端读满 64 KiB 后先关连接：发送侧被重置
                    return b""
                s.settimeout(1.0)
                try:
                    return s.recv(64)
                except OSError:
                    return b""

        # 非 ASCII token（曾让 hmac.compare_digest 抛 TypeError 杀死监听线程）
        self.assertEqual(_send(json.dumps(
            {"token": "中", "argv": ["x"]}).encode() + b"\n"), b"")
        # 非 JSON / 超长无换行（>64 KiB 上限）/ 非 dict / token 类型不对
        self.assertEqual(_send(b"not json\n"), b"")
        self.assertEqual(_send(b"a" * (70 * 1024)), b"")
        self.assertEqual(_send(b"[1,2]\n"), b"")
        self.assertEqual(_send(b'{"token": 123}\n'), b"")
        time.sleep(0.2)
        self.assertEqual(self.got, [])
        # 关键断言：服务仍存活，合法唤醒照常工作
        self.assertTrue(request_wake(self.dir, ["ok"]),
                        "坏请求之后服务必须仍可用（线程不得被杀死）")
        deadline = time.time() + 3
        while not self.got and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.got, [{"argv": ["ok"]}])


class TestWakeDegradation(unittest.TestCase):
    def test_no_info_returns_false(self):
        d = Path(tempfile.mkdtemp(prefix="gcm-wake-none-"))
        self.assertFalse(request_wake(d, ["x"]))

    def test_stale_pid_returns_false(self):
        d = Path(tempfile.mkdtemp(prefix="gcm-wake-stale-"))
        write = {"port": 9, "token": "t", "pid": 99999999}
        info_path(d).write_text(json.dumps(write), encoding="utf-8")
        self.assertFalse(request_wake(d, ["x"]), "PID 已死的残留信息不应尝试连接")

    def test_dead_port_returns_false(self):
        d = Path(tempfile.mkdtemp(prefix="gcm-wake-dead-"))
        # 绑一个端口再释放：得到「当前无人监听」的确定性端口
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        info_path(d).write_text(
            json.dumps({"port": port, "token": "t", "pid": os.getpid()}),
            encoding="utf-8")
        self.assertFalse(request_wake(d, ["x"], timeout=0.5))

    def test_corrupt_info_returns_none(self):
        d = Path(tempfile.mkdtemp(prefix="gcm-wake-bad-"))
        info_path(d).write_text("not json", encoding="utf-8")
        self.assertIsNone(read_info(d))

    def test_missing_pid_means_unknown_not_damaged(self):
        """pid 缺失/null = 未知（0），与「容器值」的损坏语义区分开。"""
        d = Path(tempfile.mkdtemp(prefix="gcm-wake-nopid-"))
        info_path(d).write_text('{"port": 4321, "token": "t"}',
                                encoding="utf-8")
        self.assertEqual(read_info(d)["pid"], 0)
        info_path(d).write_text('{"port": 4321, "token": "t", "pid": null}',
                                encoding="utf-8")
        self.assertEqual(read_info(d)["pid"], 0)

    def test_corrupt_pid_is_damaged_not_exception(self):
        """MAJOR-2 回归：pid 类型损坏必须按「信息损坏」返回 None，绝不上抛。"""
        d = Path(tempfile.mkdtemp(prefix="gcm-wake-pid-"))
        for bad in ('"abc"', "{}", "[]", '"1.5"'):
            info_path(d).write_text(
                '{"port": 1234, "token": "t", "pid": ' + bad + "}",
                encoding="utf-8")
            self.assertIsNone(read_info(d), f"pid={bad} 应判损坏")
            self.assertFalse(request_wake(d, ["x"], timeout=0.3))
        # stop() 在损坏信息上也不得抛（aboutToQuit 槽抛异常会留下坏文件循环）
        server = WakeServer(d, lambda p: None)
        server.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
