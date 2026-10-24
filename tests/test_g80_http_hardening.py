"""G80-B4-3 HTTP 硬化守卫：体积上限 / 参数白名单 / 搜索下推。

三条都是「调用方能踩到、而且踩到时很难自查」的：
1. 请求体无上限 → 一个 `Content-Length: 2GB` 就能把服务内存吃光；
2. `format` 非法时**静默回落 JSON** → 调用方以为拿到 csv；
3. 搜索取**全表**回 Python 过滤 → 仓库上千时每次搜索搬全表，且 % / _ 未转义时
   用户输入的 `%` 会被当通配符（"搜 a% 结果全中"）。
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_db(tmp: Path):
    from gcm.db.repo_db import Database
    from gcm.models import RepoSpec

    db = Database(tmp / "repos.db")
    for owner, repo, host in (("acme", "rocket", "github.com"),
                              ("acme", "50%off", "github.com"),
                              ("bob", "lib", "gitlab.com")):
        db.upsert_repo(
            RepoSpec(owner=owner, repo=repo,
                     url_https=f"https://{host}/{owner}/{repo}.git",
                     folder_name=f"{owner}__{repo}"),
            local_path=str(tmp / f"{owner}__{repo}"), host=host)
    db.close()


def _serve(tmp: Path, allow_mutate: bool = True):
    from gcm.http_api import create_server

    httpd = create_server("127.0.0.1:0", root=tmp, data_dir=tmp, allow_mutate=allow_mutate)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    return httpd, port, th


def _get(port: int, path: str):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


class TestHttpHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_hard_"))
        _make_db(self.tmp)
        self.httpd, self.port, self.th = _serve(self.tmp)
        self.addCleanup(self.th.join, 5.0)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    # ---------------------------------------------------------------- 搜索下推
    def test_search_returns_hits(self):
        code, body = _get(self.port, "/api/repos/search?q=rocket")
        self.assertEqual(code, 200)
        self.assertIn("rocket", body)
        self.assertNotIn("lib", body.replace("rocket", ""))

    def test_search_escapes_percent_wildcard(self):
        """`%` 必须被转义：搜 `%` 只能命中名字里真含 `%` 的那条。"""
        code, body = _get(self.port, "/api/repos/search?q=%25")
        self.assertEqual(code, 200)
        self.assertIn("50%off", body)
        self.assertNotIn("rocket", body, "% 被当通配符 → 全表命中")

    def test_search_host_filter_via_db(self):
        from gcm.db.repo_db import Database

        db = Database(self.tmp / "repos.db", readonly=True)
        try:
            hits = db.search_repos("acme", host="gitlab.com")
            self.assertEqual(hits, [], "host 过滤应排除 github 上的 acme")
        finally:
            db.close()

    def test_search_respects_limit(self):
        from gcm.db.repo_db import Database

        db = Database(self.tmp / "repos.db", readonly=True)
        try:
            self.assertEqual(len(db.search_repos("acme", limit=1)), 1)
        finally:
            db.close()

    # ---------------------------------------------------------------- format 白名单
    def test_report_invalid_format_is_400(self):
        code, body = _get(self.port, "/api/report?format=xlsx")
        self.assertEqual(code, 400, f"非法 format 必须 400，实际 {code}: {body}")
        self.assertIn("json/csv/markdown", body)

    def test_report_valid_formats_still_work(self):
        for fmt in ("json", "csv", "markdown"):
            code, _body = _get(self.port, f"/api/report?format={fmt}")
            self.assertEqual(code, 200, f"合法 format {fmt} 被拒")

    # ---------------------------------------------------------------- 体积上限
    def test_oversized_body_is_413_and_not_read(self):
        payload = ("x" * 1024 + "\n") * 1100  # ~1.1 MiB > 1 MiB 上限
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/clone",
            data=payload.encode("utf-8"), method="POST",
            headers={"Content-Type": "text/plain"})
        try:
            urllib.request.urlopen(req, timeout=15)
            self.fail("超大请求体竟然被接受")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 413, f"应返回 413，实际 {e.code}")
            body = e.read().decode("utf-8", errors="replace")
            self.assertIn("请求体过大", body)

    def test_normal_sized_body_still_accepted_by_gate(self):
        """小请求体不应被 413 拦掉（走正常流程，空内容时给 2 而不是 413）。"""
        import json as _json
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/clone",
            data=b"", method="POST", headers={"Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self.assertNotEqual(e.code, 413, "空请求体不该被体积上限拦")


class TestMcpSearchReusesSameImplementation(unittest.TestCase):
    def test_mcp_search_uses_db_method(self):
        """MCP 的 search_repos 必须复用 db.search_repos（不许留第二份内存过滤）。"""
        import inspect

        from gcm import mcp_server

        src = inspect.getsource(mcp_server._search_repos)
        self.assertIn("db.search_repos(", src)
        self.assertNotIn("list_repos(", src, "MCP 搜索不得再退化成全表过滤")


if __name__ == "__main__":
    unittest.main()
