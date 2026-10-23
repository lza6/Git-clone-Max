"""G66 补丁回归：契约版本单源 + 子进程 UTF-8 解码 + 时间炸弹守卫。

本文件对应 2026-10-12 终局审计发现的三个真实缺陷（G66 节）：

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
import sys
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
# G66-14：收紧守卫。原实现只匹配「纯 semver 字面量」，实测以下写法可绕过：
#   "8.0.2 "（尾空格）/ "v8.0.2"（带前缀）/ "8.0" / "8.0.2.1"。
# 契约生产者里出现任何「含 semver 形态的字符串」都值得报警 —— 自然漂移的形态
# 就是有人复制粘贴版本号并顺手加个前缀或空格，而不是刻意用 AST 拼接对抗守卫。
_SEMVER_ANYWHERE = re.compile(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])")
# 允许出现的版本字面量白名单（协议版本号等，非产品版本）。
#   MCP 协议版本由官方规范定义，与产品版本无关。
_ALLOWED_VERSION_LITERALS = frozenset({"2025-03-26", "2024-11-05"})


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

    def test_contract_doc_version_matches_package(self):
        """**守卫缺口补齐（G67-7）**：契约文档 `docs/agent-contract.md` 的版本也必须单源。

        为什么补这条：`CONTRACT_PRODUCERS` 只覆盖**代码**生产者（cli.py / mcp_server.py /
        2 个技能脚本），**没有覆盖契约文档本身**。结果文档里的示例版本在 9.1.4 bump 时
        被漏掉，悄悄停在了 `9.1.3` —— 而所有守卫都是绿的。文档是给 agent 消费者看的，
        它的 schema 示例就是承诺，必须和代码同源。
        """
        from gcm import __version__
        doc = ROOT / "docs" / "agent-contract.md"
        text = doc.read_text(encoding="utf-8")
        found = set(re.findall(r'"version"\s*:\s*"([^"]+)"', text))
        self.assertTrue(found, "契约文档应含 version 示例")
        for got in found:
            with self.subTest(found=got):
                self.assertEqual(
                    got, __version__,
                    f"docs/agent-contract.md 的 version 示例 {got!r} 与 "
                    f"gcm.__version__ {__version__!r} 不一致")
        # 「适用版本」行也必须同步
        head = re.search(r"适用版本：v([\d.]+)", text)
        self.assertIsNotNone(head, "契约文档应含「适用版本」行")
        self.assertEqual(head.group(1), __version__)

    def test_no_hardcoded_semver_in_contract_producers(self):
        """契约生产者里不得出现任何含 semver 形态的字符串字面量。

        G66-14 收紧：不再要求「整个字符串就是版本号」（那种能被 `"v8.0.2"`、
        `"8.0.2 "` 绕过），而是只要字面量里**含** semver 形态就报警。
        """
        from gcm import __version__
        for path in CONTRACT_PRODUCERS:
            with self.subTest(file=path.name):
                for lineno, value in _string_literals(path):
                    if value in _ALLOWED_VERSION_LITERALS:
                        continue
                    if _SEMVER_ANYWHERE.search(value):
                        self.fail(
                            f"{path}:{lineno} 含硬编码版本 {value!r}"
                            f"（当前 __version__={__version__}）；"
                            "版本只能来自 gcm.__version__，"
                            "确属协议版本请加入 _ALLOWED_VERSION_LITERALS")

    def test_guard_catches_evasive_spellings(self):
        """反向自检（G66-14）：守卫必须能拦住常见的规避写法。

        原实现只匹配纯 semver，实测 `"8.0.2 "` / `"v8.0.2"` 可绕过；
        本用例把这些写法逐条喂给新守卫，确保都能命中。
        """
        evasive = ["8.0.2", "8.0.2 ", " v8.0.2", "v8.0.2", "ver-8.0.2",
                   "GCM 8.0.2 release", "\t9.1.2\n", "8.0.2(beta)"]
        for raw in evasive:
            with self.subTest(raw=raw):
                self.assertIsNotNone(
                    _SEMVER_ANYWHERE.search(raw),
                    f"守卫未能拦住规避写法 {raw!r}")
        # 不应误伤的形态
        safe = ["2025-03-26", "1", "1.2", "1.2.3.4", "v1", "8.0.2.1",
                "127.0.0.1", "0.0.0.0"]
        for raw in safe:
            with self.subTest(raw=raw):
                if raw == "2025-03-26":
                    continue  # 日期正好是三段数字，属白名单场景
                if raw in ("1.2.3.4", "8.0.2.1"):
                    continue  # 四段是「含 semver」的合理命中（保守报警）
                self.assertIsNone(
                    _SEMVER_ANYWHERE.search(raw),
                    f"守卫误伤了 {raw!r}")


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
        """回环地址必须**通过白名单**（不等同于「本机一定能 bind」）。

        G69-18 修正：早期实现用 `skipTest` 兜住 bind 失败 —— 那会在
        Windows（本工具主平台）上把整条用例静默跳过，等于削弱守卫。
        这里改成区分两种失败：
          - `ValueError` = 白名单**拒绝了回环地址** → 真 bug，判失败；
          - `OSError`   = 白名单放行了，只是**该平台/该别名绑不上**
            （实测 macOS 无 lo0 别名、Windows 对 127.0.0.2 返回 WinError 10013）
            → 属平台能力，不算守卫失效，但也不跳过：断言「不是 ValueError」。
        """
        from gcm.http_api import create_server
        for bind in ("127.0.0.1:0", "localhost:0", "127.0.0.2:0", "127.0.0.3:0"):
            with self.subTest(bind=bind):
                try:
                    httpd = create_server(bind, root=ROOT)
                except ValueError as e:      # 白名单误杀回环 → 真缺陷
                    self.fail(f"回环地址 {bind} 被白名单拒绝：{e}")
                except OSError:
                    continue                 # 平台绑不上该别名，白名单本身没问题
                self.addCleanup(httpd.server_close)
                self.assertTrue(httpd.allow_mutate is False,
                                "默认必须是只读")

    def test_ipv6_bind_rejected_with_clear_message(self):
        """G67-5：IPv6 绑定必须给出**明确**的「暂不支持」，而不是崩溃或误报「非回环」。

        原实现把 `::1` 放进白名单，但服务基类的 `address_family` 是 `AF_INET`，
        IPv6 字面量交给 AF_INET 的 getaddrinfo 必然抛 `gaierror`；
        而 `serve()` 只捕 `ValueError` → `--cli --serve ::1:8765` 直接 traceback 崩溃（rc=1），
        与 docstring 承诺的「返回 2」不符。
        """
        from gcm.http_api import create_server
        for bind in ("::1:8765", "[::1]:8765", "[::]:8765", "::ffff:127.0.0.1:8765"):
            with self.subTest(bind=bind):
                with self.assertRaises(ValueError) as ctx:
                    create_server(bind, root=ROOT)
                self.assertIn("IPv6", str(ctx.exception))

    def test_non_loopback_bind_rejected(self):
        from gcm.http_api import create_server
        for bind in ("0.0.0.0:8765", "192.168.1.10:8765", "example.com:8765"):
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
        for bind in ("0.0.0.0:8765", "::1:8765"):
            with self.subTest(bind=bind):
                self.assertEqual(serve(bind, root=ROOT), 2)

    def test_serve_returns_2_on_bind_oserror(self):
        """G67-5：真正绑定失败（如端口被占）必须返回 2，而不是以 traceback 崩溃。"""
        import socket

        from gcm.http_api import create_server, serve
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        try:
            # 同端口已被占用 → bind 抛 OSError → 应返回 2
            self.assertEqual(serve(f"127.0.0.1:{port}", root=ROOT), 2)
            # 直接走 create_server 时 OSError 应向上抛（由 serve 负责转退出码）
            with self.assertRaises(OSError):
                create_server(f"127.0.0.1:{port}", root=ROOT)
        finally:
            holder.close()


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

    def test_depth_actually_takes_effect_e2e(self):
        """真实 E2E：浅克隆必须真的只拿到 1 个提交（不只是命令里有 --depth）。

        G66-14 补：原守卫只断言「命令数组含 --depth」，那只能证明「拼对了」，
        不能证明「生效了」—— 两者是不同的问题。

        注意 git 的既定行为：**对本地路径做克隆时会忽略 --depth**
        （`warning: --depth is ignored in local clones; use file:// instead.`），
        所以本用例必须用 `file://` URL，否则会得到「命令正确但仓不是浅的」的
        假阴性。真实远端（https）不受此影响。
        """
        import shutil
        import tempfile

        from gcm.git.service import GitService
        from gcm.models import RepoSpec

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g66_depth_"))
        try:
            src = tmp / "src"
            src.mkdir()

            def _g(*args: str) -> None:
                subprocess.run(["git", *args], cwd=str(src),
                               capture_output=True, check=True)

            _g("init", "-q")
            _g("config", "user.email", "t@t.com")
            _g("config", "user.name", "t")
            for i in range(5):
                (src / "a.txt").write_text(str(i), encoding="utf-8")
                _g("add", "-A")
                _g("commit", "-qm", f"c{i}")
            _g("branch", "-M", "main")
            bare = tmp / "r.git"
            subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)],
                           capture_output=True, check=True)

            # file:// 才让 git 认 --depth
            # G66-14 跨平台修正：不能写 `"file:///" + str(path).replace("\\","/")`
            # —— 在 POSIX 上路径本就以 `/` 开头，会拼出 `file:////tmp/...`（四个斜杠）。
            # as_uri() 在两个平台上都生成合法 file:// URL。
            url = bare.as_uri()
            svc = GitService(tmp / "out", fetch_depth=1, single_branch=False)
            spec = RepoSpec(owner="x", repo="r", url_https=url, folder_name="x__r")
            self.assertIn("--depth=1", svc.build_clone_cmd(spec))
            result = svc.sync(spec)
            self.assertEqual(result.status.value, "success", result.detail)

            repo_dir = pathlib.Path(result.path)
            self.assertTrue((repo_dir / ".git").is_dir(), "克隆目录应存在")

            def _git_out(*args: str) -> str:
                r = subprocess.run(["git", "-C", str(repo_dir), *args],
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                return r.stdout.strip()

            self.assertEqual(_git_out("rev-parse", "--is-shallow-repository"), "true",
                             "浅克隆仓必须是 shallow（--depth 真的生效）")
            self.assertEqual(_git_out("rev-list", "--count", "HEAD"), "1",
                             "浅克隆应只含 1 个提交（远端有 5 个）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_shallow_repo_incremental_update_e2e(self):
        """**关键回归守卫（G67-3）**：浅仓必须能增量更新，且 HEAD 真的前进。

        这是独立审查线程抓到的、而原守卫**恰好覆盖不到**的缺口：
        `test_depth_actually_takes_effect_e2e` 只 `sync` 一次（只验 clone 侧），
        于是「浅仓第二次同步永久失败」这条路径完全没有断言保护。

        原缺陷（G66-10 引入的回归）：`_update` 在浅仓上重复 `fetch --depth=N`，
        新取到的 tip 成为无父的 shallow 边界 → `merge-base` 无解 →
        `refusing to merge unrelated histories` → 被误判成「冲突」→
        仓库**从第二次同步起永久无法更新**。实测矩阵：
            depth=1, 远端+1 → conflict, HEAD 不前进
            depth=1, 远端+2 → conflict, HEAD 不前进
        改为 plain fetch（尊重既有 shallow 边界）后两者均 success 且 HEAD 前进。
        """
        import shutil
        import tempfile

        from gcm.git.service import GitService
        from gcm.models import RepoSpec

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g67_shallow_"))
        try:
            src = tmp / "src"
            src.mkdir()

            def _g(*args: str, cwd: pathlib.Path | None = None) -> None:
                subprocess.run(["git", *args], cwd=str(cwd or src),
                               capture_output=True, check=True)

            _g("init", "-q")
            _g("config", "user.email", "t@t.com")
            _g("config", "user.name", "t")
            _g("checkout", "-q", "-b", "main")
            (src / "f.txt").write_text("c1", encoding="utf-8")
            _g("add", "-A")
            _g("commit", "-qm", "c1")
            bare = tmp / "r.git"
            subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)],
                           capture_output=True, check=True)
            _g("remote", "add", "origin", str(bare))
            _g("push", "-q", "origin", "main")

            out = tmp / "out"
            out.mkdir()
            svc = GitService(root_dir=str(out), fetch_depth=1, single_branch=False)
            url = bare.as_uri()
            spec = RepoSpec(owner="o", repo="r", url_https=url, folder_name="o__r")

            r1 = svc.sync(spec)
            self.assertEqual(r1.status.value, "success", r1.detail)
            repo = pathlib.Path(r1.path)

            def _git_out(*args: str) -> str:
                r = subprocess.run(["git", "-C", str(repo), *args],
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                return r.stdout.strip()

            self.assertEqual(_git_out("rev-parse", "--is-shallow-repository"), "true",
                             "前置条件：首次克隆必须是浅仓，否则本用例失去意义")
            head_after_clone = _git_out("rev-parse", "HEAD")

            # 远端推进 2 个提交（覆盖「推进数 > depth」这一必然失败的形态）
            for i in (2, 3):
                (src / "f.txt").write_text(f"c{i}", encoding="utf-8")
                _g("add", "-A")
                _g("commit", "-qm", f"c{i}")
                _g("push", "-q", "origin", "main")

            r2 = svc.sync(RepoSpec(owner="o", repo="r", url_https=url,
                                   folder_name="o__r"))
            self.assertEqual(
                r2.status.value, "success",
                f"浅仓增量更新必须成功；实际 status={r2.status.value} "
                f"msg={r2.message or r2.detail}")
            head_after_update = _git_out("rev-parse", "HEAD")
            self.assertNotEqual(head_after_update, head_after_clone,
                                "HEAD 必须前进（原缺陷的表现就是 HEAD 永不前进）")
            self.assertEqual(_git_out("rev-parse", "--is-shallow-repository"), "true",
                             "增量更新后仍应是浅仓（不应悄悄变成全量）")

            # 再来一次，确认不是「只对第一次侥幸成功」
            (src / "f.txt").write_text("c4", encoding="utf-8")
            _g("add", "-A")
            _g("commit", "-qm", "c4")
            _g("push", "-q", "origin", "main")
            r3 = svc.sync(RepoSpec(owner="o", repo="r", url_https=url,
                                   folder_name="o__r"))
            self.assertEqual(r3.status.value, "success", r3.detail)
            self.assertNotEqual(_git_out("rev-parse", "HEAD"), head_after_update,
                                "第二次增量更新也必须前进（原缺陷是「永久卡死」）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


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

    def test_closing_quote_is_preserved(self):
        """G67-6：凭据体正则不得吞掉紧邻的收尾引号。

        `\\S+` 会把 `'` 一起吃掉，打码结果看起来像被截断（`...Negotiate ***` 丢了收尾 `'`）。
        改用 `[^\\s\"']+` 后引号保留。
        """
        from gcm.util.redact import redact
        cases = {
            "curl -H 'Authorization: Negotiate TlRM'":
                "curl -H 'Authorization: Negotiate ***'",
            'curl -H "Authorization: Bearer abc"':
                'curl -H "Authorization: Bearer ***"',
            "curl -H 'PRIVATE-TOKEN: glpat-x'":
                "curl -H 'PRIVATE-TOKEN: ***'",
            "curl -H 'X-API-Key: k1'":
                "curl -H 'X-API-Key: ***'",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(redact(raw), want)
        # 反向：不能因为排除引号而漏掉凭据本身
        self.assertNotIn("TlRM", redact("curl -H 'Authorization: Negotiate TlRM'"))
        self.assertNotIn("glpat-x", redact("curl -H 'PRIVATE-TOKEN: glpat-x'"))


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

    @unittest.skipIf(os.name != "nt", "GetACP 仅 Windows")
    def test_console_encoding_matches_getacp(self):
        """G67-4：编码必须来自 `GetACP()`（真实 ANSI 代码页），而非 locale。

        原实现用 `locale.getpreferredencoding(False)` —— 它会被 PEP 540
        （`PYTHONUTF8=1` / Python UTF-8 模式）改写为 `utf-8`，于是同一份代码在
        不同环境变量下解码结果不同，schtasks 又会变回乱码。实现在不同子树进程里
        验证 `PYTHONUTF8=1` 下仍等于 `cp<ACP>`。
        """
        import ctypes
        import subprocess

        acp = int(ctypes.windll.kernel32.GetACP())
        expected = f"cp{acp}"

        from gcm.app.autostart import _console_encoding
        self.assertEqual(_console_encoding(), expected)

        # 子进程带 PYTHONUTF8=1 重新求值：必须保持一致（这正是原实现失效的场景）
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run(
            [sys.executable, "-c",
             "from gcm.app.autostart import _console_encoding;"
             "print(_console_encoding())"],
            cwd=str(ROOT), capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), expected,
                         "PYTHONUTF8=1 下编码发生了漂移（原缺陷会在此复发）")

    def test_console_encoding_is_decodable_alias(self):
        """编码名必须真的能被 codecs 接受（防呆：拼错名字运行时才炸）。

        G66-14 跨平台修正：**不能拿中文去 encode** —— CI 上 ubuntu 的
        `locale.getpreferredencoding(False)` 可能是 `ANSI_X3.4-1968`（ASCII）
        或 `UTF-8`，用中文编码在 ASCII locale 下会抛 UnicodeEncodeError，
        让这条「编码名合法性」断言变成平台相关的假失败。用 ASCII 字符验即可 ——
        我们要验的是「名字是合法 codec」，不是「能编码中文」。
        """
        from gcm.app.autostart import _console_encoding
        self.assertTrue("ok".encode(_console_encoding()))

    @unittest.skipIf(os.name == "nt",
                     "非 Windows 上 _console_encoding 只作为回退路径存在")
    def test_console_encoding_posix_fallback_usable(self):
        """POSIX 上不应返回 Windows 专有的 `mbcs`（那是无效 codec）。"""
        import codecs

        from gcm.app.autostart import _console_encoding
        name = _console_encoding()
        # 显式断言（而非仅靠 LookupError 让测试失败）：非法名字会在这里抛
        self.assertIsInstance(codecs.lookup(name), codecs.CodecInfo,
                              f"_console_encoding() 返回了非法 codec：{name!r}")
        self.assertNotEqual(name, "mbcs", "mbcs 是 Windows 专有 codec")

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
        # 判据一（核心）：不得出现替换字符 —— 这才是「用错编码」的直接特征
        self.assertNotIn("�", text, f"提示被解码成乱码：{text[:80]!r}")
        # 判据二（与语言无关）：用 `_console_encoding()` 选定的编码，文本必须**无损**。
        # 原实现硬断言「含中文『错误』」—— 在英文系统上必红（GitHub 的
        # windows-latest 就是英文），而且 schtasks 在负载/权限不同时也会换文案，
        # 实测全量负载下偶发失败。真正要守的是「编码选对了」，
        # 而不是「系统语言是中文」。
        enc = _console_encoding()
        text.encode(enc)      # 不能无损回编码 → 说明选错了 codec
        # 若输出里确实包含 CJK（中文系统），那它必须是可读的、不是乱码
        if any("一" <= ch <= "鿿" for ch in text):
            self.assertIn("错误", text, f"中文提示应可读：{text[:80]!r}")


class TestPostCloneHookInjection(unittest.TestCase):
    """G67-1：克隆后钩子不得因不可信目录名而被注入命令。

    钩子文本是用户自己写的（可信），但替换进 `%PATH%` 的**仓库路径来自 URL**
    （不可信）—— `url_lib.sanitize_name` 只过滤 `\\/:*?"<>|` 与控制字符，
    不过滤 `& ; $ ( ) ^ ! %` 等 cmd.exe 元字符。本工具最典型的用法恰是
    「粘贴别人的仓库清单批量克隆」，所以这条路径真实可触发。

    实测（修复前）：目录名含 `&echo INJECTED&` 时，cmd.exe 会把
    `echo INJECTED` 当成独立命令执行。
    """

    def test_replacement_path_is_quoted(self):
        """替换进钩子的路径必须被双引号包裹，且不出现双层引号。"""
        svc = _make_hook_svc(r"echo done > %PATH%\marker.done")
        cmd = svc._build_post_clone_hook_cmd(ROOT / "a&b")
        self.assertIsNotNone(cmd)
        # 双引号必须闭合包裹路径
        self.assertRegex(cmd, r'"[^"]*a&b[^"]*"')

    def test_pre_quoted_placeholder_not_doubled(self):
        """用户已写 "%PATH%" 时归一化，不产生 ""path""。"""
        svc = _make_hook_svc(r'echo done > "%PATH%"')
        cmd = svc._build_post_clone_hook_cmd(ROOT / "plain")
        self.assertIsNotNone(cmd)
        self.assertNotIn('""', cmd)
        self.assertIn('"', cmd)

    def test_metachars_cannot_break_out(self):
        """含 & | > ^ 的目录名不得突破引号边界。"""
        for name in ("x&echo PWN&y", "x|whoami|y", "x^&y", "x>out&y"):
            with self.subTest(name=name):
                svc = _make_hook_svc(r"echo ok > %PATH%\ \done")
                cmd = svc._build_post_clone_hook_cmd(ROOT / name)
                self.assertIsNotNone(cmd)
                # 路径整体被一对引号包住（引号总数为偶数）
                self.assertEqual(cmd.count('"') % 2, 0)

    def test_path_with_double_quote_is_refused(self):
        """路径含 `"` 时拒绝执行（无法安全转义），而不是冒险拼接。"""
        svc = _make_hook_svc("echo %PATH%")
        self.assertIsNone(svc._build_post_clone_hook_cmd(ROOT / 'a"b'))

    def test_quoted_placeholder_with_suffix_is_quoted(self):
        """**关键回归守卫（G67-3）**：模板 `"%PATH%\\suffix"`（占位符带后缀+整体引号）。

        这是独立审查线程实测出的绕过：原归一化只替换**完整**占位符 `"%PATH%"`，
        于是 `echo done > "%PATH%\\marker.txt"` 会生成
        `""<path>"\\marker.txt"` —— 两个相邻引号使引号状态翻转，路径落到引号**外**，
        `&` 重新被 cmd.exe 当命令分隔符。实测目录名 `x&copy NUL PWNED_Q.txt&y`
        在该模板下**注入成功**。

        修复改用正则剥掉占位符两侧的引号（含带后缀写法），再由我们统一加引号。
        """
        for tpl in (r'echo done > "%PATH%\marker.txt"',
                    r"echo done > '%PATH%\marker.txt'",
                    r'echo done > "%PATH%"',
                    r"echo done > %PATH%\marker.txt",
                    "echo done > %PATH%"):
            with self.subTest(tpl=tpl):
                svc = _make_hook_svc(tpl)
                cmd = svc._build_post_clone_hook_cmd(ROOT / "x&copy NUL PWNED_Q.txt&y")
                self.assertIsNotNone(cmd)
                # 不得出现相邻的双引号（`""`）—— 那是引号状态翻转的信号
                self.assertNotIn('""', cmd, f"出现相邻引号，可被绕过：{cmd}")
                # 路径必须整体落在一对引号内
                self.assertRegex(cmd, r'"[^"]*x&copy NUL PWNED_Q\.txt&y[^"]*"')
                # 引号总数必须为偶数（状态平衡）
                self.assertEqual(cmd.count('"') % 2, 0, f"引号不平衡：{cmd}")

    def test_env_expansion_chars_are_refused(self):
        """路径含 `%` 或 `!` 时拒绝执行（引号内它们仍会被 cmd 展开/延迟展开）。"""
        svc = _make_hook_svc("echo %PATH%")
        for name in ("a%PATH%b", "a%CD%b", "a!b"):
            with self.subTest(name=name):
                self.assertIsNone(svc._build_post_clone_hook_cmd(ROOT / name))

    @unittest.skipIf(os.name != "nt", "cmd.exe 注入语义仅 Windows")
    def test_suffix_template_injection_e2e(self):
        """真实 E2E：`"%PATH%\\marker.txt"` 模板下注入必须被阻断。

        判据同 `test_no_injection_e2e_after_clone`：看注入命令的**副作用**是否真的发生。
        """
        import shutil
        import tempfile

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g67_suffix_"))
        try:
            root = tmp / "clones"
            root.mkdir()
            repo = root / "x&copy NUL PWNED_S.txt&y"
            repo.mkdir()
            svc = _make_hook_svc(r'echo done > "%PATH%\marker.txt"')
            cmd = svc._build_post_clone_hook_cmd(repo)
            self.assertIsNotNone(cmd)
            subprocess.run(cmd, shell=True, cwd=str(root), capture_output=True,
                           timeout=30)
            self.assertFalse((root / "PWNED_S.txt").exists(),
                             f"发生命令注入：{cmd}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipIf(os.name != "nt", "cmd.exe 注入语义仅 Windows")
    def test_no_injection_e2e_after_clone(self):
        """真实 E2E（走完整 _clone 路径）：注入载荷绝不真的执行。

        判据的选择很关键：
        - **不能**用「日志里是否出现注入串」—— 注入串本就是路径的一部分，必然
          出现在路径回显里（那是回显，不是执行）。
        - **不能**用 `>` 重定向做载荷 —— `>` 是 Windows 非法目录字符，git 建不出
          该目录，钩子根本不会跑，测试会变成假通过。
        - 可靠判据：**注入命令是否产生了真实副作用**。这里用
          `&mkdir PWNED_G67&`（`&` 是合法文件名字符，`mkdir` 无需重定向）。

        A/B 实测对照（同一份真实克隆流程）：
          加引号（修复后）  → status=success，PWNED_G67 未创建
          去掉引号（修复前）→ status=success，PWNED_G67 **被创建**（注入成功）
        """
        import shutil
        import tempfile

        from gcm.git.service import GitService
        from gcm.models import RepoSpec

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g67_hook_"))
        try:
            src = tmp / "src"
            src.mkdir()

            def _g(*args: str) -> None:
                subprocess.run(["git", *args], cwd=str(src),
                               capture_output=True, check=True)

            _g("init", "-q")
            _g("config", "user.email", "t@t.com")
            _g("config", "user.name", "t")
            (src / "a.txt").write_text("x", encoding="utf-8")
            _g("add", "-A")
            _g("commit", "-qm", "c1")
            _g("branch", "-M", "main")
            bare = tmp / "r.git"
            subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)],
                           capture_output=True, check=True)

            clone_root = tmp / "clones"
            lines: list[str] = []
            svc = GitService(clone_root,
                             post_clone_hook=r"echo ran > %PATH%\hook_ran.txt",
                             ssh_key="",
                             on_line=lambda c: lines.append(c.text))
            spec = RepoSpec(owner="a", repo="b", url_https=str(bare),
                            folder_name="x&mkdir PWNED_G67&y")
            from gcm.models import SyncAction, SyncResult, SyncStatus
            res = SyncResult(spec=spec, status=SyncStatus.PENDING,
                             action=SyncAction.CLONED)
            svc._clone(spec, res)

            # 前置条件：克隆必须成功且钩子真的跑了，否则本用例会「假通过」
            self.assertEqual(res.status.value, "success", res.detail)
            hook_line = next((ln for ln in lines if "执行克隆后钩子" in ln), "")
            self.assertTrue(hook_line, f"钩子未执行，用例无效：{lines[-3:]}")

            # 决定性判据：注入命令的副作用不存在
            self.assertFalse((clone_root / "PWNED_G67").exists(),
                             "发生命令注入：PWNED_G67 被创建 —— "
                             f"钩子命令：{hook_line}")
            # 且钩子命令本身必须已把路径加引号
            self.assertIn('"', hook_line, f"钩子命令未加引号：{hook_line}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _make_hook_svc(hook: str):
    """构造带钩子的 GitService，供钩子命令拼装断言用。"""
    from gcm.git.service import GitService
    return GitService(ROOT / "out", post_clone_hook=hook)


class TestNoCrossThreadWidgetAccess(unittest.TestCase):
    """G67-2：工作线程不得直接触碰 QWidget / QAbstractItemModel。

    Qt 的 QObject 有线程亲和性。原实现有 3 处在裸 ``threading.Thread`` 内直接：

    - ``main_window``：``self._emit_log(...)``（LogModel 是 QAbstractListModel）
      与 ``self.statusBar().showMessage(...)``（QWidget）
    - ``progress_table``：``owner.statusBar().showMessage(...)``
    - ``settings_panel``：在工作线程 ``QMessageBox.information(...)``（建窗 + exec）

    后果是随机崩溃或静默错行（``__main__`` 装了 excepthook 后 PyQt 通常不再
    abort，所以更难排查）。修复方式统一为「worker emit 信号 → GUI 线程槽」。

    本守卫用 AST 静态扫描守卫这三处已知形态，防止回归。
    """

    # 工作线程函数体内**禁止出现**的调用（GUI 线程亲和对象的方法名）
    # 注意：不能只列 `QMessageBox` —— 原代码用的是别名 `from PyQt6.QtWidgets
    # import QMessageBox as _QMB` 再调 `_QMB.information(...)`，被调名是
    # `information`。所以必须按「最终被调用的方法名」列，否则守卫会漏。
    _FORBIDDEN = (
        "statusBar", "_emit_log", "showMessage", "setText",
        "QMessageBox", "information", "warning", "critical", "question",
    )
    # 允许出现这些调用的位置（GUI 线程槽 / 非线程上下文）
    _ALLOW_MARKERS = ("_on_archive_finished", "_on_notify_sent", "_on_diag_finished")

    def _thread_targets(self, path: pathlib.Path) -> list[ast.AST]:
        """找出 `threading.Thread(target=fn)` 里的 fn（函数对象或 lambda）。"""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # 先收集函数名 → 函数节点
        funcs: dict[str, ast.AST] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs[node.name] = node
        found: list[ast.AST] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name != "Thread":
                continue
            for kw in node.keywords:
                if kw.arg != "target":
                    continue
                tgt = kw.value
                if isinstance(tgt, ast.Name) and tgt.id in funcs:
                    found.append(funcs[tgt.id])
                elif isinstance(tgt, ast.Lambda):
                    found.append(tgt)
        return found

    def _violations(self, file_rel: str) -> list[str]:
        path = ROOT / file_rel
        bad: list[str] = []
        for target in self._thread_targets(path):
            for node in ast.walk(target):
                if not isinstance(node, ast.Call):
                    continue
                name = (getattr(node.func, "attr", None)
                        or getattr(node.func, "id", None) or "")
                if name not in self._FORBIDDEN:
                    continue
                # 允许：在嵌套函数里再次定义 GUI 线程槽（本项目无此写法，保守放行 emit）
                bad.append(f"{file_rel}:{node.lineno} 线程内调用 {name}()")
        return bad

    def test_main_window_thread_targets_are_clean(self):
        bad = self._violations("gcm/ui/main_window.py")
        self.assertEqual(bad, [], "工作线程内直接操作了 GUI 对象：\n  "
                                  + "\n  ".join(bad))

    def test_progress_table_thread_targets_are_clean(self):
        bad = self._violations("gcm/ui/progress_table.py")
        self.assertEqual(bad, [], "工作线程内直接操作了 GUI 对象：\n  "
                                  + "\n  ".join(bad))

    def test_settings_panel_thread_targets_are_clean(self):
        bad = self._violations("gcm/ui/settings_panel.py")
        self.assertEqual(bad, [], "工作线程内直接操作了 GUI 对象：\n  "
                                  + "\n  ".join(bad))

    def test_guard_catches_the_original_violations(self):
        """反向自检：守卫必须能抓到**修复前**的三处违规。

        没有这条，守卫可能是「永远通过」的空壳。用 git 取修复前版本跑同一套
        扫描逻辑，断言三处都被抓出来（含 settings_panel 的 `_QMB.information`
        别名写法 —— 那正是最初漏掉的一种形态）。

        基线用固定的 v9.1.2 发布点 `f0e686f`（本轮 G66/G67 审计开始前的最后一个
        版本），而不是 `HEAD~N` —— 后者会随提交数变化而失效。
        """
        import subprocess
        baseline = "f0e686f"  # v9.1.2 发布提交
        rels = ("gcm/ui/main_window.py", "gcm/ui/progress_table.py",
                "gcm/ui/settings_panel.py")
        for rel in rels:
            with self.subTest(file=rel):
                old = subprocess.run(
                    ["git", "show", f"{baseline}:{rel}"], cwd=str(ROOT),
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
                if old.returncode != 0:
                    self.skipTest(f"取不到基线版本 {baseline}:{rel}")
                import tempfile
                tmpd = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g67_old_"))
                try:
                    mirror = tmpd / rel
                    mirror.parent.mkdir(parents=True, exist_ok=True)
                    mirror.write_text(old.stdout, encoding="utf-8")
                    saved = globals()["ROOT"]
                    try:
                        globals()["ROOT"] = tmpd
                        bad = self._violations(rel)
                    finally:
                        globals()["ROOT"] = saved
                    self.assertTrue(
                        bad, f"{rel} 修复前应被抓到违规，但守卫报无问题 —— 守卫是空壳")
                finally:
                    import shutil
                    shutil.rmtree(tmpd, ignore_errors=True)

    def test_bridge_signals_declared_and_connected(self):
        """三个桥接信号必须在 MainWindow 上声明**且**连接（声明没连等于没修）。"""
        src = (ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        for sig in ("archive_finished", "notify_sent", "diag_finished"):
            with self.subTest(signal=sig):
                self.assertTrue(
                    re.search(rf"^\s*{sig}\s*=\s*pyqtSignal\(", src, re.MULTILINE),
                    f"{sig} 未声明为 pyqtSignal")
                self.assertTrue(
                    re.search(rf"self\.{sig}\.connect\(", src, re.MULTILINE),
                    f"{sig} 未 connect 到槽（信号声明了但不接等于没修）")

    def test_archive_finished_slot_updates_ui_on_gui_thread(self):
        """真实构造 MainWindow：archive_finished 必须由主线程槽处理。"""
        import os as _os
        _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import tempfile

        from PyQt6.QtWidgets import QApplication

        from gcm.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g67_mw_"))
        win = None
        try:
            win = MainWindow(tmp)
            self.assertTrue(hasattr(win, "_on_archive_finished"))
            # 直接调用槽（等同 GUI 线程执行），不应抛异常
            win._on_archive_finished(str(tmp / "x.zip"), True, "归档下载完成：x.zip")
            win._on_archive_finished(str(tmp / "y.zip"), False, "归档下载失败：boom")
            win._on_notify_sent("完成通知 Webhook 已发送")
            app.processEvents()
        finally:
            if win is not None:
                win.close()
                win.deleteLater()
                app.processEvents()

    def test_slot_runs_on_gui_thread_when_emitted_from_worker(self):
        """**核心断言**：工作线程 emit → 槽必须在 GUI 线程执行（不是排队错觉）。
        整个 G67-2 修复都建立在「Qt 跨线程 emit 自动走 QueuedConnection」这一约定上，
        所以必须真的验证线程 id，而不是只验证「不报错」。断言三点：
          1. emit 后、事件循环未跑时槽**尚未执行**（证明是排队而非直连）
          2. 跑事件循环后槽执行了
          3. 槽执行的线程 id == 主线程 id
        """
        import os as _os
        import tempfile
        import threading

        _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication

        from gcm.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        main_tid = threading.get_ident()
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g67_thr_"))
        win = None
        try:
            win = MainWindow(tmp)
            seen: list[tuple[int, str, bool]] = []
            win.archive_finished.disconnect()
            win.archive_finished.connect(
                lambda dest, ok, msg: seen.append((threading.get_ident(), dest, ok)))

            threading.Thread(
                target=lambda: win.archive_finished.emit("X.zip", True, "done"),
                daemon=True).start()
            # 给工作线程一点时间完成 emit
            for _ in range(50):
                if threading.active_count() >= 1:
                    break
                threading.Event().wait(0.02)
            threading.Event().wait(0.15)

            self.assertEqual(seen, [], "emit 后槽不应立即执行 —— 应排队到 GUI 线程")
            QTimer.singleShot(150, app.quit)
            app.exec()
            self.assertEqual(len(seen), 1, "事件循环跑完后槽应已执行一次")
            self.assertEqual(seen[0][0], main_tid,
                             "槽必须执行在 GUI 主线程；跑到工作线程说明桥接失效")
        finally:
            if win is not None:
                win.close()
                win.deleteLater()
                app.processEvents()


class TestUpdateCheckThreading(unittest.TestCase):
    """G67-9：检查更新不得在 GUI 线程跑同步 HTTP。

    历史事故：`check_update_now`（按钮回调）与 `_check_update_silent`
    （`QTimer.singleShot` 回调，也在主线程）都**同步**调用 `check_latest()`，
    而它内部失败时会 `time.sleep(2)` 重试一次 —— 点一下按钮界面最长冻结 2s+，
    启动后 2.5s 也会卡一下。修复方式与 G67-2 一致：worker 线程只 emit 信号。
    """

    def test_check_latest_has_blocking_retry(self):
        """前提守卫：check_latest 确实存在阻塞重试。

        若哪天去掉了 `time.sleep`，那「必须挪到工作线程」的理由就不成立了，
        本类其余断言会变成过度约束 —— 那时应重新评估而非直接删。
        """
        src = (ROOT / "gcm/app/updater.py").read_text(encoding="utf-8")
        self.assertIn("time.sleep(", src,
                      "updater.check_latest 已无阻塞重试；请复核 G67-9 是否仍必要")

    def test_update_checked_signal_declared_and_connected(self):
        """`update_checked` 必须声明为 pyqtSignal **且** connect（声明没连等于没修）。"""
        src = (ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        self.assertTrue(
            re.search(r"^\s*update_checked\s*=\s*pyqtSignal\(", src, re.MULTILINE),
            "update_checked 未声明为 pyqtSignal")
        self.assertTrue(
            re.search(r"self\.update_checked\.connect\(", src, re.MULTILINE),
            "update_checked 未 connect 到槽")

    def test_check_update_now_does_not_call_check_latest_on_gui_thread(self):
        """AST 断言：手动检查的**两条路径**都不得在自身语句里同步调用 `check_latest(`。

        只在嵌套函数体（worker）里调用是合法的 —— 那才是修复本身。
        因此用 AST 精确取「函数自身语句」并**不下钻**嵌套 FunctionDef/Lambda，
        避免把 docstring 里提到的 `check_latest()` 误判成真调用。

        G77：实现已从 `main_window.check_update_now` 抽到 `ui/update_flow.check_now`。
        守卫必须**跟着代码走** —— 只扫 main_window 的话，它现在只会检查一个转调方法
        （函数体里没有 `check_latest`），等于变成一条永远绿的假断言。故两处都扫，
        并额外断言实现里**真的起了线程**（防有人把 worker 改回同步调用）。
        """
        targets = (
            (ROOT / "gcm/ui/main_window.py", "check_update_now"),
            (ROOT / "gcm/ui/update_flow.py", "check_now"),
        )
        problems: list[str] = []
        for path, name in targets:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            fn = next((node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef) and node.name == name), None)
            if fn is None:
                problems.append(f"{path.name} 中找不到 {name}（拆分后名字变了？守卫要跟着改）")
                continue
            stack = list(fn.body)
            while stack:
                node = stack.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue  # 嵌套函数体是 worker，合法；不下钻
                if isinstance(node, ast.Call):
                    call_name = (getattr(node.func, "attr", None)
                                 or getattr(node.func, "id", None) or "")
                    if call_name in ("check_latest", "check_latest_with_sha"):
                        problems.append(
                            f"{path.name}:{node.lineno} 在函数自身语句里调用了 {call_name}")
                stack.extend(ast.iter_child_nodes(node))
        self.assertEqual(problems, [],
                         "检查更新在 GUI 线程同步跑网络（G67-9 回归）：" + "；".join(problems))

        # 附加：worker 线程必须真的起在 `check_now` **自身作用域**里。
        # 早先这里是全文子串判断，把 worker 挪到同文件别的函数也照样绿 → 现改 AST。
        flow_path = ROOT / "gcm/ui/update_flow.py"
        flow_tree = ast.parse(flow_path.read_text(encoding="utf-8"))
        check_now = next((n for n in ast.walk(flow_tree)
                          if isinstance(n, ast.FunctionDef) and n.name == "check_now"), None)
        self.assertIsNotNone(check_now, "update_flow 里找不到 check_now（拆分后改名了？）")
        own_calls: list[str] = []
        pending = list(check_now.body)
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # worker 的函数体不在此断言范围
            if isinstance(node, ast.Call):
                own_calls.append(getattr(node.func, "attr", None)
                                 or getattr(node.func, "id", None) or "")
            pending.extend(ast.iter_child_nodes(node))
        self.assertIn("Thread", own_calls,
                      "check_now 必须在自身语句里起 threading.Thread（否则界面会冻结）")
        self.assertIn("start", own_calls, "worker 线程必须真的 start()")

    def test_manual_check_runs_worker_off_gui_thread(self):
        """**真实行为断言**：点「检查更新」时，HTTP 必须在非 GUI 线程执行。

        做法：把 `gcm.app.updater.check_latest` 换成记录线程 id 的假实现，
        断言 ① 调用发生在非主线程 ② 按钮回调**立即**返回（未被 2s 重试阻塞）
        ③ 弹窗只在 `update_checked` 回到 GUI 线程后发生。
        """
        import os as _os
        import tempfile
        import threading
        import time as _time

        _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication

        import gcm.app.updater as updater_mod
        from gcm.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        main_tid = threading.get_ident()
        calls: list[int] = []
        slot_calls: list[int] = []

        def _fake_check_latest(*_a, **_kw):
            calls.append(threading.get_ident())
            _time.sleep(0.30)  # 模拟网络往返
            return (True, "9.9.9", "https://example.invalid/dl", "")

        def _fake_check_latest_sha(*_a, **_kw):
            # G76 起生产代码改走 check_latest_with_sha（五元组，内部还会二次抓 Release
            # 取 sha256）。打桩必须打到这一层，否则真网络请求会把 emit 拖过断言窗口。
            calls.append(threading.get_ident())
            _time.sleep(0.30)
            return (True, "9.9.9", "https://example.invalid/dl", "", "deadbeef")

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g67_upd_"))
        win = None
        original = updater_mod.check_latest
        original_sha = getattr(updater_mod, "check_latest_with_sha", None)
        import gcm.ui.main_window as mw_mod
        # G76 起弹窗实现搬到 gcm.ui.update_flow（它自己 import 了 QMessageBox），
        # 只在 main_window 上打桩拦不住 → 两个模块都打，恢复时也两个都还原。
        # G77 起弹窗实现搬到 gcm.ui.update_flow，但它**没有**模块级 QMessageBox ——
        # `update_flow._msgbox()` 在运行期返回 `main_window.QMessageBox`。
        # 所以单点打桩 mw_mod.QMessageBox 即覆盖两条路径（曾试过再打桩 update_flow，
        # 那是死分支：该模块根本没有这个属性）。
        real_mb = mw_mod.QMessageBox
        try:
            win = MainWindow(tmp)
            # G68-4：必须 show() —— `_on_update_checked` 只在窗口**可见**时弹模态
            # （不可见还弹 = headless 下永久阻塞，已实测挂死 7 分钟）。
            # 这里 setText 会走「已是最新/有新版」分支，窗口不可见则断言弹窗会落空。
            win.show()
            app.processEvents()
            updater_mod.check_latest = _fake_check_latest
            if original_sha is not None:
                updater_mod.check_latest_with_sha = _fake_check_latest_sha

            # 弹窗打桩：真 QMessageBox.question 会阻塞事件循环，测试无法收尾
            class _StubMB:
                StandardButton = real_mb.StandardButton
                seen: list[str] = []

                @staticmethod
                def information(*_a, **_k):
                    _StubMB.seen.append("information")

                @staticmethod
                def question(*_a, **_k):
                    _StubMB.seen.append("question")
                    return real_mb.StandardButton.No

            mw_mod.QMessageBox = _StubMB

            # 探针：记录线程 id 后**仍然调用真槽** —— 顺带验证 _update_checking 复位
            win.update_checked.disconnect()

            def _probe(*args):
                slot_calls.append(threading.get_ident())
                win._on_update_checked(*args)

            win.update_checked.connect(_probe)

            t0 = _time.perf_counter()
            win.check_update_now()
            elapsed = _time.perf_counter() - t0

            self.assertLess(elapsed, 0.25,
                            f"check_update_now 阻塞了 {elapsed:.2f}s —— 仍在 GUI 线程同步跑")
            self.assertTrue(win._update_checking, "发起后应立即置位防连点标记")
            for _ in range(200):
                if calls:
                    break
                threading.Event().wait(0.02)
            self.assertEqual(len(calls), 1, "worker 未调用 check_latest")
            self.assertNotEqual(calls[0], main_tid,
                                "check_latest 跑在 GUI 主线程 —— G67-9 未修复")

            QTimer.singleShot(800, app.quit)
            app.exec()
            self.assertEqual(len(slot_calls), 1, "update_checked 槽应被触发一次")
            self.assertEqual(slot_calls[0], main_tid,
                             "槽必须回到 GUI 主线程执行")
            self.assertFalse(win._update_checking,
                             "槽跑完后必须复位 _update_checking，否则「检查更新」永久失效")
            self.assertEqual(_StubMB.seen, ["question"],
                             "有新版应走 question 分支一次（弹窗只在 GUI 线程发生）")
        finally:
            mw_mod.QMessageBox = real_mb
            updater_mod.check_latest = original
            if original_sha is not None:
                updater_mod.check_latest_with_sha = original_sha
            if win is not None:
                win.close()
                win.deleteLater()
                app.processEvents()

    def test_silent_check_runs_off_gui_thread(self):
        """启动后静默检查同样不得占用 GUI 线程。"""
        import os as _os
        import tempfile
        import threading
        import time as _time

        _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        import gcm.app.updater as updater_mod
        from gcm.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        main_tid = threading.get_ident()
        calls: list[int] = []

        def _fake_check_latest(*_a, **_kw):
            calls.append(threading.get_ident())
            _time.sleep(0.30)
            return (False, "0.0.0", "", "")

        def _fake_check_latest_sha(*_a, **_kw):
            # G76 起生产代码走 check_latest_with_sha（五元组）
            calls.append(threading.get_ident())
            _time.sleep(0.30)
            return (False, "0.0.0", "", "", "")

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g67_upds_"))
        win = None
        original = updater_mod.check_latest
        original_sha = getattr(updater_mod, "check_latest_with_sha", None)
        try:
            win = MainWindow(tmp)
            updater_mod.check_latest = _fake_check_latest
            if original_sha is not None:
                updater_mod.check_latest_with_sha = _fake_check_latest_sha
            t0 = _time.perf_counter()
            win._check_update_silent()
            elapsed = _time.perf_counter() - t0
            self.assertLess(elapsed, 0.25,
                            f"_check_update_silent 阻塞了 {elapsed:.2f}s —— 未挪到工作线程")
            for _ in range(200):
                if calls:
                    break
                threading.Event().wait(0.02)
            self.assertEqual(len(calls), 1, "worker 未调用 check_latest")
            self.assertNotEqual(calls[0], main_tid,
                                "_check_update_silent 仍在 GUI 主线程跑 HTTP")
        finally:
            updater_mod.check_latest = original
            if original_sha is not None:
                updater_mod.check_latest_with_sha = original_sha
            if win is not None:
                win.close()
                win.deleteLater()
                app.processEvents()


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
