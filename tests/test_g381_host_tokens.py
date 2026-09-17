"""G38-1 按 host 映射凭据 单测。

覆盖：
- GitService._env：github→Bearer / gitlab→PRIVATE-TOKEN / 未登记 host→不注入 / 空 host→全局
- host_tokens 映射优先级（映射值 > 全局 token）
- SettingsStore host_tokens 逐条目加密落盘 + 读回解密
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.settings import Settings, SettingsStore  # noqa: E402（sys.path 注入后导入）
from gcm.git.service import GitService  # noqa: E402


def _mk(base: Path, **kw) -> GitService:
    return GitService(base, **kw)


class TestHostTokensInject(unittest.TestCase):
    """G38-1 GitService 按 host 注入认证头。"""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())

    def _extra_header(self, svc):
        env = svc._env()
        if not env:
            return None
        n = int(env.get("GIT_CONFIG_COUNT", 0) or 0)
        for i in range(n):
            if env.get(f"GIT_CONFIG_KEY_{i}") == "http.extraHeader":
                return env.get(f"GIT_CONFIG_VALUE_{i}")
        return None

    def test_github_uses_bearer_from_host_tokens(self):
        """github + host_tokens[github] → Bearer 用映射值。"""
        svc = _mk(self.base, token="global_xyz",
                  host_tokens={"github.com": "map_abc"})
        svc._current_host = "github.com"
        self.assertEqual(self._extra_header(svc), "Authorization: Bearer map_abc")

    def test_gitlab_uses_private_token_header(self):
        """gitlab + host_tokens[gitlab.com] → PRIVATE-TOKEN 头。"""
        svc = _mk(self.base, token="global_xyz",
                  host_tokens={"gitlab.com": "glpat-123"})
        svc._current_host = "gitlab.com"
        self.assertEqual(self._extra_header(svc), "PRIVATE-TOKEN: glpat-123")

    def test_unknown_host_no_global_token(self):
        """未知 host + 仅全局 token（未登记）→ 不注入（F1 安全）。"""
        svc = _mk(self.base, token="global_xyz")
        svc._current_host = "example.com"
        self.assertIsNone(self._extra_header(svc), "未登记 host 不应发送全局 token")

    def test_unknown_host_with_mapping_injects(self):
        """未知 host + host_tokens 显式登记 → 注入映射值。"""
        svc = _mk(self.base, token="global_xyz",
                  host_tokens={"example.com": "mapped-xyz"})
        svc._current_host = "example.com"
        self.assertEqual(self._extra_header(svc), "Authorization: Bearer mapped-xyz")

    def test_empty_host_uses_global(self):
        """空 host（本工具 github 主战场）→ 全局 token Bearer。"""
        svc = _mk(self.base, token="global_xyz")
        svc._current_host = ""
        self.assertEqual(self._extra_header(svc), "Authorization: Bearer global_xyz")

    def test_host_tokens_override_global(self):
        """同 host 映射优先级 > 全局 token。"""
        svc = _mk(self.base, token="global_xyz",
                  host_tokens={"github.com": "mapped_github"})
        svc._current_host = "github.com"
        self.assertIn("mapped_github", self._extra_header(svc))


class TestSettingsHostTokens(unittest.TestCase):
    """G38-1 SettingsStore host_tokens 加密落盘。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_host_tokens_roundtrip_encrypted(self):
        """保存→磁盘 host_tokens 密文（enc:v1: 前缀）→读回解密一致。"""
        store = SettingsStore(self.tmp / "settings.json")
        s = Settings()
        s.host_tokens = {"gitlab.com": "glpat-secret", "github.com": "ghp-secret"}
        store.save(s)
        raw = (self.tmp / "settings.json").read_text(encoding="utf-8")
        # 磁盘上不应出现明文 token
        self.assertNotIn("glpat-secret", raw)
        self.assertNotIn("ghp-secret", raw)
        self.assertIn("enc:v1:", raw)
        # 读回解密
        s2 = SettingsStore(self.tmp / "settings.json").load()
        self.assertEqual(s2.host_tokens["gitlab.com"], "glpat-secret")
        self.assertEqual(s2.host_tokens["github.com"], "ghp-secret")

    def test_host_tokens_empty_by_default(self):
        """默认 host_tokens 为空 dict。"""
        s = Settings()
        self.assertEqual(s.host_tokens, {})

    def test_host_tokens_corrupt_safe(self):
        """损坏条目读回为空串，不崩。"""
        store = SettingsStore(self.tmp / "settings.json")
        s = Settings()
        s.host_tokens = {"gitlab.com": "enc:v1:not-valid-b64"}
        store.save(s)
        s2 = SettingsStore(self.tmp / "settings.json").load()
        self.assertIn("gitlab.com", s2.host_tokens)  # 键保留，值解密失败→空


if __name__ == "__main__":
    unittest.main(verbosity=2)
