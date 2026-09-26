"""G59-7 settings IM webhook 字段：默认值 / 持久化 / token 加密落盘。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gcm.db.settings import Settings, SettingsStore


class TestImAlertSettings(unittest.TestCase):
    def test_defaults(self):
        s = Settings()
        self.assertEqual(s.alert_im_channel, "")
        self.assertEqual(s.alert_im_url, "")
        self.assertEqual(s.alert_im_token, "")

    def test_roundtrip_encrypted_token(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "s.json")
            s = Settings()
            s.alert_im_channel = "dingtalk"
            s.alert_im_url = "https://oapi.dingtalk.com/robot/send?access_token=abc"
            s.alert_im_token = "secret-im-token-456"
            store.save(s)
            raw = (Path(d) / "s.json").read_text(encoding="utf-8")
            self.assertNotIn("secret-im-token-456", raw)  # 绝不明文
            s2 = store.load()
            self.assertEqual(s2.alert_im_channel, "dingtalk")
            self.assertEqual(s2.alert_im_url, "https://oapi.dingtalk.com/robot/send?access_token=abc")
            self.assertEqual(s2.alert_im_token, "secret-im-token-456")


if __name__ == "__main__":
    unittest.main()