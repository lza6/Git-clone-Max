"""G84-8 / G84-9：`settings.json` 的两条数据安全守卫。

**G84-8（凭据被静默清空）**：`_decrypt_token` 对解不开的密文返回空串（这是对的，
避免把垃圾当凭据用）。但 `load() → save()` 走一趟（用户只是改了个并发数）会把
空串**当成新值重新加密落盘** → 用户原本还能手工救回的密文永久消失。
真实触发场景：换机器 / Windows 账户迁移 / DPAPI 后端缺失 / 旧版 xorfixed 升级。

**G84-9（掉电丢全部凭据）**：`tmp` 写完直接 `os.replace`，中间没有 fsync。
`replace` 只保证目录项原子，不保证内容已落盘 —— 掉电可能留下**零长度**的
`settings.json`，而它装着全部 token。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db import settings as S  # noqa: E402


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._td.name)
        self.path = self.dir / "settings.json"
        self.addCleanup(self._td.cleanup)

    def _write_payload(self, payload: dict) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _read_payload(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


class TestUndecryptableIsPreserved(_TmpCase):
    # 必须是**任何后端都解不开**的密文：原来用合法 base64，POSIX 的 xorfixed
    # 后端能把它"解"成垃圾串（非空）→ 不进入"解不开"分支 → CI 上 4 例全红。
    GARBAGE = "enc:v1:%%%not-base64%%%"

    def test_load_returns_empty_for_undecryptable(self):
        self._write_payload({"token": self.GARBAGE})
        s = S.SettingsStore(self.path).load()
        self.assertEqual(s.token, "", "损坏密文应当读成空串（不能当凭据用）")

    def test_save_keeps_original_ciphertext(self):
        """核心断言：改个无关字段再保存，磁盘上的密文**必须原样还在**。"""
        self._write_payload({"token": self.GARBAGE, "concurrency": 4})
        store = S.SettingsStore(self.path)
        s = store.load()
        s.concurrency = 6                  # 用户只是改了个并发数
        store.save(s)
        self.assertEqual(self._read_payload()["token"], self.GARBAGE,
                         "解密失败的密文被覆盖了 —— 用户凭据永久丢失")
        self.assertEqual(self._read_payload()["concurrency"], 6)

    def test_without_mechanism_the_credential_would_be_lost(self):
        """反向自检：把记账清空，密文就会被空串覆盖 —— 证明机制真的在起作用。"""
        self._write_payload({"token": self.GARBAGE})
        store = S.SettingsStore(self.path)
        s = store.load()
        store._undecryptable.clear()
        store.save(s)
        self.assertEqual(self._read_payload()["token"], "",
                         "对照组失效：没有记账保护时本应被清空，说明测试没有覆盖到机制")

    def test_host_tokens_entry_preserved_per_key(self):
        self._write_payload({"host_tokens": {"github.com": self.GARBAGE}})
        store = S.SettingsStore(self.path)
        s = store.load()
        store.save(s)
        self.assertEqual(
            self._read_payload()["host_tokens"].get("github.com"), self.GARBAGE,
            "逐 host 凭据在解密失败后被清空了")

    def test_user_retyping_overrides_preserved_ciphertext(self):
        """用户重填了 token → 必须用新值，不能被旧密文顶回去。"""
        self._write_payload({"token": self.GARBAGE})
        store = S.SettingsStore(self.path)
        s = store.load()
        s.token = "ghp_newtoken"
        store.save(s)
        stored = self._read_payload()["token"]
        self.assertNotEqual(stored, self.GARBAGE)
        self.assertEqual(S._decrypt_token(stored), "ghp_newtoken",
                         "用户重填的 token 没能正确加密落盘")


class TestCredentialDeletion(_TmpCase):
    """R9（审查发现）：记账保护不能变成"永远删不掉"。"""

    # 必须是**任何后端都解不开**的密文：原来用合法 base64，POSIX 的 xorfixed
    # 后端能把它"解"成垃圾串（非空）→ 不进入"解不开"分支 → CI 上 4 例全红。
    GARBAGE = "enc:v1:%%%not-base64%%%"

    def test_host_row_removal_actually_deletes(self):
        """在「按平台凭据」里整行删掉某个 host = 明确要删除，不能被塞回去。"""
        self._write_payload({"host_tokens": {"github.com": self.GARBAGE,
                                            "gitlab.com": "enc:v1:another"}})
        store = S.SettingsStore(self.path)
        s = store.load()
        s.host_tokens = {}          # 用户把整行删掉
        store.save(s)
        self.assertEqual(self._read_payload()["host_tokens"], {},
                         "删掉的 host 又被原密文塞回去了（等于删不掉）")

    def test_forget_undecryptable_allows_clearing(self):
        self._write_payload({"token": self.GARBAGE})
        store = S.SettingsStore(self.path)
        s = store.load()
        self.assertIn("token", store.undecryptable_fields())
        store.forget_undecryptable("token")
        s.token = ""
        store.save(s)
        self.assertEqual(self._read_payload()["token"], "",
                         "调用 forget_undecryptable 后仍被旧密文写回")

    def test_undecryptable_fields_reports_only_broken(self):
        self._write_payload({"token": self.GARBAGE,
                             "notify_webhook_token": S._encrypt_token("real-value")})
        store = S.SettingsStore(self.path)
        store.load()
        self.assertEqual(store.undecryptable_fields(), ("token",))


class TestBackendIndependence(_TmpCase):
    """同一套语义必须在两种后端下都成立（Windows=dpapi / POSIX=xorfixed）。

    CI 实测教训：只在 Windows 上跑过的密钥相关测试会在 POSIX 上红。
    """

    def test_preserve_holds_under_xorfixed_backend(self):
        from unittest import mock as _mock

        with _mock.patch.object(S, "_get_backend", return_value="xorfixed"):
            enc = S._encrypt_token("real-secret")
            self.assertTrue(enc.startswith("enc:v1:"))
            self.assertEqual(S._decrypt_token(enc), "real-secret")
            # 坏密文在 xor 后端下同样解不开
            self.assertEqual(S._decrypt_token("enc:v1:%%%not-base64%%%"), "")
            # 记账保护同样生效
            self._write_payload({"token": "enc:v1:%%%not-base64%%%"})
            store = S.SettingsStore(self.path)
            loaded = store.load()
            self.assertEqual(loaded.token, "")
            self.assertIn("token", store.undecryptable_fields())
            store.save(loaded)
            self.assertEqual(self._read_payload()["token"], "enc:v1:%%%not-base64%%%")


class TestAtomicDurability(_TmpCase):
    def test_roundtrip_still_works(self):
        store = S.SettingsStore(self.path)
        s = S.Settings()
        s.token = "ghp_roundtrip"
        store.save(s)
        self.assertEqual(S.SettingsStore(self.path).load().token, "ghp_roundtrip")

    def test_no_tmp_left_behind_and_bak_holds_previous(self):
        store = S.SettingsStore(self.path)
        s = S.Settings()
        s.concurrency = 8
        store.save(s)
        first = self.path.read_text(encoding="utf-8")
        s.concurrency = 12
        store.save(s)
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"留下了 tmp 残留：{leftovers}")
        bak = self.path.with_suffix(".json.bak")
        self.assertTrue(bak.exists(), "第二次保存应留下 .bak（上一版内容）")
        self.assertEqual(bak.read_text(encoding="utf-8"), first)

    def test_write_helpers_fsync_path_is_used(self):
        """`save()` 必须走带 fsync 的写盘助手（不是裸 write_text）。"""
        src = (ROOT / "gcm" / "db" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("_write_text_synced(", src)
        self.assertIn("os.fsync", src)


if __name__ == "__main__":
    unittest.main()
