# -*- coding: utf-8 -*-
"""自更新版本比对 + GitHub Release 查询单测。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app import updater
from gcm.app import updater as _m  # 别名避免命名冲突


class FakeFetcher:
    def __init__(self, payload):
        self.payload = payload

    def __call__(self):
        return json.dumps(self.payload)


def _release(tag, exe_url="https://example.com/Git-clone-Max.exe"):
    return {"tag_name": tag,
            "assets": [{"name": "Git-clone-Max.exe",
                        "browser_download_url": exe_url}],
            "html_url": "https://github.com/lza6/Git-clone-Max/releases/latest"}


class TestUpdater(unittest.TestCase):
    def test_parse_versions(self):
        self.assertGreater(updater._parse_version("v2.1.0"), updater._parse_version("v2.0.0"))
        self.assertEqual(updater._parse_version("v2.0.0"), updater._parse_version("2.0.0"))
        self.assertLess(updater._parse_version("v2.0.0-rc1"), updater._parse_version("v2.0.0"))

    def test_newer_found_with_url(self):
        cur = updater.__version__
        # 服务端返回一个高于当前版的 tag → 应报告有新版且带 exe 下载地址
        fake = FakeFetcher(_release("v999.0.0"))
        has_new, ver, url, err = updater.check_latest(fetcher=fake)
        self.assertTrue(has_new)
        self.assertEqual(ver, "v999.0.0")
        self.assertIn(".exe", url)
        self.assertEqual(err, "")

    def test_same_version_means_no_update(self):
        fake = FakeFetcher(_release("v" + updater.__version__.lstrip("v")))
        has_new, ver, url, err = updater.check_latest(fetcher=fake)
        self.assertFalse(has_new)

    def test_older_release_no_update(self):
        fake = FakeFetcher(_release("v0.1.0"))
        has_new, _, _, err = updater.check_latest(fetcher=fake)
        self.assertFalse(has_new)

    def test_fetch_error_reports(self):
        def boom():
            raise RuntimeError("network down")
        has_new, ver, url, err = updater.check_latest(fetcher=boom)
        self.assertFalse(has_new)
        self.assertIn("检查更新失败", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)