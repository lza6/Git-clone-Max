"""G84-18：从 README / awesome 清单批量导入地址。

为什么要守：这是「挂着刷几百个仓库」的第一道门 —— 用户的第一批地址几乎都躺在
某个清单里。手抄是最劝退的一步，而"导入错了/静默丢一半"比没有这个功能更糟。
所以这里同时守**抽取正确性**与**不静默丢弃**（无效链接必须回显条数）。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.url_lib import extract_repo_urls, parse_urls  # noqa: E402

README = """# Awesome tools

- [vercel-labs/skills](https://github.com/vercel-labs/skills)
- 裸地址：https://github.com/octocat/Hello-World
- 重复：https://github.com/vercel-labs/skills
- 徽章：![b](https://img.shields.io/badge/a-b-c.svg)
- 子组：https://gitlab.com/group/sub/project
- 中文标点结尾：https://github.com/owner/repo。
"""


class TestExtractor(unittest.TestCase):
    def test_dedupes_and_keeps_order(self):
        urls = extract_repo_urls(README)
        self.assertEqual(urls[0], "https://github.com/vercel-labs/skills")
        self.assertEqual(len(urls), len(set(urls)), "抽取结果没有去重")
        self.assertEqual(urls.count("https://github.com/vercel-labs/skills"), 1)

    def test_strips_cjk_trailing_punctuation(self):
        urls = extract_repo_urls("见 https://github.com/owner/repo。\n")
        self.assertIn("https://github.com/owner/repo", urls)

    def test_badge_url_is_extracted_but_rejected_downstream(self):
        """徽章/图片地址由提取层保留、由 parse_urls 判无效 —— 分层清晰。"""
        urls = extract_repo_urls(README)
        self.assertTrue(any("img.shields.io" in u for u in urls))
        specs, invalid = parse_urls("\n".join(urls))
        self.assertFalse(any("shields.io" in s.url_https for s in specs))
        self.assertEqual(len(invalid), 1)

    def test_parses_three_platforms(self):
        specs, _ = parse_urls("\n".join(extract_repo_urls(README)))
        names = {s.folder_name for s in specs}
        self.assertIn("vercel-labs__skills", names)
        self.assertIn("octocat__Hello-World", names)
        self.assertTrue(any("gitlab" in n for n in names), f"子组地址没被识别：{names}")

    def test_empty_input(self):
        self.assertEqual(extract_repo_urls(""), [])
        self.assertEqual(extract_repo_urls("没有任何链接的文本"), [])


class _FakeInput:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def toPlainText(self) -> str:
        return self.text

    def appendPlainText(self, more: str) -> None:
        self.text += more


class _FakeOwner:
    def __init__(self, existing: str = "") -> None:
        self.repo_input = _FakeInput(existing)
        self.messages: list[str] = []

    def statusBar(self):
        owner = self

        class _Bar:
            def showMessage(self, msg: str) -> None:
                owner.messages.append(msg)
        return _Bar()


class TestImportHandler(unittest.TestCase):
    def setUp(self):
        from gcm.ui import main_window
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.path = pathlib.Path(self._td.name) / "README.md"
        self.path.write_text(README, encoding="utf-8")
        info = mock.MagicMock()
        self.patches = [
            mock.patch.object(main_window, "QFileDialog",
                              types.SimpleNamespace(
                                  getOpenFileName=lambda *a, **k: (str(self.path), ""))),
            mock.patch.object(main_window, "QMessageBox", info),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.info = info

    def _run(self, owner):
        from gcm.ui.download_tools import import_readme_list
        import_readme_list(owner)

    def test_appends_valid_urls(self):
        owner = _FakeOwner()
        self._run(owner)
        got = owner.repo_input.toPlainText()
        self.assertIn("github.com/vercel-labs/skills", got)
        self.assertIn("octocat/Hello-World", got)
        self.assertNotIn("shields.io", got, "徽章地址不该被当成仓库导入")

    def test_does_not_duplicate_existing(self):
        owner = _FakeOwner("https://github.com/vercel-labs/skills\n")
        self._run(owner)
        text = owner.repo_input.toPlainText()
        self.assertEqual(text.count("vercel-labs/skills"), 1,
                         "已存在的地址被重复导入")

    def test_reports_skipped_links_instead_of_silently_dropping(self):
        owner = _FakeOwner()
        self._run(owner)
        shown = " ".join(str(c.args) for c in self.info.information.call_args_list)
        self.assertIn("跳过", shown, "无效链接被静默丢弃了（用户看不到为什么少了几条）")

    def test_no_urls_in_file_is_explained(self):
        self.path.write_text("没有任何链接\n", encoding="utf-8")
        owner = _FakeOwner()
        self._run(owner)
        self.assertEqual(owner.repo_input.toPlainText(), "")
        self.assertTrue(self.info.information.called)

    def test_gbk_readme_is_decoded(self):
        """审查发现：只按 utf-8 读会把中文 GBK README 变成满屏替换符 → "0 个链接"。"""
        self.path.write_bytes(
            ("# 清单\n- https://github.com/owner/gbk-repo\n").encode("gbk"))
        owner = _FakeOwner()
        self._run(owner)
        self.assertIn("gbk-repo", owner.repo_input.toPlainText(),
                      "GBK 编码的清单没有回退解码，用户会看到 0 条导入")

    def test_button_is_wired(self):
        layout_src = (ROOT / "gcm/ui/download_layout.py").read_text(encoding="utf-8")
        self.assertIn("btn_readme_import", layout_src)
        self.assertIn("owner.import_readme_list", layout_src)
        mw = (ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn("def import_readme_list(self)", mw,
                      "按钮连了不存在的槽（真机上点一下就是 AttributeError）")


if __name__ == "__main__":
    unittest.main()
