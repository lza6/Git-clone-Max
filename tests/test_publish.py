# -*- coding: utf-8 -*-
"""publish_release.py 的发布流程测试（全 mock，不触网、不真实上传/删除）。

用 unittest.mock 替换 publish_release 的 `github` 模块（通过 sys.modules 注入影子
模块，使 `from github import Auth, Github` 取到 mock），替换 _get_token / urllib，
并构造真实临时 DIST 产物；直接调用 publish_release.main() 断言行为与调用记录。
"""
from __future__ import annotations

import io
import sys
import tempfile
import time as real_time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import gcm  # noqa: E402
import publish_release as pr  # noqa: E402


class _Asset:
    """远端附件（GitReleaseAsset 的替身）。"""

    def __init__(self, name, size, url="https://example.com/assets/x"):
        self.name = name
        self.size = size
        self.browser_download_url = url
        self.delete_asset = mock.Mock(return_value=True)


class _Release:
    """远端 Release（GitRelease 的替身）。"""

    def __init__(self, rid=1, assets=None):
        self.id = rid
        self.html_url = "https://github.com/lza6/Git-clone-Max/releases/tag/x"
        self._assets = list(assets or [])
        self._uploaded = []
        self.delete_asset = mock.Mock(return_value=True)
        self.get_assets = mock.Mock(side_effect=lambda: list(self._assets))
        self.upload_asset = mock.Mock(side_effect=self._do_upload)

    def _do_upload(self, path, content_type=None, timeout=None, **kw):
        a = _Asset(pr.ARTIFACT_NAME, Path(path).stat().st_size)
        a.id = 42
        self._uploaded.append(a)
        return a


class _Repo:
    def __init__(self, existing=None):
        self._rel = existing
        self.get_release = mock.Mock(side_effect=self._get_release)
        self.create_git_release = mock.Mock(side_effect=self._create)

    def _get_release(self, tag):
        if self._rel is None:
            raise Exception("404 {“message”}: Not Found")
        return self._rel

    def _create(self, tag, name, message, draft=False, prerelease=False,
                **kwargs):
        self._rel = _Release(rid=99)
        return self._rel


class _Github:
    def __init__(self, repo):
        self._repo = repo
        self.get_repo = mock.Mock(return_value=repo)


def _make_fake_time():
    """真实单调时钟 + mock sleep，避免超时兜底误触发也不真睡。"""
    fake_time = types.ModuleType("fake_time")
    fake_time.monotonic = real_time.monotonic
    fake_time.sleep = mock.Mock(return_value=None)
    fake_time.perf_counter = real_time.perf_counter
    return fake_time


