"""S-05：URL 语义不得被文件名清洗改写（Rev10 W2 P1）。

旧行为：`sanitize_name`（文件名清洗）的输出被用来重拼网络 URL ——
真实案例 `geocar/con`（搜索 API 可查到的保留名仓库）会被改成
`geocar/con_.git` → 静默 404 / 克隆到错误地址。
修法：URL 保持原始 owner/repo 段；清洗只作用于 `folder_name`（落盘目录）。
"""
import unittest

from gcm.app.url_lib import parse_repo_url


class TestUrlSemanticsPreserved(unittest.TestCase):
    def test_reserved_name_repo_url_unchanged(self):
        """CON 仓库（Windows 保留名）：URL 不改写，目录名仍可落盘。"""
        spec = parse_repo_url("https://github.com/geocar/con")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.url_https, "https://github.com/geocar/con.git",
                         "URL 必须保持原始段（此前被改成 con_.git）")
        self.assertEqual(spec.owner, "geocar")
        self.assertEqual(spec.repo, "con")
        self.assertEqual(spec.folder_name, "geocar__con_",
                         "目录名仍需清洗（Windows 下 CON 目录不可创建）")

    def test_normal_repo_untouched(self):
        spec = parse_repo_url("https://github.com/ocaml/dune")
        self.assertEqual(spec.url_https, "https://github.com/ocaml/dune.git")
        self.assertEqual(spec.folder_name, "ocaml__dune")

    def test_dotted_name_kept(self):
        """点号是 GitHub repo 名合法字符，不得被清洗改写 URL。"""
        spec = parse_repo_url("https://github.com/my-repo/v1.2")
        self.assertEqual(spec.url_https, "https://github.com/my-repo/v1.2.git")

    def test_invalid_char_only_affects_folder(self):
        """含非法字符的段（GitHub 上不存在但用户会粘贴）：URL 原样、目录清洗。"""
        spec = parse_repo_url("https://github.com/foo/a:b")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.url_https, "https://github.com/foo/a:b.git")
        self.assertEqual(spec.folder_name, "foo__a_b")


if __name__ == "__main__":
    unittest.main()
