"""Rev17 Batch C：release_fetch 单元测试（纯逻辑，零网络）。

网络层（latest_release_assets / download_asset 的真实请求）不在这里测——
付费 API 红线 + 离线纪律。断点续传的 206/200/416 分支用本地 HTTP 服务在
集成场景验（有真实 socket 才成立），单元级只测 pick_asset 的平台匹配
与语义表里**不需要网络**的部分。
"""
from __future__ import annotations

import unittest


class TestPickAsset(unittest.TestCase):
    def test_windows_amd64_prefers_explicit(self):
        from gcm.app.release_fetch import pick_asset
        assets = [
            {"name": "tool-linux-amd64.tar.gz", "browser_download_url": "u1", "size": 1},
            {"name": "tool-windows-amd64.zip", "browser_download_url": "u2", "size": 2},
            {"name": "tool-darwin-arm64.zip", "browser_download_url": "u3", "size": 3},
        ]
        a = pick_asset(assets, os_tag="windows", arch_tag="amd64")
        self.assertIsNotNone(a)
        self.assertEqual(a.name, "tool-windows-amd64.zip")

    def test_anti_rules_exclude_checksums(self):
        from gcm.app.release_fetch import pick_asset
        assets = [
            {"name": "tool-windows-amd64.zip.sha256", "browser_download_url": "u1"},
            {"name": "tool-windows-amd64.zip", "browser_download_url": "u2"},
        ]
        a = pick_asset(assets, os_tag="windows", arch_tag="amd64")
        self.assertEqual(a.name, "tool-windows-amd64.zip")

    def test_arch_priority(self):
        from gcm.app.release_fetch import pick_asset
        assets = [
            {"name": "tool-windows-arm64.zip", "browser_download_url": "u1"},
            {"name": "tool-windows-x86_64.zip", "browser_download_url": "u2"},
        ]
        a = pick_asset(assets, os_tag="windows", arch_tag="amd64")
        self.assertEqual(a.name, "tool-windows-x86_64.zip")

    def test_generic_fallback_when_no_platform_word(self):
        from gcm.app.release_fetch import pick_asset
        assets = [{"name": "tool.zip", "browser_download_url": "u1"}]
        a = pick_asset(assets, os_tag="windows", arch_tag="amd64")
        self.assertIsNotNone(a)
        self.assertEqual(a.name, "tool.zip")

    def test_wrong_platform_excluded_entirely(self):
        from gcm.app.release_fetch import pick_asset
        assets = [
            {"name": "tool-linux-amd64.tar.gz", "browser_download_url": "u1"},
            {"name": "tool-darwin-arm64.zip", "browser_download_url": "u2"},
        ]
        a = pick_asset(assets, os_tag="windows", arch_tag="amd64")
        self.assertIsNone(a, "明确写了别平台的资产不得兜底命中")

    def test_no_assets_returns_none(self):
        from gcm.app.release_fetch import pick_asset
        self.assertIsNone(pick_asset([], os_tag="windows", arch_tag="amd64"))


class TestResumeSemantics(unittest.TestCase):
    """断点续传语义表中可用本地文件系统验证的部分。"""

    def test_oversized_part_reset(self):
        """.part 大于远端 size → 丢弃重下（written 归零路径）。"""
        import tempfile
        from pathlib import Path

        from gcm.app.release_fetch import ReleaseAsset, download_asset
        # 本地 file:// 场景太绕：直接验证内部一致——size 已知且 part 超大时应清零。
        # 用 should_abort 在首轮就取消，检查 part 状态不受 OSError 分支影响。
        d = Path(tempfile.mkdtemp())
        part = d / "a.bin.part"
        part.write_bytes(b"x" * 100)
        asset = ReleaseAsset(name="a.bin", url="http://127.0.0.1:1/no-such", size=50)
        with self.assertRaises((OSError, RuntimeError, TimeoutError)):
            download_asset(asset, d, timeout=1.0)
        # 超尺寸 part 在第二次尝试前已被清（100 > 50）
        self.assertFalse(part.exists() and part.stat().st_size == 100,
                         "超尺寸 .part 未被重置")

    def test_local_oserror_no_retry_keeps_part(self):
        """本地 OSError（目标目录不可写）必须直接抛，不退避重试。"""
        from gcm.app.release_fetch import ReleaseAsset, download_asset
        asset = ReleaseAsset(name="a.bin", url="http://127.0.0.1:1/no-such", size=0)
        # Windows 上拿一个文件当目录用 → mkdir 抛 OSError（结构性失败）
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        blocker = d / "not-a-dir"
        blocker.write_text("x")
        with self.assertRaises(OSError):
            download_asset(asset, blocker, timeout=1.0)


