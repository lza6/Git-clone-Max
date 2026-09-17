"""G38 网络与同步前沿 单测（纯逻辑层，不触网）。

覆盖：
- G38-2 apply_mirror_prefix / GitService._mirror_url（HTTPS 拼接、SSH 不拼、无前缀不拼）
- G38-3 _precheck_reachable（可达/不可达/异常）
- G38-4 single_branch 注入到 clone 命令
- G38-5 depth1 保命模式降级链（treeless 失败 → depth1 成功）
- G38-6 force_ipv4 → http.version=HTTP/1.1 env 注入
- G38-7 常用主机白名单 is_supported_host
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.url_lib import apply_mirror_prefix, is_supported_host  # noqa: E402
from gcm.git.service import GitService  # noqa: E402
from gcm.models import RepoSpec  # noqa: E402


class TestMirrorPrefix(unittest.TestCase):
    """G38-2 镜像前缀纯函数 + service 拼接。"""

    def test_https_prefixed(self):
        self.assertEqual(
            apply_mirror_prefix("https://github.com/a/b", "https://ghproxy.com"),
            "https://ghproxy.com/https://github.com/a/b")

    def test_ssh_untouched(self):
        self.assertEqual(apply_mirror_prefix("git@github.com:a/b.git", "https://p.com"),
                         "git@github.com:a/b.git")

    def test_empty_prefix_untouched(self):
        self.assertEqual(apply_mirror_prefix("https://github.com/a/b", ""),
                         "https://github.com/a/b")

    def test_service_mirror_url_by_host(self):
        svc = GitService(Path(tempfile.mkdtemp()),
                         mirror_prefix={"github.com": "https://ghproxy.com/"})
        svc._current_host = "github.com"
        self.assertEqual(svc._mirror_url("https://github.com/a/b.git"),
                         "https://ghproxy.com/https://github.com/a/b.git")

    def test_service_mirror_url_no_host_entry(self):
        svc = GitService(Path(tempfile.mkdtemp()),
                         mirror_prefix={"github.com": "https://ghproxy.com"})
        svc._current_host = "gitlab.com"
        self.assertEqual(svc._mirror_url("https://gitlab.com/a/b.git"),
                         "https://gitlab.com/a/b.git")


class TestPrecheck(unittest.TestCase):
    """G38-3 远端可达性预检。"""

    def test_reachable_ok(self):
        svc = GitService(Path(tempfile.mkdtemp()))
        proc = mock.Mock(returncode=0, stdout="abc\tHEAD\n", stderr="")
        with mock.patch("subprocess.run", return_value=proc):
            ok, err = svc._precheck_reachable("https://github.com/a/b.git")
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_unreachable_returns_error(self):
        svc = GitService(Path(tempfile.mkdtemp()))
        proc = mock.Mock(returncode=128, stdout="", stderr="fatal: could not resolve host")
        with mock.patch("subprocess.run", return_value=proc):
            ok, err = svc._precheck_reachable("https://nope.invalid/a/b.git")
        self.assertFalse(ok)
        self.assertIn("could not resolve host", err)

    def test_exception_safe(self):
        svc = GitService(Path(tempfile.mkdtemp()))
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            ok, err = svc._precheck_reachable("https://x/y")
        self.assertFalse(ok)
        self.assertIn("boom", err)


class TestSingleBranchAndFallback(unittest.TestCase):
    """G38-4 单分支 / G38-5 depth1 保命模式。"""

    def _svc(self, **kw):
        return GitService(Path(tempfile.mkdtemp()), **kw)

    def test_single_branch_flag_default_off(self):
        svc = self._svc()
        self.assertFalse(svc.single_branch)

    def test_single_branch_adds_flags(self):
        """single_branch + fetch_depth → clone 命令带 --depth/--single-branch。"""
        svc = self._svc(fetch_depth=3, single_branch=True)
        svc._current_host = "github.com"
        spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b.git",
                        folder_name="a__b")
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured.setdefault("cmds", []).append(list(cmd))
            return (0, "100%")

        with mock.patch("gcm.git.service.run_git_ui", side_effect=fake_run):
            svc._clone(spec, mock.Mock(status=None, action=None, message="",
                                       detail="", head_sha="", remote_sha="",
                                       commits=0, path="", ended=0.0, started=0.0))
        first = captured["cmds"][0]
        self.assertIn("--depth=3", first)
        self.assertIn("--single-branch", first)

    def test_fallback_depth1_after_treeless(self):
        """treeless 失败 → depth1 单分支（含提示文案）。"""
        svc = self._svc()
        svc._current_host = "github.com"
        spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b.git",
                        folder_name="a__b")
        calls = []
        warns = []

        def fake_run(cmd, *a, **kw):
            calls.append(list(cmd))
            # 前两次失败（常规 + treeless），第三次 depth1 成功
            return (0 if "--depth=1" in " ".join(cmd) else 128, "")

        res = mock.Mock(status=None, action=None, message="", detail="",
                        head_sha="HEAD", remote_sha="", commits=0, path="",
                        ended=0.0, started=0.0)
        with mock.patch("gcm.git.service.run_git_ui", side_effect=fake_run), \
                mock.patch("gcm.git.service._is_networkish_error", return_value=True), \
                mock.patch.object(svc, "_emit", side_effect=lambda t, lv="info": warns.append(t)), \
                mock.patch.object(svc, "_safe_rmtree_partial"), \
                mock.patch.object(svc, "_head_sha", return_value="HEAD"), \
                mock.patch.object(svc, "_repo_dir", return_value=Path(tempfile.mkdtemp()) / "x"):
            svc._clone(spec, res)
        joined = [" ".join(c) for c in calls]
        self.assertTrue(any("--depth=1" in j and "--single-branch" in j for j in joined))
        self.assertTrue(any("depth1" in w or "保命" in w for w in warns))


class TestForceIpv4(unittest.TestCase):
    """G38-6 强制 HTTP/1.1。"""

    def test_force_ipv4_injects_http_version(self):
        svc = GitService(Path(tempfile.mkdtemp()), force_ipv4=True)
        svc._current_host = "github.com"
        env = svc._env()
        n = int(env.get("GIT_CONFIG_COUNT", 0))
        keys = [env.get(f"GIT_CONFIG_KEY_{i}") for i in range(n)]
        self.assertIn("http.version", keys)
        idx = keys.index("http.version")
        self.assertEqual(env.get(f"GIT_CONFIG_VALUE_{idx}"), "HTTP/1.1")

    def test_force_ipv4_off_absent(self):
        svc = GitService(Path(tempfile.mkdtemp()))
        svc._current_host = "github.com"
        env = svc._env() or {}
        n = int(env.get("GIT_CONFIG_COUNT", 0) or 0)
        keys = [env.get(f"GIT_CONFIG_KEY_{i}") for i in range(n)]
        self.assertNotIn("http.version", keys)


class TestCustomHosts(unittest.TestCase):
    """G38-7 常用主机白名单。"""

    def test_builtin_hosts_supported(self):
        for h in ("github.com", "gitlab.com", "gitee.com", "codeberg.org",
                  "bitbucket.org", "git.sr.ht"):
            self.assertTrue(is_supported_host(h), h)

    def test_unknown_host_unsupported(self):
        self.assertFalse(is_supported_host("mygit.internal.corp"))
        self.assertFalse(is_supported_host(""))

    def test_custom_host_from_settings(self):
        """settings.json custom_hosts 登记后受支持（临时 GCM_DATA_DIR）。"""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "settings.json").write_text(
            '{"custom_hosts": ["mygit.example.com"]}', encoding="utf-8")
        with mock.patch.dict(os.environ, {"GCM_DATA_DIR": str(tmp)}):
            self.assertTrue(is_supported_host("mygit.example.com"))
        # 未登记的仍不受支持
        self.assertFalse(is_supported_host("other.example.com"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
