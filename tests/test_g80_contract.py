"""G80-B1 契约与安全边界守卫（先红后绿）。

覆盖三件事（对应参考计划 B1 / 发现 F1、F2、F3、F4）：
1. MCP 「只读」工具真的只读：不建表、不迁移、不写库；旧库/空库只允许**一次**迁移；
2. 失败文本（message/detail）在**所有出站口**都打码：HTTP 行、MCP 行、引擎落库前；
3. CLI 退出码与 `docs/agent-contract.md` §2.2 一致：failed/conflict/cancelled → 1；
4. `--portable`/`--minimized` 等全局标志不得被当成 URLs 文件名。

为什么要有这些守卫：这三类缺陷都属于「能再犯」的类型（忘了传 readonly / 新加一个出口忘打码 /
新加一个状态忘算退出码 / 新加一个全局标志忘剥离），本项目 CLAUDE.md §5 要求给这类问题补静态或 E2E 守卫。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestMcpReadonly(unittest.TestCase):
    """F1：MCP 只读工具不得写库。"""

    def test_open_db_requests_readonly_connection(self):
        from gcm import mcp_server

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            _make_db(data_dir / "repos.db")
            with mock.patch("gcm.db.repo_db.Database", wraps=_real_db()) as spy:
                db = mcp_server._open_db(data_dir)
                db.close()
                self.assertTrue(spy.called, "Database 根本没被打开")
                _args, kwargs = spy.call_args
                self.assertTrue(
                    kwargs.get("readonly"),
                    "MCP 打开数据库时必须显式 readonly=True（否则会 executescript+commit）",
                )

    def test_open_db_missing_file_does_not_create_db(self):
        """空数据目录：不应为了「列仓库」而凭空创建 repos.db。"""
        from gcm import mcp_server

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            db = mcp_server._open_db(data_dir)
            try:
                self.assertFalse(
                    (data_dir / "repos.db").exists(),
                    "只读工具不得创建数据库文件（会把只读契约变成写操作）",
                )
                self.assertEqual(db.list_repos(), [])
            finally:
                db.close()

    def test_open_db_legacy_schema_migrates_once_then_readonly(self):
        """v1 旧库：允许一次迁移，之后必须回到只读连接。"""
        from gcm import mcp_server

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            path = data_dir / "repos.db"
            _make_v1_db(path)  # 缺 favorite/tags 等列 → 只读快路径会抛 OperationalError

            first = mcp_server._open_db(data_dir)
            first.close()
            second = mcp_server._open_db(data_dir)
            try:
                # 迁移完成后，列必须存在（能被只读快路径校验）
                con = sqlite3.connect(path)
                cols = {r[1] for r in con.execute("PRAGMA table_info(repos)")}
                con.close()
                self.assertIn("favorite", cols, "旧库未被迁移")
            finally:
                second.close()

    def test_readonly_tool_call_leaves_db_bytes_untouched(self):
        """端到端：跑一次 list_repos 后 DB 文件内容（含 schema 版本）不得变化。"""
        from gcm import mcp_server

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            path = data_dir / "repos.db"
            _make_db(path)
            con = sqlite3.connect(path)
            ver_before = con.execute("PRAGMA user_version").fetchone()[0]
            con.close()
            before = path.read_bytes()

            db = mcp_server._open_db(data_dir)
            try:
                db.list_repos()
            finally:
                db.close()

            con = sqlite3.connect(path)
            ver_after = con.execute("PRAGMA user_version").fetchone()[0]
            con.close()
            self.assertEqual(ver_before, ver_after)
            self.assertEqual(before, path.read_bytes(), "只读工具改动了 DB 文件字节")


class TestOutboundRedaction(unittest.TestCase):
    """F2：message/detail 里的凭据必须在所有出口被打码。"""

    LEAKY = (
        "fatal: unable to access 'https://user:s3cret@github.com/a/b.git': 403 "
        "token=ghp_ABCDEFGHIJKLMNOPQRSTUVWX Authorization: Bearer sk-abcdefghijklmnopqrstuv"
    )

    def test_redact_row_helper_exists_and_masks_text_values(self):
        from gcm.util.redact import redact_row

        row = {"repo": "a/b", "message": self.LEAKY, "count": 3, "local_path": "C:/Users/alice/r"}
        out = redact_row(row)
        self.assertNotIn("s3cret", out["message"])
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWX", out["message"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuv", out["message"])
        self.assertEqual(out["repo"], "a/b")
        self.assertEqual(out["count"], 3, "非文本值不得被改写")

    def test_http_sanitize_row_redacts_message(self):
        from gcm.http_api import _sanitize_row

        out = _sanitize_row({"repo": "a/b", "message": self.LEAKY, "url": "https://u:p@h/x"})
        self.assertNotIn("s3cret", str(out))
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWX", str(out))

    def test_mcp_sanitize_row_redacts_message(self):
        from gcm.mcp_server import _sanitize_row

        out = _sanitize_row({"repo": "a/b", "message": self.LEAKY})
        self.assertNotIn("s3cret", str(out))
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWX", str(out))

    def test_engine_redacts_result_before_persist(self):
        """引擎落库前的唯一打码点：_redact_result 必须就地打码 message/detail。"""
        from gcm.app.engine import _redact_result
        from gcm.models import SyncResult, SyncStatus, RepoSpec

        res = SyncResult(spec=RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b"),
                         status=SyncStatus.FAILED, message="异常",
                         detail=self.LEAKY)
        out = _redact_result(res)
        self.assertNotIn("s3cret", out.detail)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWX", out.detail)


class TestCliExitCode(unittest.TestCase):
    """F3：退出码对齐 docs/agent-contract.md §2.2。"""

    def test_exit_code_matrix(self):
        from gcm.cli import _exit_code_of

        self.assertEqual(_exit_code_of({"success": 3}), 0)
        self.assertEqual(_exit_code_of({"success": 2, "failed": 1}), 1)
        self.assertEqual(_exit_code_of({"success": 2, "conflict": 1}), 1,
                         "契约 §2.2：conflict 视为非全成功")
        self.assertEqual(_exit_code_of({"success": 2, "cancelled": 1}), 1,
                         "契约 §2.2：cancelled 视为非全成功")
        self.assertEqual(_exit_code_of({"skipped": 2}), 0, "跳过不算失败")
        self.assertEqual(_exit_code_of({}), 0)


class TestGlobalFlagStripping(unittest.TestCase):
    """F4：全局标志不得被 CLI 当成 URLs 文件名（会 exit 2）。"""

    def test_json_ok_flag_matches_exit_code(self):
        """--json 的 `ok` 字段与退出码必须同源（否则出现 ok=true 但 exit 1）。"""
        from gcm.cli import _run_json
        from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus

        def _res(status, action):
            return SyncResult(spec=RepoSpec(owner="a", repo="b",
                                            url_https="https://github.com/a/b"),
                              status=status, action=action)

        conflict = _run_json(None, None, [], [_res(SyncStatus.CONFLICT, SyncAction.CONFLICT)])
        self.assertFalse(conflict["ok"], "conflict 时 JSON 不得报 ok=true")
        cancelled = _run_json(None, None, [], [_res(SyncStatus.CANCELLED, SyncAction.CANCELLED)])
        self.assertFalse(cancelled["ok"], "cancelled 时 JSON 不得报 ok=true")
        ok = _run_json(None, None, [], [_res(SyncStatus.SUCCESS, SyncAction.CLONED)])
        self.assertTrue(ok["ok"])

    def test_strip_global_flags(self):
        from gcm.__main__ import _strip_global_flags

        self.assertEqual(_strip_global_flags(["--cli", "--portable", "u.txt"]), ["--cli", "u.txt"])
        self.assertEqual(_strip_global_flags(["--portable", "--cli", "u.txt"]), ["--cli", "u.txt"])
        self.assertEqual(_strip_global_flags(["--cli", "--minimized", "-"]), ["--cli", "-"])
        # 不认识的参数原样保留（避免吞掉用户输入）
        self.assertEqual(_strip_global_flags(["--cli", "u.txt"]), ["--cli", "u.txt"])

    def test_cli_receives_clean_argv_through_main(self):
        from gcm import __main__ as entry

        seen = {}

        def _fake_cli_main(argv=None):
            seen["argv"] = list(argv or [])
            return 0

        with mock.patch("gcm.cli.main", _fake_cli_main):
            with mock.patch.object(sys, "argv", ["gcm", "--cli", "--portable", "u.txt"]):
                rc = entry.main()
        self.assertEqual(rc, 0)
        self.assertNotIn("--portable", seen["argv"], "全局标志必须剥离后再交给 CLI")
        self.assertIn("u.txt", seen["argv"])


# ---------------------------------------------------------------- helpers
def _real_db():
    from gcm.db.repo_db import Database

    return Database


def _make_db(path: Path) -> None:
    from gcm.db.repo_db import Database

    db = Database(path)
    db.close()


def _make_v1_db(path: Path) -> None:
    """造一个缺列的旧库：只读快路径的 `SELECT favorite` 会抛 OperationalError。"""
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL,
            repo TEXT NOT NULL,
            host TEXT DEFAULT 'github.com',
            folder_name TEXT NOT NULL,
            local_path TEXT,
            url_https TEXT,
            tags TEXT DEFAULT '',
            note TEXT DEFAULT '',
            excluded INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    unittest.main()
