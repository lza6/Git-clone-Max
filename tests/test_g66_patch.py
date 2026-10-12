"""G66 补丁回归：契约版本单源 + 子进程 UTF-8 解码 + 时间炸弹守卫。

本文件对应 2026-10-12 终局审计发现的三个真实缺陷（详见 计划书/验收清单.md G66 节）：

- G66-2 **契约版本硬编码**：CLI `--json` / `--diag` 与 MCP `serverInfo` 的
  `version` 字段写死 `"8.0.2"`，`gcm.__version__` 已到 9.1.2 → 所有 agent 消费者
  读到的版本落后 4 个大版本；`tests/test_g56_cli_json.py` 还把错误值断言成正确值。
- G66-3 **时间炸弹用例**：`tests/test_repo_tags.py` 写死 `2026-09-01` 并假设
  "距今 8 天"，时间一过必然变红（实测 2026-10-12 已红 → 主矩阵门禁失效）。
- G66-4 **中文 Windows 子进程解码崩溃**：`subprocess.run(text=True)` 未指定
  `encoding`，Python 用系统 locale（中文 Windows = GBK）解码 git 的 UTF-8 输出
  （中文文件名/路径极常见）→ `UnicodeDecodeError` 在读取线程抛出 → 明明克隆成功
  却归类为失败。仅 scanner.py 与 service.py 一处显式指定了 utf-8。

守卫方式：静态 AST 扫描（新增代码若重新引入硬编码版本/裸 text=True 即红灯）。
"""
from __future__ import annotations

import ast
import datetime
import os
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 契约版本生产者：这些文件里的 version 必须来自 gcm.__version__，不得出现字面量。
CONTRACT_PRODUCERS = [
    ROOT / "gcm" / "cli.py",
    ROOT / "gcm" / "mcp_server.py",
    ROOT / "skills" / "gcm-report" / "scripts" / "export-report.py",
    ROOT / "skills" / "gcm-archive" / "scripts" / "archive-list.py",
]

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _string_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
    return out


class TestContractVersionSingleSource(unittest.TestCase):
    """G66-2：契约 version 只能来自 gcm.__version__。"""

    def test_version_is_semver(self):
        from gcm import __version__
        self.assertRegex(__version__, _SEMVER)

    def test_cli_json_version_matches_package(self):
        from gcm import __version__
        from gcm.cli import _run_json
        doc = _run_json(None, None, [], [])
        self.assertEqual(doc["version"], __version__)

    def test_mcp_serverinfo_version_matches_package(self):
        from gcm import __version__
        from gcm.mcp_server import CONTRACT_VERSION
        self.assertEqual(CONTRACT_VERSION, __version__)

    def test_skill_scripts_use_package_version(self):
        from gcm import __version__
        for path in CONTRACT_PRODUCERS[2:]:
            with self.subTest(script=path.name):
                src = path.read_text(encoding="utf-8")
                self.assertIn("CONTRACT_VERSION", src,
                              f"{path.name} 应通过 CONTRACT_VERSION 取版本")
                for lineno, value in _string_literals(path):
                    self.assertFalse(
                        _SEMVER.match(value),
                        f"{path}:{lineno} 出现硬编码版本字面量 {value!r}，"
                        f"应改用 gcm.__version__（当前 {__version__}）")

    def test_no_hardcoded_semver_in_contract_producers(self):
        from gcm import __version__
        for path in CONTRACT_PRODUCERS:
            with self.subTest(file=path.name):
                for lineno, value in _string_literals(path):
                    if _SEMVER.match(value):
                        self.fail(
                            f"{path}:{lineno} 硬编码版本 {value!r}"
                            f"（当前 __version__={__version__}）")