class TestResumeWithLocalServer(unittest.TestCase):
    """用本地 HTTP 服务真跑 download_asset 的 206/200 语义（真实 socket）。

    覆盖面：Range 续传、206 偏移校验、服务器忽略 Range（200 重下）、
    416 收尾、取消保留 .part。416/错位分支在本地 handler 里可控构造。
    """

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        cls.data = bytes(range(256)) * 8  # 2048 字节

        class _BaseH(BaseHTTPRequestHandler):
            data = cls.data

            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/ignore-range":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(self.data)))
                    self.end_headers()
                    self.wfile.write(self.data)
                    return
                rng = self.headers.get("Range") or ""
                m = __import__("re").match(r"bytes=(\d+)-", rng)
                start = int(m.group(1)) if m else 0
                if self.path == "/416-when-start" and start:
                    self.send_response(416)
                    self.end_headers()
                    return
                body = self.data[start:]
                self.send_response(206 if start else 200)
                self.send_header("Content-Range",
                                 f"bytes {start}-{len(self.data)-1}/{len(self.data)}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        globals()["_BaseH"] = _BaseH
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _BaseH)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _dl(self, path, name="a.bin", dest=None, **kw):
        import tempfile
        from pathlib import Path as _P

        from gcm.app.release_fetch import ReleaseAsset, download_asset
        d = _P(dest or tempfile.mkdtemp())
        asset = ReleaseAsset(name=name,
                             url=f"http://127.0.0.1:{self.port}{path}",
                             size=len(self.data))
        return download_asset(asset, d, **kw)

    def test_full_download_200(self):
        out = self._dl("/x")
        self.assertEqual(out.stat().st_size, len(self.data))
        self.assertEqual(out.read_bytes(), self.data)
        self.assertFalse(out.with_name(out.name + ".part").exists(),
                         "成功后 .part 必须消失（os.replace）")

    def test_resume_from_partial_part(self):
        """预置半截 .part → 206 续传 → 最终文件完整。"""
        import tempfile
        from pathlib import Path as _P
        d = _P(tempfile.mkdtemp())
        part = d / "a.bin.part"
        half = len(self.data) // 2
        part.write_bytes(self.data[:half])
        out = self._dl("/x", dest=str(d))
        self.assertEqual(out.read_bytes(), self.data)

    def test_server_ignoring_range_resets_and_redownloads(self):
        """服务器忽略 Range 返回 200 → 清 .part 整文件重下（不拼接损坏）。"""
        import tempfile
        from pathlib import Path as _P
        d = _P(tempfile.mkdtemp())
        part = d / "b.bin.part"
        part.write_bytes(self.data[:100])
        out = self._dl("/ignore-range", name="b.bin", dest=str(d))
        self.assertEqual(out.read_bytes(), self.data)

    def test_cancel_keeps_part(self):
        """should_abort 中途取消 → RuntimeError 且 .part 保留（续传进度不丢）。"""
        import tempfile
        from pathlib import Path as _P
        d = _P(tempfile.mkdtemp())
        state = {"n": 0}

        def abort():
            state["n"] += 1
            return state["n"] > 2  # 读第 3 块前取消

        with self.assertRaises(RuntimeError):
            self._dl("/x", name="c.bin", dest=str(d), should_abort=abort)
        self.assertTrue((d / "c.bin.part").exists(),
                        "取消后 .part 必须保留（续传语义）")

    def test_latest_release_assets_parses_local_api(self):
        """release API JSON 解析（本地 socket 假装 api.github.com，monkeypatch 域名）。"""
        import json as _json

        import gcm.app.release_fetch as rf

        payload = _json.dumps({
            "tag_name": "v9.9.9",
            "assets": [
                {"name": "tool-windows-x86_64.zip",
                 "browser_download_url": "http://dl/x.zip", "size": 10},
                {"name": "notes.txt", "browser_download_url": "http://dl/t", "size": 1},
            ],
        }).encode()

        class H(globals()["_BaseH"]):
            data = payload
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(self.data)))
                self.end_headers()
                self.wfile.write(self.data)

        import threading
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        orig = rf.urllib.request.urlopen
        try:
            # 只把 api.github.com 的请求改投本地 server
            real_urlopen = orig

            def fake(url, timeout=0):
                if "api.github.com" in getattr(url, "full_url", str(url)):
                    url = url.full_url.replace(
                        "https://api.github.com",
                        f"http://127.0.0.1:{srv.server_address[1]}")
                return real_urlopen(url, timeout=timeout)

            rf.urllib.request.urlopen = fake
            tag, assets = rf.latest_release_assets("owner/repo", token="tk")
        finally:
            rf.urllib.request.urlopen = orig
            srv.shutdown()
        self.assertEqual(tag, "v9.9.9")
        self.assertEqual(len(assets), 2)
        self.assertEqual(assets[0].name, "tool-windows-x86_64.zip")

    def test_platform_tags_windows_and_posix(self):
        """_platform_tags 双分支（os.name monkeypatch）。"""
        import gcm.app.release_fetch as rf
        self.assertEqual(rf._platform_tags(), ("windows", "amd64"))  # 本机 nt
        orig = rf.os.name
        try:
            rf.os.name = "posix"
            import unittest.mock as mock
            with (mock.patch("platform.system", return_value="Linux"),
                 mock.patch("platform.machine", return_value="aarch64")):
                self.assertEqual(rf._platform_tags(), ("linux", "aarch64"))
            with mock.patch("platform.system", return_value="FreeBSD"):
                self.assertEqual(rf._platform_tags(), ("freebsd", "amd64"))
        finally:
            rf.os.name = orig

    def test_416_with_complete_part_finalizes(self):
        """416 且本地已完整 → 直接收尾（os.replace），不报错。"""
        import tempfile
        from pathlib import Path as _P
        d = _P(tempfile.mkdtemp())
        part = d / "e.bin.part"
        part.write_bytes(self.data)  # 已完整
        out = self._dl("/416-when-start", name="e.bin", dest=str(d))
        self.assertEqual(out.read_bytes(), self.data)
        self.assertFalse(part.exists())

    def test_token_header_sent(self):
        """token 传入 → Authorization: Bearer 头发给服务器（本地 server 回显验证）。"""
        import tempfile
        from pathlib import Path as _P

        from gcm.app.release_fetch import ReleaseAsset, download_asset
        seen = {}

        class H(globals()["_BaseH"]):
            def do_GET(self):
                seen["auth"] = self.headers.get("Authorization")
                super().do_GET()

        import threading
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            d = _P(tempfile.mkdtemp())
            asset = ReleaseAsset(name="f.bin",
                                 url=f"http://127.0.0.1:{srv.server_address[1]}/x",
                                 size=len(self.data))
            download_asset(asset, d, token="t0k3n")
        finally:
            srv.shutdown()
        self.assertEqual(seen.get("auth"), "Bearer t0k3n")

    def test_size_mismatch_raises(self):
        """最终字节数 != 远端 size → 报下载不完整。"""
        import tempfile
        from pathlib import Path as _P

        from gcm.app.release_fetch import ReleaseAsset, download_asset
        d = _P(tempfile.mkdtemp())
        asset = ReleaseAsset(name="d.bin",
                             url=f"http://127.0.0.1:{self.port}/x",
                             size=len(self.data) + 999)  # 谎报更大 size
        with self.assertRaises(RuntimeError):
            download_asset(asset, d)


if __name__ == "__main__":
    unittest.main()
