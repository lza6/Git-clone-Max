# -*- coding: utf-8 -*-
"""G58-2 镜像多备援：候选构造、凭据门禁、克隆回退、更新回退、测速排序。

设计要点（也是本文件的验收点）：
- 镜像只对 HTTPS 生效；SSH / 本地路径永不拼接。
- **有凭据时绝不用镜像**（第三方镜像能看到 Authorization 头）。
- 源站永远是最后候选：镜像全挂也必须能继续克隆。
- 克隆前用 `git ls-remote` 短超时探测，避免镜像没有该仓库时卡满 600s。
- 更新失败时可在镜像/源站之间切换，成功则把 origin 留在可用地址。
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

from gcm.app import mirror_probe, mirrors  # noqa: E402
from gcm.models import RepoSpec, SyncResult, SyncStatus  # noqa: E402


def _spec(url: str = "https://github.com/a/b.git") -> RepoSpec:
    return RepoSpec(owner="a", repo="b", url_https=url, folder_name="a__b")


class TestNormalize(unittest.TestCase):
    def test_prefix_list_accepts_str_list_and_text(self):
        self.assertEqual(mirrors.normalize_prefix_list("https://a/"),
                         ("https://a",))
        self.assertEqual(mirrors.normalize_prefix_list(
            ["https://a", "https://b/", "https://a"]),
            ("https://a", "https://b"))
        self.assertEqual(mirrors.normalize_prefix_list(
            "https://a, https://b，https://c"),
            ("https://a", "https://b", "https://c"))

    def test_mapping_normalizes_hosts(self):
        got = mirrors.normalize_mirror_prefix({
            "GitHub.com": "https://a", "www.gitlab.com": ["https://b", "https://c"]})
        self.assertEqual(got["github.com"], ("https://a",))
        self.assertEqual(got["gitlab.com"], ("https://b", "https://c"))

    def test_mirror_prefixes_user_then_presets(self):
        got = mirrors.mirror_prefixes_for("github.com", {"github.com": ["https://u"]})
        self.assertEqual(got[0], "https://u")
        self.assertIn("https://gh-proxy.com", got)
        self.assertEqual(got, tuple(dict.fromkeys(got)), "候选不应重复")

    def test_presets_only_for_github_and_can_be_disabled(self):
        self.assertEqual(
            mirrors.mirror_prefixes_for("gitlab.com", {}, use_presets=True), ())
        self.assertEqual(
            mirrors.mirror_prefixes_for("github.com", {}, use_presets=False), ())

    def test_wildcard_applies_to_all_hosts(self):
        got = mirrors.mirror_prefixes_for("example.com", {"*": "https://w"})
        self.assertEqual(got, ("https://w",))

    def test_build_urls_only_https(self):
        self.assertEqual(
            mirrors.build_mirror_urls("git@github.com:a/b.git", ("https://m",)), [])
        self.assertEqual(
            mirrors.build_mirror_urls("https://github.com/a/b", ("https://m",)),
            ["https://m/https://github.com/a/b"])
        self.assertEqual(
            mirrors.build_mirror_urls("https://github.com/a/b",
                                      ("https://m", "https://m/")),
            ["https://m/https://github.com/a/b"], "重复前缀应去重")

    def test_requires_credentials(self):
        self.assertTrue(mirrors.requires_credentials(
            "github.com", "tok", {}, {}))
        self.assertTrue(mirrors.requires_credentials(
            "gitlab.com", "", {"gitlab.com": "glpat"}, {}))
        self.assertTrue(mirrors.requires_credentials(
            "corp.example", "", {"corp.example": "x"}, {}))
        self.assertFalse(mirrors.requires_credentials(
            "corp.example", "", {}, {}))
        self.assertFalse(mirrors.requires_credentials(
            "github.com", "", {}, {}, has_ssh_key=True))


class TestServiceMirrorGating(unittest.TestCase):
    def _svc(self, **kw):
        from gcm.git.service import GitService
        d = Path(tempfile.mkdtemp(prefix="gcm_mirror_"))
        svc = GitService(d, **kw)
        svc._current_host = "github.com"
        svc._emit = lambda *a, **k: None
        return svc

    def test_mirror_allowed_without_credentials(self):
        svc = self._svc(mirror_prefix={"github.com": ["https://m"]})
        self.assertTrue(svc._mirror_allowed("https://github.com/a/b.git"))

    def test_mirror_forbidden_with_token(self):
        svc = self._svc(mirror_prefix={"github.com": ["https://m"]}, token="ghp_x")
        self.assertFalse(svc._mirror_allowed("https://github.com/a/b.git"),
                         "有令牌时必须直连源站，不能让镜像看到凭据")

    def test_mirror_forbidden_for_ssh(self):
        svc = self._svc(mirror_prefix={"github.com": ["https://m"]})
        self.assertFalse(svc._mirror_allowed("git@github.com:a/b.git"))

    def test_resolve_picks_first_reachable_and_skips_dead(self):
        svc = self._svc(mirror_prefix={"github.com": ["https://dead", "https://live"]},
                        mirror_use_presets=False)
        calls = []

        def fake_probe(url, timeout, cancelled=None):
            calls.append(url)
            return (url.startswith("https://live"), 100, "" if url.startswith("https://live") else "timeout")

        with mock.patch("gcm.git.service.probe_git_url", side_effect=fake_probe):
            got = svc._resolve_clone_url("https://github.com/a/b.git")
        self.assertEqual(got, "https://live/https://github.com/a/b.git")
        self.assertEqual(calls, ["https://dead/https://github.com/a/b.git",
                                 "https://live/https://github.com/a/b.git"])

    def test_resolve_falls_back_to_source_when_all_dead(self):
        svc = self._svc(mirror_prefix={"github.com": ["https://dead"]},
                        mirror_use_presets=False)
        with mock.patch("gcm.git.service.probe_git_url", return_value=(False, 5, "x")):
            got = svc._resolve_clone_url("https://github.com/a/b.git")
        self.assertEqual(got, "https://github.com/a/b.git")

    def test_resolve_prefers_last_good_mirror(self):
        svc = self._svc(mirror_prefix={"github.com": ["https://a", "https://b"]},
                        mirror_use_presets=False)
        svc._preferred_mirror = "https://b"
        order = []

        def fake_probe(url, timeout, cancelled=None):
            order.append(url)
            return True, 10, ""

        with mock.patch("gcm.git.service.probe_git_url", side_effect=fake_probe):
            got = svc._resolve_clone_url("https://github.com/x/y.git")
        self.assertTrue(got.startswith("https://b/"))
        self.assertEqual(order[0], "https://b/https://github.com/x/y.git")


class TestCloneFailover(unittest.TestCase):
    def test_clone_retries_source_after_mirror_failure(self):
        from gcm.git.service import GitService
        d = Path(tempfile.mkdtemp(prefix="gcm_clone_fo_"))
        svc = GitService(d, mirror_prefix={"github.com": ["https://m"]},
                         mirror_use_presets=False, mirror_fallback=True)
        svc._current_host = "github.com"
        svc._emit = lambda *a, **k: None
        spec = _spec()
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)
        calls = []

        def fake_run(cmd, cwd, on_line, **kw):
            url = cmd[-2]
            calls.append(url)
            return (0 if url == spec.url_https else 1), None

        with mock.patch("gcm.git.service.probe_git_url", return_value=(True, 20, "")), \
                mock.patch("gcm.git.service.run_git_ui", side_effect=fake_run), \
                mock.patch.object(svc, "_head_sha", return_value="abc1234"), \
                mock.patch.object(svc, "_safe_rmtree_partial", return_value=None):
            got = svc._clone(spec, res)
        self.assertEqual(got.status, SyncStatus.SUCCESS)
        self.assertEqual(calls, ["https://m/https://github.com/a/b.git",
                                 "https://github.com/a/b.git"],
                         "镜像失败后必须用源站重试一次")

    def test_clone_uses_source_when_credentials_present(self):
        from gcm.git.service import GitService
        d = Path(tempfile.mkdtemp(prefix="gcm_clone_cred_"))
        svc = GitService(d, mirror_prefix={"github.com": ["https://m"]},
                         mirror_use_presets=False, token="ghp_x")
        svc._current_host = "github.com"
        svc._emit = lambda *a, **k: None
        spec = _spec()
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)
        calls = []

        def fake_run(cmd, cwd, on_line, **kw):
            calls.append(cmd[-2])
            return 0, None

        with mock.patch("gcm.git.service.probe_git_url") as probe, \
                mock.patch("gcm.git.service.run_git_ui", side_effect=fake_run), \
                mock.patch.object(svc, "_head_sha", return_value="abc"), \
                mock.patch.object(svc, "_safe_rmtree_partial", return_value=None):
            svc._clone(spec, res)
        probe.assert_not_called()
        self.assertEqual(calls, [spec.url_https])

    def test_build_clone_cmd_honours_url_override(self):
        from gcm.git.service import GitService
        svc = GitService(Path(tempfile.mkdtemp(prefix="gcm_cmd_")))
        cmd = svc.build_clone_cmd(_spec(), url_override="https://m/https://github.com/a/b")
        self.assertEqual(cmd[-2], "https://m/https://github.com/a/b")


class TestUpdateFailover(unittest.TestCase):
    def test_fetch_switches_to_mirror_after_failure(self):
        from gcm.git.service import GitService
        d = Path(tempfile.mkdtemp(prefix="gcm_upd_"))
        repo = d / "a__b"
        (repo / ".git").mkdir(parents=True)
        svc = GitService(d, mirror_prefix={"github.com": ["https://m"]},
                         mirror_use_presets=False, mirror_fallback=True)
        svc._current_host = "github.com"
        svc._emit = lambda *a, **k: None
        spec = _spec()
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)
        fetch_calls = []

        def fake_run(cmd, cwd, on_line, **kw):
            if "fetch" in cmd:
                fetch_calls.append(cmd)
                return (1 if len(fetch_calls) == 1 else 0), None
            return 0, None

        set_urls = []

        with mock.patch.object(svc, "_repo_dir", return_value=repo), \
                mock.patch.object(svc, "_head_sha", return_value="abc"), \
                mock.patch.object(svc, "_local_branch", return_value="main"), \
                mock.patch.object(svc, "_count_new_commits", return_value=0), \
                mock.patch.object(svc, "_remote_head", return_value="abc"), \
                mock.patch("gcm.git.service._detect_conflict", return_value=(False, "")), \
                mock.patch.object(svc, "_get_origin_url",
                                  return_value="https://github.com/a/b.git"), \
                mock.patch.object(svc, "_set_origin_url",
                                  side_effect=lambda rd, u: set_urls.append(u) or True), \
                mock.patch("gcm.git.service.run_git_ui", side_effect=fake_run):
            got = svc._update(spec, res)
        self.assertEqual(len(fetch_calls), 2, "首次失败后应重试一次")
        self.assertEqual(set_urls, ["https://m/https://github.com/a/b.git"])
        self.assertEqual(got.status, SyncStatus.SUCCESS)

    def test_failover_candidates_include_source_last(self):
        from gcm.git.service import GitService
        svc = GitService(Path(tempfile.mkdtemp(prefix="gcm_cand_")),
                         mirror_prefix={"github.com": ["https://a", "https://b"]},
                         mirror_use_presets=False)
        svc._current_host = "github.com"
        got = svc._update_failover_candidates("https://github.com/x/y.git")
        self.assertEqual(got, [
            "https://a/https://github.com/x/y.git",
            "https://b/https://github.com/x/y.git",
            "https://github.com/x/y.git",
        ])

    def test_no_failover_with_token(self):
        from gcm.git.service import GitService
        svc = GitService(Path(tempfile.mkdtemp(prefix="gcm_cand2_")),
                         mirror_prefix={"github.com": ["https://a"]}, token="t")
        svc._current_host = "github.com"
        self.assertEqual(svc._update_failover_candidates("https://github.com/x/y.git"), [])


class TestProbe(unittest.TestCase):
    def setUp(self):
        mirror_probe.reset_cache_for_tests()

    def test_rank_orders_reachable_by_latency(self):
        def fake(url, timeout):
            if "slow" in url:
                return True, 900, ""
            if "fast" in url:
                return True, 100, ""
            return False, 5000, "timeout"

        results = mirror_probe.rank_mirrors(
            "https://github.com/a/b",
            ["https://dead", "https://slow", "https://fast"],
            runner=fake, include_source=False)
        self.assertEqual([r["url"].split("/")[2] for r in results],
                         ["fast", "slow", "dead"])
        self.assertEqual([r["ok"] for r in results], [True, True, False])

    def test_rank_includes_source_marked(self):
        results = mirror_probe.rank_mirrors(
            "https://github.com/a/b", ["https://m"],
            runner=lambda u, t: (True, 50, ""))
        self.assertTrue(results[-1]["is_source"])
        self.assertEqual(results[-1]["url"], "https://github.com/a/b")

    def test_cache_avoids_repeat_probe(self):
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return True, 10, ""

        mirror_probe.probe_git_url("https://m/x", runner=fake)
        mirror_probe.probe_git_url("https://m/x", runner=fake)
        self.assertEqual(len(calls), 1, "10 分钟内同 URL 不应重复探测")

    def test_cancelled_short_circuits(self):
        calls = []
        ok, _ms, err = mirror_probe.probe_git_url(
            "https://m/x", runner=lambda u, t: calls.append(u) or (True, 1, ""),
            cancelled=lambda: True)
        self.assertFalse(ok)
        self.assertEqual(err, "已取消")
        self.assertEqual(calls, [])

    def test_default_runner_treats_rc2_as_reachable(self):
        proc = mock.Mock(returncode=2, stdout="", stderr="")
        with mock.patch("gcm.app.mirror_probe.subprocess.run", return_value=proc):
            ok, _ms, _err = mirror_probe.default_runner("https://m/x", 3)
        self.assertTrue(ok, "空仓库（无 ref）也是可达的")

    def test_default_runner_timeout_is_failure(self):
        import subprocess as _sp
        with mock.patch("gcm.app.mirror_probe.subprocess.run",
                        side_effect=_sp.TimeoutExpired("git", 3)):
            ok, _ms, err = mirror_probe.default_runner("https://m/x", 3)
        self.assertFalse(ok)
        self.assertIn("超时", err)


class TestSettingsRoundTrip(unittest.TestCase):
    def test_multi_mirror_persists(self):
        from gcm.db.settings import Settings, SettingsStore
        d = Path(tempfile.mkdtemp(prefix="gcm_mset_"))
        st = SettingsStore(d / "s.json")
        s = st.load()
        s.mirror_prefix = {"github.com": ["https://a", "https://b"]}
        s.mirror_use_presets = False
        s.mirror_fallback = True
        st.save(s)
        got = st.load()
        self.assertEqual(got.mirror_prefix["github.com"], ["https://a", "https://b"])
        self.assertFalse(got.mirror_use_presets)
        self.assertTrue(got.mirror_fallback)
        self.assertTrue(Settings().mirror_use_presets, "默认应启用内置镜像")
        self.assertTrue(Settings().mirror_fallback, "默认应回退源站")


class TestMirrorUi(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication
        self._app = QApplication.instance() or QApplication([])
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_mui_"))
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        self.store = SettingsStore(self.tmp / "s.json")
        self.win = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                              settings=self.store)
        self.win._check_update_silent = lambda *a, **k: None
        self.win._maybe_show_onboarding = lambda *a, **k: None
        self.addCleanup(self.win.close)

    def test_parse_mirror_text_multi(self):
        got = self.win.settings_panel._parse_mirror_text(
            "github.com=https://a, https://b\nGitLab.com=https://c")
        self.assertEqual(got["github.com"], ["https://a", "https://b"])
        self.assertEqual(got["gitlab.com"], "https://c")

    def test_save_mirror_roundtrip(self):
        self.win.edit_mirror.setText("github.com=https://a, https://b")
        self.win.settings_panel._save_mirror()
        self.assertEqual(self.store.load().mirror_prefix["github.com"],
                         ["https://a", "https://b"])

    def test_probe_result_written_in_latency_order(self):
        sample = "https://github.com/octocat/Hello-World"
        results = [
            {"url": f"https://slow/{sample}", "ok": True, "ms": 900, "error": "", "is_source": False},
            {"url": f"https://fast/{sample}", "ok": True, "ms": 100, "error": "", "is_source": False},
            {"url": sample, "ok": True, "ms": 50, "error": "", "is_source": True},
        ]
        self.win.settings_panel._on_mirror_probe_done(results, sample)
        text = self.win.edit_mirror.text()
        self.assertEqual(text, "github.com=https://fast, https://slow")
        self.assertEqual(self.store.load().mirror_prefix["github.com"],
                         ["https://fast", "https://slow"])

    def test_probe_with_no_reachable_mirror_keeps_config(self):
        self.win.edit_mirror.setText("github.com=https://keep")
        self.win.settings_panel._save_mirror()
        sample = "https://github.com/octocat/Hello-World"
        self.win.settings_panel._on_mirror_probe_done(
            [{"url": f"https://dead/{sample}", "ok": False, "ms": 8000,
              "error": "timeout", "is_source": False}], sample)
        self.assertEqual(self.win.edit_mirror.text(), "github.com=https://keep")

    def test_toggles_persist(self):
        self.win.settings_panel._save_mirror_presets(False)
        self.win.settings_panel._save_mirror_fallback(False)
        s = self.store.load()
        self.assertFalse(s.mirror_use_presets)
        self.assertFalse(s.mirror_fallback)


class TestRealGitE2E(unittest.TestCase):
    """真实 git 冒烟：启用镜像配置不得影响本地/非 HTTPS 仓库。"""

    def _git(self, *args, cwd=None):
        import subprocess
        subprocess.run(["git", *args], cwd=cwd, check=True,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_mirror_e2e_"))
        src = self.tmp / "src"
        src.mkdir()
        self._git("init", "-b", "main", cwd=src)
        self._git("config", "user.email", "t@example.com", cwd=src)
        self._git("config", "user.name", "t", cwd=src)
        (src / "a.txt").write_text("one", encoding="utf-8")
        self._git("add", ".", cwd=src)
        self._git("commit", "-m", "init", cwd=src)
        self.bare = self.tmp / "origin.git"
        self._git("clone", "--bare", str(src), str(self.bare))
        self.src = src

    def _url(self):
        return self.bare.resolve().as_uri()

    def test_probe_real_local_repo(self):
        ok, ms, err = mirror_probe.probe_git_url(self._url(), timeout=10,
                                                 use_cache=False)
        self.assertTrue(ok, f"real ls-remote 应成功：{err}（{ms}ms）")

    def test_sync_with_mirror_config_uses_source_for_file_url(self):
        from gcm.git.service import GitService
        svc = GitService(self.tmp / "clones",
                         mirror_prefix={"github.com": ["https://dead"]},
                         mirror_use_presets=True, mirror_fallback=True)
        spec = RepoSpec(owner="o", repo="r", url_https=self._url(),
                        folder_name="o__r")
        res = svc.sync(spec)
        self.assertEqual(res.status, SyncStatus.SUCCESS, res.message)
        self.assertTrue((Path(res.path) / ".git").exists())

        # 增量更新同样不应被镜像影响
        (self.src / "b.txt").write_text("two", encoding="utf-8")
        self._git("add", ".", cwd=self.src)
        self._git("commit", "-m", "second", cwd=self.src)
        self._git("push", str(self.bare), "main", cwd=self.src)
        res2 = svc.sync(spec)
        self.assertEqual(res2.status, SyncStatus.SUCCESS, res2.message)
        self.assertEqual((Path(res2.path) / "b.txt").read_text(encoding="utf-8"), "two")


class TestProbeConcurrency(unittest.TestCase):
    def setUp(self):
        mirror_probe.reset_cache_for_tests()

    def test_concurrent_probe_is_safe(self):
        import threading
        results = []
        lock = threading.Lock()

        def fake(url, timeout):
            import time as _t
            _t.sleep(0.01)
            return True, 12, ""

        def worker():
            got = mirror_probe.probe_git_url("https://m/concurrent", runner=fake)
            with lock:
                results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(len(results), 12)
        self.assertTrue(all(r[0] for r in results))


if __name__ == "__main__":
    unittest.main(verbosity=2)