class TestSubprocessTextDecoding(unittest.TestCase):
    """G66-4：所有文本模式子进程调用必须显式 UTF-8 解码。"""

    def _scan(self, roots: tuple[str, ...]) -> list[str]:
        bad: list[str] = []
        for root in roots:
            for path in sorted((ROOT / root).rglob("*.py")):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except SyntaxError:  # 语法错误由 ruff/编译期拦，不在此重复
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    name = getattr(func, "attr", None) or getattr(func, "id", None)
                    if name not in ("run", "Popen", "check_output", "call",
                                    "check_call"):
                        continue
                    kwargs = {kw.arg for kw in node.keywords}
                    text_modes = {"text", "universal_newlines"} & kwargs
                    if text_modes and "encoding" not in kwargs:
                        bad.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        return bad

    def test_gcm_and_scripts_text_calls_use_utf8(self):
        bad = self._scan(("gcm", "scripts"))
        self.assertEqual(bad, [], "以下调用在中文 Windows(GBK) 下会解码崩溃，"
                                  "请补 encoding='utf-8', errors='replace'：\n  "
                                  + "\n  ".join(bad))

    def test_tests_dir_text_calls_use_utf8(self):
        """测试自身的 subprocess 也必须显式解码，否则夹具线程崩溃导致随机红灯。"""
        bad = self._scan(("tests",))
        self.assertEqual(bad, [], "以下测试调用在中文 Windows(GBK) 下会解码崩溃：\n  "
                                  + "\n  ".join(bad))


class TestHttpApiBindHardening(unittest.TestCase):
    """G66-6：本地 HTTP API 无鉴权 → 必须拒绝非回环绑定。

    加固前：``--serve 0.0.0.0:8765`` 会静默监听全网卡，同网段任何人可直接
    ``GET /api/repos`` 拉走仓库清单、``GET /api/report?format=csv`` 下载报表
    （``--allow-mutate`` 下还能远程触发克隆写盘）。
    """

    def test_loopback_binds_accepted(self):
        from gcm.http_api import create_server
        for bind in ("127.0.0.1:0", "localhost:0", "::1:0"):
            with self.subTest(bind=bind):
                try:
                    httpd = create_server(bind, root=ROOT)
                except OSError:  # 环境不支持 ::1 等，跳过（非断言失败）
                    continue
                self.addCleanup(httpd.server_close)
                self.assertTrue(httpd.allow_mutate is False)

    def test_non_loopback_bind_rejected(self):
        from gcm.http_api import create_server
        for bind in ("0.0.0.0:8765", "192.168.1.10:8765", "example.com:8765",
                     "[::]:8765"):
            with self.subTest(bind=bind):
                with self.assertRaises(ValueError) as ctx:
                    create_server(bind, root=ROOT)
                self.assertIn("非回环", str(ctx.exception))

    def test_illegal_port_rejected(self):
        from gcm.http_api import create_server
        for bind in ("127.0.0.1:70000", "127.0.0.1:-1", "127.0.0.1:abc"):
            with self.subTest(bind=bind), self.assertRaises(ValueError):
                create_server(bind, root=ROOT)
    def test_serve_returns_2_on_refused_bind(self):
        from gcm.http_api import serve
        rc = serve("0.0.0.0:8765", root=ROOT)
        self.assertEqual(rc, 2)


