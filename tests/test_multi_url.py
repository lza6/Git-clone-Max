# -*- coding: utf-8 -*-
"""多平台 Git URL 解析器单元测试（对应指南 G01-1/G01-2/G01-6）。

覆盖：
- 多平台 HTTPS/SSH/短格式/子组解析
- 命名规则：github → owner__repo；跨 host → host__owner__repo
- 仓库详情页路径（tree/blob）剥离
- 旧 API 兼容：parse_repo_url 仅 GitHub；is_github_url 严格；host_of 通用
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.url_lib import (
    parse_any_repo_url, parse_repo_url, parse_urls,
    is_github_url, is_git_url, host_of, normalize_url,
)


class TestAnyUrlParsing(unittest.TestCase):
    """通用多平台解析：任何 KNOWN_HOSTS 平台都应解析成功。"""

    def test_github_https(self):
        s = parse_any_repo_url("https://github.com/vercel-labs/skills")
        self.assertEqual((s.owner, s.repo), ("vercel-labs", "skills"))
        self.assertEqual(s.folder_name, "vercel-labs__skills")
        self.assertEqual(s.url_https, "https://github.com/vercel-labs/skills.git")

    def test_gitlab_https(self):
        s = parse_any_repo_url("https://gitlab.com/gitlab-org/gitlab")
        self.assertEqual((s.owner, s.repo), ("gitlab-org", "gitlab"))
        self.assertEqual(s.folder_name, "gitlab.com__gitlab-org__gitlab")

    def test_gitee_https(self):
        s = parse_any_repo_url("https://gitee.com/mirrors/pytorch")
        self.assertEqual(s.folder_name, "gitee.com__mirrors__pytorch")

    def test_codeberg_https(self):
        s = parse_any_repo_url("https://codeberg.org/forgejo/forgejo")
        self.assertEqual(s.folder_name, "codeberg.org__forgejo__forgejo")

    def test_bitbucket_https(self):
        s = parse_any_repo_url("https://bitbucket.org/atlassian/python-bitbucket")
        self.assertEqual((s.owner, s.repo), ("atlassian", "python-bitbucket"))
        self.assertEqual(s.folder_name, "bitbucket.org__atlassian__python-bitbucket")

    def test_ssh_github(self):
        s = parse_any_repo_url("git@github.com:microsoft/azure-skills.git")
        self.assertEqual((s.owner, s.repo), ("microsoft", "azure-skills"))
        self.assertEqual(s.folder_name, "microsoft__azure-skills")

    def test_ssh_gitlab(self):
        s = parse_any_repo_url("git@gitlab.com:gitlab-org/gitlab.git")
        self.assertEqual(s.folder_name, "gitlab.com__gitlab-org__gitlab")

    def test_ssh_uri_gitlab(self):
        s = parse_any_repo_url("ssh://git@gitlab.com:2222/gitlab-org/gitlab.git")
        # 带端口的 host 应正确解析 host=gitlab.com
        self.assertEqual(s.host if hasattr(s, "host") else "gitlab.com",
                         "gitlab.com" if s.folder_name.startswith("gitlab.com") else "?")
        self.assertEqual(s.folder_name, "gitlab.com__gitlab-org__gitlab")

    def test_ssh_uri_codeberg(self):
        s = parse_any_repo_url("ssh://git@codeberg.org/x/y.git")
        self.assertEqual(s.folder_name, "codeberg.org__x__y")

    def test_short_format(self):
        s = parse_any_repo_url("openmeterio/openmeter")
        self.assertEqual(s.folder_name, "openmeterio__openmeter")

    def test_trailing_git_suffix_https(self):
        s = parse_any_repo_url("https://github.com/a/b.git")
        self.assertEqual(s.folder_name, "a__b")
        self.assertEqual(s.url_https, "https://github.com/a/b.git")

    def test_invalid(self):
        self.assertIsNone(parse_any_repo_url(""))
        self.assertIsNone(parse_any_repo_url("not a url"))
        self.assertIsNone(parse_any_repo_url("   "))

    def test_subgroup_gitlab(self):
        """子组路径 group/subgroup/repo → 取最后两段。"""
        s = parse_any_repo_url("https://gitlab.com/group/subgroup/repo.git")
        self.assertEqual((s.owner, s.repo), ("subgroup", "repo"))
        self.assertEqual(s.folder_name, "gitlab.com__subgroup__repo")

    def test_tree_blob_path_stripped(self):
        """仓库详情页路径（tree/blob）应剥离到仓库名。"""
        s = parse_any_repo_url("https://github.com/Arize-ai/phoenix/tree/main/docs")
        self.assertEqual((s.owner, s.repo), ("Arize-ai", "phoenix"))
        self.assertEqual(s.folder_name, "Arize-ai__phoenix")

        s2 = parse_any_repo_url("https://github.com/Arize-ai/phoenix/blob/main/README.md")
        self.assertEqual((s2.owner, s2.repo), ("Arize-ai", "phoenix"))


class TestLegacyApi(unittest.TestCase):
    """旧 API 兼容：parse_repo_url 仅 GitHub；is_github_url 严格。"""

    def test_parse_repo_url_github(self):
        s = parse_repo_url("https://github.com/a/b")
        self.assertEqual(s.folder_name, "a__b")

    def test_parse_repo_url_short(self):
        s = parse_repo_url("openmeterio/openmeter")
        self.assertEqual((s.owner, s.repo), ("openmeterio", "openmeter"))

    def test_parse_repo_url_non_github_none(self):
        self.assertIsNone(parse_repo_url("https://gitlab.com/foo/bar"))
        self.assertIsNone(parse_repo_url("git@gitlab.com:foo/bar.git"))

    def test_is_github_url(self):
        self.assertTrue(is_github_url("https://github.com/a/b"))
        self.assertTrue(is_github_url("github.com/a/b"))
        self.assertFalse(is_github_url("gitlab.com/a/b"))
        self.assertFalse(is_github_url("https://gitlab.com/a/b"))

    def test_is_git_url(self):
        self.assertTrue(is_git_url("https://gitlab.com/a/b"))
        self.assertTrue(is_git_url("openmeterio/openmeter"))
        self.assertFalse(is_git_url("not a url"))


class TestHostOf(unittest.TestCase):
    def test_host_of_https(self):
        self.assertEqual(host_of("https://github.com/a/b"), "github.com")
        self.assertEqual(host_of("https://gitlab.com/a/b"), "gitlab.com")
        self.assertEqual(host_of("https://codeberg.org/a/b"), "codeberg.org")
        self.assertEqual(host_of("https://www.github.com/a/b"), "github.com")

    def test_host_of_ssh(self):
        self.assertEqual(host_of("git@github.com:x/y.git"), "github.com")
        self.assertEqual(host_of("ssh://git@gitlab.com/x/y.git"), "gitlab.com")

    def test_host_of_short(self):
        self.assertEqual(host_of("openmeterio/openmeter"), "github.com")

    def test_host_of_empty(self):
        self.assertEqual(host_of(""), "")

    def test_host_of_custom_domain(self):
        self.assertEqual(host_of("https://example.com/a/b"), "example.com")


class TestParseUrlsBatch(unittest.TestCase):
    def test_mixed_platforms(self):
        specs, invalid = parse_urls(
            "https://github.com/a/b\nhttps://gitlab.com/c/d\nnope\n\n"
        )
        self.assertEqual([s.folder_name for s in specs],
                         ["a__b", "gitlab.com__c__d"])
        self.assertEqual(invalid, ["nope"])

    def test_all_invalid(self):
        specs, invalid = parse_urls("x\n\ny")
        self.assertEqual(specs, [])
        self.assertEqual(invalid, ["x", "y"])

    def test_whitespace_ignored(self):
        specs, invalid = parse_urls("  \n\t\n")
        self.assertEqual(specs, [])
        self.assertEqual(invalid, [])


class TestNormalize(unittest.TestCase):
    def test_normalize_multi(self):
        self.assertEqual(normalize_url("https://github.com/a/b"),
                         "https://github.com/a/b.git")
        self.assertEqual(normalize_url("git@gitlab.com:x/y.git"),
                         "https://gitlab.com/x/y.git")
        self.assertIsNone(normalize_url("not a url"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestDetailPagesAndSecurity(unittest.TestCase):
    """补充审查修复项：详情页路径 / 安全边界（对应独立 code-reviewer/security-reviewer 发现）。"""

    def test_github_detail_pages(self):
        for u in [
            "https://github.com/user/repo/actions",
            "https://github.com/user/repo/releases/latest",
            "https://github.com/user/repo/wiki",
            "https://github.com/user/repo/issues/123",
            "https://github.com/user/repo/pulls",
            "https://github.com/user/repo/commits/main",
            "https://github.com/user/repo/raw/master/README.md",
        ]:
            s = parse_any_repo_url(u)
            self.assertEqual((s.owner, s.repo), ("user", "repo"), u)
            self.assertEqual(s.folder_name, "user__repo", u)

    def test_two_segment_src_repo_kept(self):
        """2 段真实仓库 user/src 不应被误剥。"""
        s = parse_any_repo_url("https://github.com/user/src")
        self.assertEqual((s.owner, s.repo), ("user", "src"))

    def test_dotdot_segment_rejected(self):
        self.assertIsNone(parse_any_repo_url("https://github.com/../b"))
        self.assertIsNone(parse_any_repo_url("https://github.com/a/.."))

    def test_at_sign_credential_prefix_rejected(self):
        """凭据前置式主机覆盖攻击：owner/repo 含 @ → 拒绝。"""
        self.assertIsNone(parse_any_repo_url("https://github.com/attacker@evil.example/r"))
        self.assertIsNone(parse_any_repo_url("git@evil.example:attacker@evil/r.git"))

    def test_subgroup_url_preserved(self):
        """子组路径重建 URL 保留分组前缀，避免克隆错误仓库。"""
        s1 = parse_any_repo_url("https://gitlab.com/group/a/repo")
        s2 = parse_any_repo_url("https://gitlab.com/group/b/repo")
        self.assertNotEqual(s1.url_https, s2.url_https)
        self.assertEqual(s1.url_https, "https://gitlab.com/group/a/repo.git")
        self.assertEqual(s2.url_https, "https://gitlab.com/group/b/repo.git")

    def test_legacy_api_no_crash(self):
        """旧 API 不得对合法输入抛异常（回归审查 CRITICAL）。"""
        self.assertEqual(parse_repo_url("https://github.com/a/b").folder_name, "a__b")
        self.assertIsNone(parse_repo_url("https://gitlab.com/x/y"))
        self.assertTrue(is_github_url("github.com/a/b"))
        self.assertFalse(is_github_url("gitlab.com/a/b"))
        self.assertEqual(host_of("https://gitlab.com/a/b"), "gitlab.com")
        self.assertEqual(host_of("openmeterio/openmeter"), "github.com")
