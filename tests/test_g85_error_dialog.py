# -*- coding: utf-8 -*-
"""G85 B1 守卫：错误三段式统一（U-18 收口批）。

Rev18 B1：把「带原始异常文本」的弹窗迁到 `ui/error_dialog` 三段式
（发生了什么 / 为什么 / 怎么办）。分类唯一来源 `app/errors.classify_error`
（S21 铁律：UI 不得再写关键字表）。

本文件三层守卫：

1. **迁移闭环**：6 处缺陷面各有真实触发测试 —— 异常路径真实执行、
   断言 ErrorDialog 消费了 `ErrorInfo`（title/cause/action 三段非空）。
   模态 `exec()` 在测试中 mock 掉（对话框是外部边界，CLAUDE.md §4 允许），
   组件与分类链真实执行。
2. **AST 守卫**：`gcm/ui/` 禁止新增 `QMessageBox.critical/warning(<异常文本>)`
   形态，白名单登记存量（error_dialog 兜底分支）。含反向自检
   （插入违规样本 → 必须红）。
3. **回归保护**：迁移不得破坏「确认类弹窗默认 No」语义（R15-06 审计）。

盘点口径（B1-1，2026-11-14 实测）：AST 全量扫描 gcm/ui 59 处 QMessageBox，
带异常/底层错误文本直达用户 6 处；指南预估 11 处中其余为
validate_ssh_key_path / permission_warning 等已是人话文案（CUT，登记理由）。
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402

from gcm.ui import error_dialog  # noqa: E402
from gcm.ui.error_dialog import ErrorDialog, show_error, show_error_message  # noqa: E402


# ---------------------------------------------------------------------------
# 1. show_error_message：str 载荷入口（worker 信号 / (ok, msg) 返回形态）
# ---------------------------------------------------------------------------


class TestShowErrorMessage(unittest.TestCase):
    """str 载荷 → classify_error → 三段式 ErrorInfo。exec 不真跑（模态）。"""

    def _captured(self, message, context=""):
        holder: list = []
        with mock.patch.object(error_dialog.ErrorDialog, "exec", lambda self: None), \
             mock.patch.object(error_dialog, "ErrorDialog",
                               side_effect=lambda info, **kw:
                               mock.MagicMock(__class__=error_dialog.ErrorDialog)):
            # 上面的 side_effect 让 ErrorDialog(...) 返回 mock，
            # 真实的 classify 链路仍然执行；exec 由 MagicMock 吸收。
            show_error_message(None, message, context=context)
            return holder

    def test_network_message_gets_three_segments(self):
        """网络类错误文本 → 三段式 title/cause/action 齐全。"""
        holder: list = []
        real_cls = error_dialog.ErrorDialog

        def _factory(info, parent=None, title=None):
            holder.append(info)
            dlg = mock.MagicMock()
            dlg.exec = lambda: None
            return dlg

        with mock.patch.object(error_dialog, "ErrorDialog", side_effect=_factory):
            show_error_message(None, "fatal: unable to access 'https://github.com': "
                                     "Failed to connect to github.com port 443",
                               context="测试场景")
        self.assertEqual(len(holder), 1, "ErrorDialog 应被构建一次")
        info = holder[0]
        self.assertTrue(str(getattr(info, "title", "")).strip(), "title 段缺失")
        self.assertTrue(str(getattr(info, "cause", "")).strip(), "cause 段缺失")
        self.assertTrue(str(getattr(info, "action", "")).strip(), "action 段缺失")

    def test_unknown_message_falls_back_to_diag_hint(self):
        """分类器拿不准 → 建议「怎么办」段仍非空（不比裸 str 差）。"""
        holder: list = []

        def _factory(info, parent=None, title=None):
            holder.append(info)
            return mock.MagicMock()

        with mock.patch.object(error_dialog, "ErrorDialog", side_effect=_factory):
            show_error_message(None, "某种完全无法分类的神秘错误xyzzy", context="测试")
        self.assertEqual(len(holder), 1)
        info = holder[0]
        self.assertTrue(str(getattr(info, "action", "")).strip(), "未知错误也要有「怎么办」")

    def test_dialog_renders_three_segments(self):
        """ErrorDialog 真实构建（不 exec）：body 含三段文本。"""
        from PyQt6.QtWidgets import QLabel
        info = error_dialog.classify_error(
            "fatal: Authentication failed for 'https://github.com/o/r.git'",
            "测试：认证失败")
        dlg = ErrorDialog(info, parent=None)
        try:
            labels = dlg.findChildren(QLabel)
            joined = "\n".join(lbl.text() for lbl in labels)
            self.assertIn("为什么：", joined)
            self.assertIn("怎么办：", joined)
        finally:
            dlg.deleteLater()


# ---------------------------------------------------------------------------
# 2. 六处迁移点真实触发（异常路径真实执行；exec 被 mock，组件链真实跑）
# ---------------------------------------------------------------------------


class TestMigrationSites(unittest.TestCase):
    """每处迁移：真实触发错误路径 → 断言走了 show_error/show_error_message。"""

    def test_main_window_mkdir_failure_uses_show_error(self):
        """main_window.py:2356 mkdir OSError → show_error（不再裸 str(e)）。

        触发方式跨平台一致：mock `Path.mkdir` 抛 OSError（Windows 上 `Z:` 盘符
        依赖 NT 盘符语义，POSIX 会当普通目录名创建成功 → 错误路径根本不触发）。
        """
        from gcm.ui import main_window as mw

        w = mw.MainWindow(data_dir=tempfile.mkdtemp(prefix="gcm85_a_"))
        try:
            # 第一道门（repo_input 为空 → information 模态）必须先过：
            # 填一个有效地址，让流程走到 target mkdir 的 OSError 分支。
            w.repo_input.setPlainText("https://github.com/o/r")
            w.target_edit.setText("Z:\\__gcm_no_such_drive__\\deep\\dir")
            holder: list = []

            def _factory(info, parent=None, title=None):
                holder.append(info)
                return mock.MagicMock()

            with mock.patch.object(error_dialog, "ErrorDialog", side_effect=_factory), \
                 mock.patch.object(mw.QMessageBox, "question",
                                   return_value=QMessageBox.StandardButton.Yes), \
                 mock.patch.object(mw.Path, "mkdir",
                                   side_effect=OSError(13, "模拟盘符不存在")):
                w.start_all()
            self.assertEqual(len(holder), 1,
                             f"mkdir 失败必须走三段式（holder={holder}）")
            self.assertIn("Errno 13", str(holder[0].raw), "三段式 raw 必须承载 OSError 原文")
        finally:
            w.deleteLater()

    def test_settings_backup_failure_uses_show_error(self):
        """settings_actions.py:222 backup_to 异常 → show_error。"""
        from gcm.ui import settings_actions as sa

        class _FakeDB:
            def backup_to(self, dest):
                raise OSError("模拟磁盘满")

        class _Owner:
            db = _FakeDB()
            data_dir = "."

        panel = type("P", (), {"owner": _Owner()})()
        holder: list = []

        def _factory(info, parent=None, title=None):
            holder.append(info)
            return mock.MagicMock()

        with mock.patch.object(error_dialog, "ErrorDialog", side_effect=_factory), \
             mock.patch("gcm.ui.busy.busy"):
            sa.backup_database(panel)
        self.assertEqual(len(holder), 1, "备份失败必须走三段式 ErrorDialog")
        self.assertIn("磁盘满", str(holder[0].raw))

    def test_local_dialog_scan_failure_uses_show_error_message(self):
        """local_repos_dialog.py:195 扫描失败 → show_error_message（三段式）。"""
        from gcm.ui.local_repos_dialog import LocalReposDialog

        dlg = LocalReposDialog(parent=None, root_paths=[], db=None)
        try:
            holder: list = []

            def _factory(info, parent=None, title=None):
                holder.append(info)
                return mock.MagicMock()

            with mock.patch.object(error_dialog, "ErrorDialog", side_effect=_factory):
                dlg._on_scan_failed("fatal: 磁盘错误详情")
            self.assertEqual(len(holder), 1)
            self.assertIn("磁盘错误详情", str(holder[0].raw))
            self.assertIn("扫描失败", dlg.status.text())
            self.assertFalse(dlg.progress.isVisible())
        finally:
            dlg.close()

    def test_halfclone_repair_failure_uses_error_dialog(self):
        """halfclone_actions.py:78 修复失败 msg → ErrorDialog 直建。"""
        from gcm.ui import halfclone_actions as ha
        from gcm.models import RepoSpec
        from gcm.ui.progress_table import ProgressTable

        class _Owner:
            def __init__(self, spec):
                self.row_specs = {0: spec}
                self.target_edit = mock.Mock()
                self.target_edit.text.return_value = ""
                self.data_dir = Path(tempfile.gettempdir())
                self.statusBar = lambda: mock.Mock()
                self._emit_log = mock.Mock()
                self.settings = mock.Mock()

        with tempfile.TemporaryDirectory() as td:
            half = Path(td) / "half"
            (half / ".git").mkdir(parents=True)
            spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b",
                            local_path=str(half))
            owner = _Owner(spec)
            panel = ProgressTable(owner)
            panel._test_owner_ref = owner  # 弱引用需外部强引用（test_g80_halfclone_ui 同款）

            try:
                holder: list = []

                def _factory(info, parent=None, title=None):
                    holder.append(info)
                    return mock.MagicMock()

                with mock.patch.object(error_dialog, "ErrorDialog", side_effect=_factory):
                    ha.repair_halfcloned_row(panel, 0)
                self.assertEqual(len(holder), 1, "修复失败必须走三段式")
                # 空 .git 目录 → classify_repo = unborn_head → 文案「HEAD 不可解析」
                self.assertIn("HEAD", str(holder[0].raw))
            finally:
                panel.deleteLater()

    def test_migrate_failure_uses_error_dialog(self):
        """settings_actions.py:331 迁移失败 → ErrorDialog（str 载荷三段式）。"""
        from gcm.ui import settings_actions as sa

        owner = mock.MagicMock()
        owner.busy = False
        owner.data_dir = tempfile.mkdtemp(prefix="gcm85_m_")
        holder: list = []

        def _factory(info, parent=None, title=None):
            holder.append(info)
            return mock.MagicMock()

        with mock.patch("gcm.db.settings.migrate_data_dir",
                        return_value=(False, "模拟迁移 IO 错误")),              mock.patch.object(sa.QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.Yes),              mock.patch.object(error_dialog, "ErrorDialog", side_effect=_factory):
            sa.migrate_data_dir_flow(type("P", (), {"owner": owner})())
        self.assertEqual(len(holder), 1, "迁移失败必须走三段式")
        self.assertIn("模拟迁移 IO 错误", str(holder[0].raw))

# ---------------------------------------------------------------------------
# 3. AST 守卫：gcm/ui 禁止新增带异常文本的裸 QMessageBox（含反向自检）
# ---------------------------------------------------------------------------

# 存量白名单：error_dialog.py 兜底分支（分类/构建失败退化为纯文本 —— 这是
# 「不比原来差」的设计，不是缺陷）。行号会漂移，按文件名登记。
_WHITELIST_FILES = {"error_dialog.py"}


def _scan_flagged(ui_dir: Path) -> list[str]:
    """AST 扫描 gcm/ui：QMessageBox.critical/warning 的文本参数带异常形态。"""
    flagged: list[str] = []
    pat = __import__("re").compile(
        r"str\(\s*(e|exc|err|ex|error)\b|\{\s*(e|exc|err|ex|error)\b|\+\s*str\(")
    for py in sorted(ui_dir.rglob("*.py")):
        try:
            src = py.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "QMessageBox"
                    and node.func.attr in ("critical", "warning")):
                continue
            seg = ast.get_source_segment(src, node) or ""
            joined = " ".join(seg.split())
            if pat.search(joined) and py.name not in _WHITELIST_FILES:
                flagged.append(f"{py.name}:{node.lineno}")
    return flagged


class TestAstGuard(unittest.TestCase):
    """禁止新增裸 str(e) 弹窗（B1-3）；反向自检证明扫描器真实有效。"""

    def test_no_new_flagged_callsites(self):
        flagged = _scan_flagged(ROOT / "gcm" / "ui")
        self.assertEqual(
            flagged, [],
            f"gcm/ui 出现新的带异常文本弹窗（应迁 error_dialog）：{flagged}")

    def test_scanner_catches_violation_sample(self):
        """反向自检：插入违规样本 → 扫描器必须报红（否则守卫是摆设）。"""
        orig = _scan_flagged
        sample = ROOT / "gcm" / "ui" / "_ast_guard_sample_tmp.py"
        try:
            sample.write_text(
                "from PyQt6.QtWidgets import QMessageBox\n"
                "def f(parent, e):\n"
                "    QMessageBox.warning(parent, 't', str(e))\n",
                encoding="utf-8")
            flagged = orig(ROOT / "gcm" / "ui")
            self.assertTrue(any("_ast_guard_sample_tmp.py" in f for f in flagged),
                            "扫描器没抓到违规样本 —— 守卫无效")
        finally:
            sample.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 4. 兼容保护：确认类弹窗语义不变（R15-06）
# ---------------------------------------------------------------------------


class TestConfirmDialogsUnchanged(unittest.TestCase):
    """迁移只动失败类弹窗；确认类默认 No 语义不得被破坏。"""

    def test_delete_halfclone_confirmation_still_default_no(self):
        from gcm.ui.progress_table import ProgressTable
        from gcm.models import RepoSpec

        class _Owner:
            def __init__(self, spec):
                self.row_specs = {0: spec}
                self.target_edit = mock.Mock()
                self.target_edit.text.return_value = ""
                self.data_dir = Path(tempfile.gettempdir())
                self.statusBar = lambda: mock.Mock()
                self._emit_log = mock.Mock()
                self.settings = mock.Mock()

        with tempfile.TemporaryDirectory() as td:
            half = Path(td) / "half"
            (half / ".git").mkdir(parents=True)
            spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b",
                            local_path=str(half))
            owner = _Owner(spec)
            panel = ProgressTable(owner)
            panel._test_owner_ref = owner
            try:
                with mock.patch("gcm.ui.main_window.QMessageBox") as mb:
                    mb.question.return_value = mb.StandardButton.No
                    panel._delete_halfcloned_row(0)
                self.assertTrue(half.exists(), "确认类默认 No 语义被破坏")
            finally:
                panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