class TestCliEntrypointContract(unittest.TestCase):
    """G66-8：CLI 入口必须可用且不落进 GUI 分支。

    修复前实测：``python -m gcm --help`` **静默走 GUI 分支**（__main__ 的
    ``flags["cli"]`` 为假）→ 创建 QApplication 并阻塞 43 分钟，agent/脚本
    想查用法反而挂死；``python -m gcm --cli --serve 127.0.0.1:8765``
    100% 崩溃（``--serve`` 未从 args 摘除，被自己当成 bind）。
    """

    def test_help_returns_0_and_prints_usage_without_gui(self):
        import io
        from contextlib import redirect_stdout

        from gcm.cli import main as cli_main
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["--cli", "--help"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        for token in ("--cli", "--json", "--dir", "--diag", "--serve",
                      "--allow-mutate", "退出码"):
            self.assertIn(token, out, f"帮助缺少 {token}")

    def test_main_parse_flags_recognizes_help(self):
        from gcm.__main__ import _parse_flags
        self.assertTrue(_parse_flags(["--help"])["help"])
        self.assertTrue(_parse_flags(["-h"])["help"])
        self.assertTrue(_parse_flags(["--cli", "--help"])["help"])
        self.assertFalse(_parse_flags(["--cli"])["help"])

    def test_serve_consumes_its_bind_argument(self):
        """--serve 必须把 host:port 一起消费掉，否则 bind 会拿到 '--serve'。"""
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from gcm.cli import main as cli_main
        # 缺失值时明确报错并返回 2（而非把 '--serve' 当端口）
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_main(["--cli", "--serve"])
        self.assertEqual(rc, 2)
        self.assertIn("--serve", out.getvalue() + err.getvalue())

    def test_serve_rejects_non_loopback_bind(self):
        """文档原样命令形式必须能走到绑定校验（而不是解析期就炸）。"""
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from gcm.cli import main as cli_main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_main(["--cli", "--serve", "0.0.0.0:8799"])
        self.assertEqual(rc, 2)
        self.assertIn("非回环", out.getvalue() + err.getvalue())


class TestAuthHeaderGlobalFallback(unittest.TestCase):
    """G66-9：设置页写 ``host_auth_types["*"]``，service 必须回退读取它。

    修复前：``_env()`` 只做 ``_hats.get(host)`` → UI 那个下拉永不生效，
    用户选 Basic/PRIVATE-TOKEN 仍发出 Bearer（凭据以非预期形式外发）。
    """

    @staticmethod
    def _extra_headers(host_auth_types: dict) -> list[str]:
        """构造自建 host 场景，取 _env() 写进 GIT_CONFIG_VALUE_* 的认证头值。

        认证头逻辑内联在 `_env()` 中，依赖 `self._current_host`（引擎/worker 在
        同步前设置），故此处显式赋值复现真实调用上下文。
        """
        from gcm.git.service import GitService
        svc = GitService(ROOT / "out", host_tokens={"mygit.example.com": "tok123"},
                         host_auth_types=host_auth_types)
        svc._current_host = "mygit.example.com"
        env = svc._env() or {}
        return [
            v for k, v in env.items()
            if k.startswith("GIT_CONFIG_VALUE_") and isinstance(v, str)
            and ("Authorization" in v or "PRIVATE-TOKEN" in v)
        ]

    def test_global_star_basic_applies(self):
        import base64
        headers = self._extra_headers({"*": "basic"})
        self.assertEqual(len(headers), 1, f"期望 1 个认证头，实际 {headers}")
        expected = base64.b64encode(b"tok123").decode("ascii")
        self.assertEqual(headers[0], f"Authorization: Basic {expected}")

    def test_global_star_private_token_applies(self):
        headers = self._extra_headers({"*": "private_token"})
        self.assertEqual(headers[0], "PRIVATE-TOKEN: tok123")

    def test_host_specific_overrides_star(self):
        headers = self._extra_headers({"*": "basic", "mygit.example.com": "private_token"})
        self.assertEqual(headers[0], "PRIVATE-TOKEN: tok123")

    def test_default_is_bearer(self):
        headers = self._extra_headers({})
        self.assertEqual(headers[0], "Authorization: Bearer tok123")


class TestShallowCloneDepth(unittest.TestCase):
    """G66-10：--depth 必须独立生效，不能被 single_branch 门控。

    修复前实测：``depth=1, single_branch=False`` → 命令里没有 ``--depth``
    → 用户选「浅克隆」却拉全量历史（流量/耗时/磁盘差数十倍），
    而日志仍打印「浅克隆 depth=1」。
    """

    @staticmethod
    def _cmd(depth: int, single_branch: bool) -> list[str]:
        from gcm.git.service import GitService
        from gcm.models import RepoSpec
        svc = GitService(ROOT / "out", fetch_depth=depth, single_branch=single_branch)
        spec = RepoSpec(owner="a", repo="b",
                        url_https="https://github.com/a/b.git", folder_name="a__b")
        return svc.build_clone_cmd(spec)

    def test_depth_without_single_branch(self):
        cmd = self._cmd(1, False)
        self.assertIn("--depth=1", cmd)
        self.assertNotIn("--single-branch", cmd)

    def test_depth_with_single_branch(self):
        cmd = self._cmd(1, True)
        self.assertIn("--depth=1", cmd)
        self.assertIn("--single-branch", cmd)

    def test_no_depth_when_zero(self):
        cmd = self._cmd(0, True)
        self.assertFalse(any(c.startswith("--depth") for c in cmd))
        self.assertNotIn("--single-branch", cmd)

    def test_depth_n(self):
        self.assertIn("--depth=50", self._cmd(50, False))


class TestRedactCoverage(unittest.TestCase):
    """G66-11：脱敏必须覆盖 GitHub 旧式 token 头与 URL query 凭据。

    修复前缺口（实测）：``Authorization: token <x>`` 与
    ``?access_token=ghp_x`` 原样穿透，会随 UI/日志/审计落盘外泄。
    """

    def test_authorization_schemes(self):
        from gcm.util.redact import redact
        cases = {
            "Authorization: token ghp_abc": "Authorization: token ***",
            "Authorization: Bearer ghp_abc": "Authorization: Bearer ***",
            "Authorization: Basic dXNlcjpw": "Authorization: Basic ***",
            "authorization: token xyz": "authorization: token ***",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(redact(raw), want)
                self.assertNotIn("ghp_abc", redact(raw))
                self.assertNotIn("dXNlcjpw", redact(raw))

    def test_other_private_headers(self):
        from gcm.util.redact import redact
        for raw in ("X-Auth-Token: secret1", "X-API-Key: secret2",
                    "JOB-TOKEN: secret3", "PRIVATE-TOKEN: glpat-x"):
            with self.subTest(raw=raw):
                self.assertIn("***", redact(raw))
                self.assertNotIn("secret", redact(raw))

    def test_url_query_credentials(self):
        from gcm.util.redact import redact
        cases = [
            "https://github.com/a/b.git?access_token=ghp_leak&x=1",
            "https://api.example.com/v1?api_key=AKIA123&page=2",
            "https://x.com/cb?token=tok#frag",
            "https://x.com/p?private_token=glpat-y",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                out = redact(raw)
                self.assertIn("***", out)
                for leak in ("ghp_leak", "AKIA123", "tok#", "glpat-y"):
                    self.assertNotIn(leak, out)

    def test_no_false_positives(self):
        from gcm.util.redact import redact
        for raw in ("https://x.com/ok?page=2&limit=10",
                    "normal text without secret",
                    "https://user:pass@github.com/a/b.git"):
            with self.subTest(raw=raw):
                out = redact(raw)
                self.assertNotIn("page=***", out)
                self.assertNotIn("limit=***", out)
        self.assertEqual(redact("https://user:pass@h/a"),
                         "https://***@h/a")


class TestConsoleToolEncoding(unittest.TestCase):
    """G66-13：Windows 自带工具（schtasks 等）输出的是 ANSI 代码页，不是 UTF-8。

    G66-4 把 26 处子进程统一改成 ``encoding="utf-8"`` —— 对 git 是对的，
    但对 **schtasks** 是功能退化。实测（本机 cp936）::

        schtasks /Query /TN <不存在>
          utf-8 解码 → '����: ϵͳ�Ҳ���ָ�����ļ���'   ← 一片乱码
          gbk  解码 → '错误: 系统找不到指定的文件。'      ← 正确

    这段文本会被拼进 ``任务计划操作失败（rc=…）：{detail}`` 直接展示给用户，
    所以是真实的用户可见退化，不是内部细节。
    """

    def test_console_encoding_matches_ansi_codepage(self):
        import locale

        from gcm.app.autostart import _console_encoding
        enc = _console_encoding()
        self.assertTrue(enc, "必须返回一个可用编码名")
        # 应与系统 ANSI 代码页一致（中文 Windows = cp936）
        self.assertEqual(enc, locale.getpreferredencoding(False))

    def test_console_encoding_is_decodable_alias(self):
        from gcm.app.autostart import _console_encoding
        # 编码名必须真的能被 codecs 接受（防呆：拼错名字会在运行时才炸）
        "测试".encode(_console_encoding())

    @unittest.skipIf(os.name != "nt", "schtasks 仅 Windows")
    def test_schtasks_output_is_readable_chinese(self):
        """真实调用 schtasks，断言中文提示可读（不是 U+FFFD 乱码）。"""
        import subprocess

        from gcm.app.autostart import _console_encoding
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", "GCM_Probe_NotExist_G66"],
            capture_output=True, text=True,
            encoding=_console_encoding(), errors="replace", timeout=20)
        self.assertNotEqual(r.returncode, 0, "不存在的任务应返回非 0")
        text = (r.stderr or "") + (r.stdout or "")
        self.assertTrue(text.strip(), "应捕获到错误文本")
        # 乱码特征：替换字符占比过高
        self.assertNotIn("�", text,
                         f"中文提示被解码成乱码：{text[:80]!r}")
        self.assertIn("错误", text, f"应含可读中文「错误」：{text[:80]!r}")


class TestPackagingVersionSync(unittest.TestCase):
    """G66-2：打包/元数据里的版本号必须与 gcm.__version__ 一致。

    历史事故：``pyproject.toml`` 停在 8.0.2、``packaging/installer.iss`` 停在
    8.0.2、``Git-clone-Max.spec`` 曾停在 8.0.2 —— 装出来的 setup.exe 与
    exe 属性版本都落后于实际发布版本。
    """

    def test_pyproject_version_matches(self):
        from gcm import __version__
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        self.assertIsNotNone(match, "pyproject.toml 缺少 [project].version")
        self.assertEqual(match.group(1), __version__,
                         "pyproject.toml version 与 gcm.__version__ 不一致")

    def test_installer_iss_version_matches(self):
        from gcm import __version__
        text = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
        match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', text)
        self.assertIsNotNone(match, "installer.iss 缺少 MyAppVersion")
        self.assertEqual(match.group(1), __version__,
                         "installer.iss MyAppVersion 与 gcm.__version__ 不一致")

    def test_pyinstaller_spec_version_matches(self):
        from gcm import __version__
        parts = [int(x) for x in __version__.split(".")]
        tuple_literal = f"({parts[0]}, {parts[1]}, {parts[2]}, 0)"
        text = (ROOT / "Git-clone-Max.spec").read_text(encoding="utf-8")
        self.assertIn(f"filevers={tuple_literal}", text,
                      "spec filevers 与 __version__ 不一致（Windows exe 属性会显示旧版本）")
        self.assertIn(f"prodvers={tuple_literal}", text)
        self.assertIn(f"StringStruct('FileVersion', '{__version__}')", text)
        self.assertIn(f"StringStruct('ProductVersion', '{__version__}')", text)

    def test_changelog_has_current_version_entry(self):
        from gcm import __changelog__, __version__
        joined = "\n".join(__changelog__)
        self.assertIn(f"{__version__}:", joined,
                      "gcm.__changelog__ 缺少当前版本条目（发布清单要求每次 bump 追加一行）")


class TestNoTimeBombAssertions(unittest.TestCase):
    """G66-3：禁止把"距今 N 天"这类相对语义写成绝对日期字面量。"""

    # 相对语义关键词 → 同一行若出现绝对日期字面量即为时间炸弹
    RELATIVE_HINTS = ("距今", "超过", "未过期", "过期", "stale")
    DATE_LITERAL = re.compile(r"\d{4}-\d{2}-\d{2}")
    # 本守卫文件自身在文档里引用了反例，不参与扫描。
    SELF = pathlib.Path(__file__).name

    @staticmethod
    def _docstring_lines(tree: ast.AST) -> set[int]:
        """模块/类/函数 docstring 覆盖的行号（文档中的示例不算断言）。"""
        lines: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                start = first.lineno
                end = getattr(first, "end_lineno", start) or start
                lines.update(range(start, end + 1))
        return lines

    def test_tests_dir_has_no_relative_date_literals(self):
        offenders: list[str] = []
        for path in sorted((ROOT / "tests").rglob("test_*.py")):
            if path.name == self.SELF:
                continue
            text = path.read_text(encoding="utf-8")
            skip = self._docstring_lines(ast.parse(text))
            for lineno, line in enumerate(text.splitlines(), 1):
                if lineno in skip or not self.DATE_LITERAL.search(line):
                    continue
                if any(hint in line for hint in self.RELATIVE_HINTS):
                    offenders.append(f"tests/{path.name}:{lineno} {line.strip()[:90]}")
        self.assertEqual(offenders, [], "检测到依赖当前时间的绝对日期字面量：\n  "
                                        + "\n  ".join(offenders))

    def test_is_stale_boundary_is_relative(self):
        """边界语义（=30 天不过期 / >30 天过期）用相对时间自证，不依赖跑测日期。"""
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        from gcm.ui.manage_model import ManageModel

        def _row(days_ago: int) -> dict:
            ts = (datetime.datetime.now()
                  - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
            return {"folder_name": "a__b", "owner": "a", "repo": "b",
                    "local_path": "/x/a__b", "last_sync_at": ts,
                    "head_sha": "abc", "host": "github.com", "url": "u", "id": 1,
                    "default_branch": "main", "tags": "", "favorite": 0,
                    "excluded": 0}

        self.assertFalse(ManageModel([_row(29)]).is_stale(0))
        self.assertFalse(ManageModel([_row(30)]).is_stale(0))
        self.assertTrue(ManageModel([_row(31)]).is_stale(0))
        self.assertTrue(ManageModel([_row(3650)]).is_stale(0))
        # 空值/非法值不得抛异常（UI 表格里脏数据是常态）
        self.assertFalse(ManageModel([dict(_row(1), last_sync_at="")]).is_stale(0))
        self.assertFalse(ManageModel(
            [dict(_row(1), last_sync_at="not-a-date")]).is_stale(0))


class TestSubprocessDecodeE2E(unittest.TestCase):
    """G66-4 真实 E2E：UTF-8 输出的 git 命令在本机 locale 下必须可解码。

    不触网：全程本地裸仓（file://）。用 UTF-8 而非 GBK 可表示的字符
    （``…`` U+2026 / ``®`` U+00AE 等，中文仓库的提交信息与文件名里很常见）
    构造真实 git 输出。

    修复前实测：``git log`` 的读取线程抛
    ``UnicodeDecodeError: 'gbk' codec can't decode byte 0xad``，
    且 ``CompletedProcess.stdout`` 塌陷为 ``None`` → 下游 ``None[...]`` TypeError
    → 明明克隆/查询成功却向用户报「失败」。
    """

    @staticmethod
    def _git(cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True)

    def test_utf8_git_output_decodes_on_host_locale(self):
        import shutil
        import tempfile

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g66_decode_"))
        try:
            work = tmp / "work"
            work.mkdir()
            self._git(work, "init", "-q")
            self._git(work, "config", "user.email", "t@t.com")
            self._git(work, "config", "user.name", "t")
            # U+2026 / U+00AE / U+2460 均不是单字节可 GBK 解码的安全字符
            (work / "报告…®.txt").write_text("x", encoding="utf-8")
            (work / "配置①.md").write_text("y", encoding="utf-8")
            self._git(work, "add", "-A")
            self._git(work, "commit", "-qm", "init: 中文…®① 提交信息")
            self._git(work, "branch", "-M", "main")
            remote = tmp / "remote.git"
            subprocess.run(["git", "clone", "-q", "--bare", str(work), str(remote)],
                           capture_output=True, check=True)

            # 走产品代码路径：gcm.git.service 的 git 包装（已强制 utf-8 解码）
            from gcm.git.service import _divergence_info, _is_dirty
            self.assertIsInstance(_is_dirty(str(remote)), bool)
            self.assertIsInstance(_divergence_info(str(work)), tuple)

            # 直接验证 log 输出可解码且内容正确（这是实测崩溃的那条命令）
            r = subprocess.run(
                ["git", "-C", str(work), "log", "--oneline", "--encoding=UTF-8"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNotNone(r.stdout)
            self.assertIn("中文…®①", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_contract_version_env_passthrough(self):
        """CLI --diag --json 的 version 同样来自 __version__（第二条生产者路径）。"""
        import io
        import json
        from contextlib import redirect_stdout

        from gcm import __version__
        from gcm.cli import main as cli_main
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--cli", "--diag", "--json"])
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["version"], __version__)


if __name__ == "__main__":
    unittest.main()