class PublishTestCase(unittest.TestCase):
    """公共 setUp：临时 DIST 产物 + mock github 模块。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dist = self.tmp / pr.ARTIFACT_NAME
        self.dist.write_bytes(b"EXE")  # 3 字节真实文件

        self.rel = _Release(rid=1)
        self.repo = _Repo(existing=self.rel)
        self.gh = _Github(self.repo)

        # 注入影子 github 模块：让 `from github import Auth, Github` 取到 mock
        shadow_github = types.ModuleType("github")
        shadow_github.Auth = types.SimpleNamespace(
            Token=lambda t: f"TOKEN:{t[:4]}")
        shadow_github.Github = mock.Mock(return_value=self.gh)
        self._shadow_saved = sys.modules.get("github")
        sys.modules["github"] = shadow_github
        self.addCleanup(self._restore_github)

        self._time_saved = pr.time
        pr.time = _make_fake_time()
        self.addCleanup(self._restore_time)

        # G45-8：版本一致性校验基于 gcm.__version__/__changelog__，
        # setUp 统一 patch 成与用例 tag 一致（v4.2.0），旧用例无需改 tag。
        self._patchers = [
            mock.patch.object(gcm, "__version__", "4.2.0"),
            mock.patch.object(gcm, "__changelog__", ("4.2.0: G45-8 测试条目",)),
            mock.patch.object(pr, "_get_token", return_value="t" * 40),
            # urllib 保持真实模块：服务端下载校验读 asset.browser_download_url
            # （example.com 替身 URL），真实 urlopen 失败仅 [WARN] 跳过不影响断言。
            # hashlib 保持真实模块：BODY 模板现在用 hashlib 计算产物 sha256
            # （G28-4 校验行），mock 掉会让期望 message 与实际不一致。
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop)

        # 先保存原值再替换，tearDown 还原，避免模块级常量泄漏污染后续测试模块
        # （test_update_auto 的 pr.BODY 在 import 期用真实 dist 冻结，这里不还原
        #   会让后续 _build_body() 读到残留临时假产物，导致顺序依赖失败）
        self._pr_dist_saved = pr.DIST
        self._pr_zip_saved = pr.PORTABLE_ZIP
        pr.DIST = self.dist  # 脚本常量，测试内赋值真实 Path
        # G23-7 便携包常量：测试内赋值临时路径且不生成 zip（隔离主产物断言）
        self.portable = self.tmp / pr.PORTABLE_NAME
        pr.PORTABLE_ZIP = self.portable
        self.addCleanup(self._restore_pr_paths)

    def _restore_github(self):
        if self._shadow_saved is not None:
            sys.modules["github"] = self._shadow_saved
        else:
            sys.modules.pop("github", None)

    def _restore_time(self):
        pr.time = self._time_saved

    def _restore_pr_paths(self):
        pr.DIST = self._pr_dist_saved
        pr.PORTABLE_ZIP = self._pr_zip_saved

    def _stop(self):
        for p in self._patchers:
            p.stop()

    def _run_main(self, *argv):
        sys.argv = ["publish_release.py", *argv]
        return pr.main()


class TestIdempotentSkip(PublishTestCase):
    """用例 1：Release 存在且同名附件 size == 本地 → SKIP，不删不传。"""

    def test_idempotent_skip_when_asset_matches(self):
        self.rel._assets = [_Asset(pr.ARTIFACT_NAME, self.dist.stat().st_size)]
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            rc = self._run_main("--tag", "v4.2.0")
        self.assertEqual(rc, 0)
        self.rel.delete_asset.assert_not_called()
        self.rel.upload_asset.assert_not_called()
        self.assertIn("SKIP", buf.getvalue())


class TestReuploadWhenSizeDiffers(PublishTestCase):
    """用例 2：同名附件 size 与本地不同 → 删旧 + 传新，return 0。"""

    def test_reupload_when_size_differs(self):
        self.rel._assets = [_Asset(pr.ARTIFACT_NAME, 999)]
        asset = self.rel._assets[0]
        rc = self._run_main("--tag", "v4.2.0")
        self.assertEqual(rc, 0)
        asset.delete_asset.assert_called_once()
        self.rel.upload_asset.assert_called_once()
        self.assertEqual(len(self.rel._uploaded), 1)


class TestDryRunNoMutation(PublishTestCase):
    """用例 3：--dry-run 且 Release 存在 → 不删不传，return 0。"""

    def test_dry_run_no_mutation(self):
        self.rel._assets = [_Asset(pr.ARTIFACT_NAME, self.dist.stat().st_size)]
        rc = self._run_main("--dry-run", "--tag", "v4.2.0")
        self.assertEqual(rc, 0)
        self.rel.delete_asset.assert_not_called()
        self.rel.upload_asset.assert_not_called()


class TestNewReleaseCreated(PublishTestCase):
    """用例 4：Release 不存在 → create_git_release + upload_asset 被调。"""

    def test_new_release_created(self):
        self.repo._rel = None
        rc = self._run_main("--tag", "v4.2.0")
        self.assertEqual(rc, 0)
        self.repo.create_git_release.assert_called_once()
        call = self.repo.create_git_release.call_args
        kwargs = call.kwargs
        if not kwargs:
            kwargs = {
                "tag": call.args[0], "name": call.args[1],
                "message": call.args[2],
                "draft": call.args[3] if len(call.args) > 3 else False,
                "prerelease": call.args[4] if len(call.args) > 4 else False,
            }
        self.assertEqual(kwargs.get("tag"), "v4.2.0")
        self.assertEqual(kwargs.get("name"), "Git-clone-Max v4.2.0")
        msg = kwargs.get("message", "")
        self.assertIn("4.2.0", msg, "body 应含当前版本")
        self.assertNotIn("{version}", msg, "占位符应被替换")
        self.assertNotIn("{sha256_exe}", msg)
        self.assertNotIn("{sha256_zip}", msg)
        self.assertEqual(len(self.repo._rel._uploaded), 1)


class TestNetworkRetryThenFail(PublishTestCase):
    """用例 5：repo.get_release 连续抛异常 → 重试 3 次后仍失败 → 非 0 不崩。"""

    def test_network_retry_then_fail(self):
        self.repo.get_release.side_effect = [
            Exception("boom1"), Exception("boom2"), Exception("boom3")]
        rc = self._run_main("--tag", "v4.2.0")
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.repo.get_release.call_count, 3)


class TestEnsureRemoteTag(unittest.TestCase):
    """publish_release._ensure_remote_tag：缺失补建 / 已有跳过 / 默认分支回退 / 422 竞态。"""

    @staticmethod
    def _fake_repo(ref_exists=False, create_ok=True):
        from github import GithubException

        class _Commit:
            sha = "deadbeef"

        class _Branch:
            commit = _Commit()

        class _Repo:
            default_branch = "main"
            created = []

            def get_git_ref(self, ref):
                if ref_exists:
                    return object()
                raise GithubException(404, {})

            def get_branch(self, name):
                return _Branch()

            def create_git_ref(self, ref, sha):
                self.created.append((ref, sha))
                if not create_ok:
                    raise GithubException(422, {})

        return _Repo()

    def test_creates_when_missing(self):
        repo = self._fake_repo(ref_exists=False)
        pr._ensure_remote_tag(repo, "v4.2.0", "abc123")
        self.assertEqual(repo.created, [("refs/tags/v4.2.0", "abc123")])

    def test_skips_when_exists(self):
        repo = self._fake_repo(ref_exists=True)
        pr._ensure_remote_tag(repo, "v4.2.0", "abc123")
        self.assertEqual(repo.created, [])

    def test_uses_default_branch_when_no_sha(self):
        repo = self._fake_repo(ref_exists=False)
        pr._ensure_remote_tag(repo, "v4.2.0", None)
        self.assertEqual(repo.created, [("refs/tags/v4.2.0", "deadbeef")])

    def test_race_422_treated_as_ok(self):
        repo = self._fake_repo(ref_exists=False, create_ok=False)
        pr._ensure_remote_tag(repo, "v4.2.0", "abc123")  # 不抛异常
        self.assertEqual(repo.created, [("refs/tags/v4.2.0", "abc123")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
