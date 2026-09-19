# -*- coding: utf-8 -*-
"""G49-1 轻量 i18n 测试：tr / set_language / zh 默认 / en / 缺失键回退中文。"""
from __future__ import annotations
import unittest

from gcm.i18n import tr, set_language, current_language


class TestI18n(unittest.TestCase):
    def tearDown(self):
        set_language("zh")  # 不污染其它测试

    def test_default_zh(self):
        self.assertEqual(current_language(), "zh")
        self.assertEqual(tr("下载中心"), "下载中心")
        self.assertEqual(tr("检查更新"), "检查更新")

    def test_en_switch(self):
        set_language("en")
        self.assertEqual(current_language(), "en")
        self.assertEqual(tr("下载中心"), "Download Center")
        self.assertEqual(tr("检查更新"), "Check for updates")
        self.assertEqual(tr("清空日志"), "Clear log")

    def test_missing_key_falls_back_chinese(self):
        set_language("en")
        self.assertEqual(tr("（不存在的键）"), "（不存在的键）")
        set_language("zh")
        self.assertEqual(tr("（不存在的键）"), "（不存在的键）")

    def test_invalid_language_falls_back(self):
        set_language("fr")
        self.assertEqual(current_language(), "zh")
        self.assertEqual(tr("下载中心"), "下载中心")

    def test_en_dict_fully_covers_zh(self):
        from gcm.i18n.zh import ZH
        from gcm.i18n.en import EN
        for k in ZH:
            self.assertIn(k, EN, f"EN 缺键：{k!r}")
            if k != "English":  # 语言名保持原文（中文/English 各自显示）
                self.assertNotEqual(EN[k], k, f"EN 键未翻译（仍是中文）：{k!r}")
        self.assertGreaterEqual(len(ZH), 140)
