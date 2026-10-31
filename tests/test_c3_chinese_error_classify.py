# -*- coding: utf-8 -*-
"""C-3 / A5-1 补漏守卫：中文生产文案必须被 classify_error 正确分类。

`git/service._classify_failure` 产出的中文平台限制/目录占用/检出失败文案，
此前全部落入 other（三段式提示文不对题、统计失真）。补 `_PLATFORM_KEYWORDS`
中文词后应归 platform。中文网络文案（C-2 伴生）已由 `_NETWORK_CN` 覆盖，一并锁定。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.errors import CAT_LOCAL, CAT_NETWORK, CAT_PLATFORM, classify_error


class TestChineseProductionMessages(unittest.TestCase):
    """生产 message 全部是中文（git/service.py:122-145），分类必须跟上。"""

    def test_platform_limit_cn(self):
        for msg in ("仓库含 Windows 不允许的文件名，无法检出",
                    "检出工作区失败（平台/文件系统限制）"):
            info = classify_error(msg)
            self.assertEqual(info.category, CAT_PLATFORM, msg)
            self.assertFalse(info.retryable)

    def test_dir_exists_cn_is_platform_or_local_not_other(self):
        info = classify_error("目标目录已存在（重复提交或残留）")
        self.assertEqual(info.category, CAT_PLATFORM,
                         "「已存在」应命中平台类（若分类漂移请显式更新本断言，勿放宽")

    def test_network_cn_retryable(self):
        info = classify_error("网络连接失败（可自动重试）")
        self.assertEqual(info.category, CAT_NETWORK)
        self.assertTrue(info.retryable)

    def test_unknown_still_other(self):
        info = classify_error("完全无法理解的错误xyz")
        self.assertEqual(info.category, "other")


if __name__ == "__main__":
    unittest.main(verbosity=2)
