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

        self._patchers = [
            mock.patch.object(pr, "_get_token", return_value="t" * 40),
            mock.patch.object(pr, "urllib", mock.MagicMock()),
            mock.patch.object(pr, "hashlib", mock.MagicMock()),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop)

        pr.DIST = self.dist  # 脚本常量，测试内赋值真实 Path

    def _restore_github(self):
        if self._shadow_saved is not None:
            sys.modules["github"] = self._shadow_saved
        else:
            sys.modules.pop("github", None)

    def _restore_time(self):
        pr.time = self._time_saved

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
        self.repo.create_git_release.assert_called_once_with(
            tag="v4.2.0", name="Git-clone-Max v4.2.0",
            message=pr.BODY, draft=False, prerelease=False)
        self.assertEqual(len(self.repo._rel._uploaded), 1)


class TestNetworkRetryThenFail(PublishTestCase):
    """用例 5：repo.get_release 连续抛异常 → 重试 3 次后仍失败 → 非 0 不崩。"""

    def test_network_retry_then_fail(self):
        self.repo.get_release.side_effect = [
            Exception("boom1"), Exception("boom2"), Exception("boom3")]
        rc = self._run_main("--tag", "v4.2.0")
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.repo.get_release.call_count, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)