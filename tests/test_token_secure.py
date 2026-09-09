# -*- coding: utf-8 -*-
"""G06-1 token 安全存储测试：加密/解密/损坏兜底/空值（对应指南 G06-1）。

Windows DPAPI / macOS Keychain / Linux keyring 加密；无可用后端时降级为
「掩码存储」并在日志注明。目标：settings.json 中 token 永不明文。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.settings import SettingsStore, _encrypt_token, _decrypt_token, _mask_token


class TestTokenEncryption(unittest.TestCase):
    """加密/解密/掩码单测（纯函数，无平台依赖）。"""

    def test_roundtrip_encrypt_decrypt(self):
        tok = "ghp_0123456789abcdef"
        enc = _encrypt_token(tok)
        # 加密结果不是明文
        self.assertNotIn(tok, str(enc))
        self.assertEqual(_decrypt_token(enc), tok)

    def test_empty_token_returns_empty(self):
        self.assertEqual(_encrypt_token(""), "")
        self.assertEqual(_decrypt_token(""), "")

    def test_mask(self):
        self.assertEqual(_mask_token(""), "")
        self.assertEqual(_mask_token("abc123"), "abc12*")  # 保留前5位
        self.assertEqual(_mask_token("abcdef123456"), "abcde*******")  # 12位 → 5+7星


class TestSettingsTokenSecure(unittest.TestCase):
    """SettingsStore 落盘 token 必须加密；读回能解出原始值。"""

    def test_token_not_plaintext_on_disk(self):
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "settings.json")
        s = st.load()
        s.token = "ghp_SECRET_123"
        st.save(s)
        raw = json.loads((d / "settings.json").read_text(encoding="utf-8"))
        # token 字段必须是密文（不含明文子串）
        self.assertNotIn("ghp_SECRET_123", str(raw.get("token")))

    def test_token_roundtrip_load(self):
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "settings.json")
        s = st.load()
        s.token = "ghp_SECRET_456"
        st.save(s)
        s2 = st.load()
        self.assertEqual(s2.token, "ghp_SECRET_456")

    def test_token_empty_stays_empty(self):
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "settings.json")
        s = st.load()
        s.token = ""
        st.save(s)
        raw = json.loads((d / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(raw.get("token"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSettingsBackupRestore(unittest.TestCase):
    """G06-8 settings 备份与损坏恢复。"""

    def test_backup_created_on_save(self):
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "settings.json")
        s = st.load()
        s.concurrency = 5
        st.save(s)
        # 再保存一次 → 覆盖前备份存在
        s.concurrency = 6
        st.save(s)
        bak = d / "settings.json.bak"
        self.assertTrue(bak.exists(), "save 时应有 .bak 备份")

    def test_corrupt_main_restores_from_backup(self):
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "settings.json")
        s = st.load()
        s.concurrency = 9
        st.save(s)          # 第 1 次保存：主文件=9，无备份
        st.save(s)          # 第 2 次保存：备份上一版主文件(=9)，主文件仍=9
        # 损坏主文件
        (d / "settings.json").write_text("{ corrupt !!", encoding="utf-8")
        s2 = st.load()
        self.assertEqual(s2.concurrency, 9, "损坏时从 .bak 恢复上一版保存值(9)")
        bak = d / "settings.json.bak"
        self.assertTrue(bak.exists())

    def test_no_backup_corrupt_falls_back_default(self):
        d = Path(tempfile.mkdtemp())
        p = d / "settings.json"
        p.write_text("{ corrupt !!", encoding="utf-8")  # 直接从损坏开始，无 .bak
        st = SettingsStore(p)
        s = st.load()
        self.assertEqual(s.concurrency, 8)  # 兜底默认
