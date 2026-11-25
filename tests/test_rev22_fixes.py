"""Rev22 守卫：字形安全 + 管理表 i18n + 统计对话框线程生命周期。

三个真实事故（2026-11-26 终极审计轮实测发现）：

1. `历史 ▾` 的 U+25BE 在微软雅黑无字形（Geometric Shapes 小三角区段
   U+25B2–25BF 整体缺失），离屏截图渲染为 tofu；同文件 180 行外已记录过
   U+25B8 的同根因修复（「更多 ▸」），第二次漏网。→ AST 扫描 gcm 非
   docstring 字符串常量，禁用该区段。
2. `manage_model._HEADERS` 用 `tr(变量)` 模式（8 个列头），避开
   `test_i18n_coverage` 只查 `tr("字面量")` 的口径 → `作者/仓库`、`上次同步`、
   `HEAD`、`历史` 四个键中英字典双缺，英文模式列头显示中文。
3. `StatisticsDialog` 的聚合线程 `finished→deleteLater` 之后 wrapper 已删，
   `closeEvent` 里 `t.isRunning()` 抛 RuntimeError → PyQt6 abort
   （0xC0000409）：统计对话框聚合完成后关闭即闪退。截图审计脚本固定在
   `dialog_statistics` 之后中止、后 4 个对话框从未被截到，正是它的证据。

每条都有反向自检（守卫必须能红，`.agent/rules.md` §8）。
"""
from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 项目铁律：直接构造 QWidget 的测试，模块级先建 QApplication
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

# Geometric Shapes 里的大/小三角区段：微软雅黑缺字形（实测 U+25B8/U+25BE），
# 渲染为 tofu。新增字形敏感字符时把码点加进这里，不要只写注释。
FORBIDDEN_RANGES = ((0x25B2, 0x25BF), (0x25C0, 0x25C4))
# 历史文本豁免：changelog 记载的是当年按钮文字（考古用，不进 UI 渲染）。
_SCAN_EXEMPT = {"gcm/_changelog_data.py"}


def _is_forbidden_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in FORBIDDEN_RANGES)


def scan_triangle_in_source(text: str) -> list[str]:
    """返回源码中「非 docstring 字符串常量」里出现的禁字（行号:字符）。

    只扫 `ast.Constant` 的字符串值：注释天然不在 AST 里；docstring 属于
    描述性文本（含历史说明「更多 ▸」），按节点形态排除。
    """
    tree = ast.parse(text)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    hits: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            for ch in node.value:
                if _is_forbidden_char(ch):
                    hits.append(f"line {node.lineno}: U+{ord(ch):04X}")
    return hits


class TestGlyphSafety(unittest.TestCase):
    def test_no_triangle_chars_in_gcm_string_constants(self):
        offenders: list[str] = []
        for p in (ROOT / "gcm").rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel in _SCAN_EXEMPT or rel.startswith("gcm/i18n/"):
                continue  # i18n 值随 tr 键生成，源头在 ui 调用点（已扫）
            offenders.extend(
                f"{rel} {hit}" for hit in
                scan_triangle_in_source(p.read_text(encoding="utf-8")))
        self.assertEqual(
            offenders, [],
            "Geometric Shapes 三角在微软雅黑无字形会渲染成 tofu"
            "（历史事故：✓✕⚠⊘ / 更多 ▸ / 历史 ▾）：\n  " + "\n  ".join(offenders))

    def test_scanner_catches_planted_defect(self):
        """反向自检：扫描器必须真的能红；docstring 豁免也必须真的生效。"""
        planted = 'x = "历史 ▾"\n'
        self.assertEqual(len(scan_triangle_in_source(planted)), 1)
        in_doc = 'def f():\n    """说明里允许出现 ▸ 作为历史记载"""\n    return 1\n'
        self.assertEqual(scan_triangle_in_source(in_doc), [])

    def test_history_button_text_is_plain(self):
        """`历史` 按钮的显示文本必须是纯文本（B1 修复钉死）。"""
        src = (ROOT / "gcm/ui/download_layout.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setText"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "btn_recent"):
                found.append(node)
        self.assertTrue(found, "download_layout 里找不到 btn_recent.setText 调用")
        arg = found[0].args[0]
        # tr("历史") → Call(args[0] = Constant("历史"))
        text = (arg.args[0].value if isinstance(arg, ast.Call)
                and getattr(arg.func, "id", "") == "tr" else None)
        self.assertEqual(text, "历史", "历史按钮文本必须是不含装饰字符的纯文本")


class TestManageTableHeadersI18n(unittest.TestCase):
    """`tr(变量)` 是 i18n 守卫声明过的盲区 —— 这里单独钉住管理表列头。"""

    def _header_keys(self) -> list[str]:
        tree = ast.parse(
            (ROOT / "gcm/ui/manage_model.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign)
                    and any(getattr(t, "id", "") == "_HEADERS" for t in node.targets)):
                return [c.value for c in ast.walk(node.value)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)]
        self.fail("manage_model 里找不到 _HEADERS 赋值")

    def test_all_header_keys_in_both_dicts(self):
        from gcm.i18n.en import EN
        from gcm.i18n.zh import ZH
        keys = self._header_keys()
        self.assertEqual(len(keys), 8, f"列头数量变了？实际 {keys}")
        missing_en = [k for k in keys if k not in EN]
        missing_zh = [k for k in keys if k not in ZH]
        self.assertEqual(missing_en, [], f"EN 字典缺管理表列头键：{missing_en}")
        self.assertEqual(missing_zh, [], f"ZH 字典缺管理表列头键：{missing_zh}")


class TestStatisticsDialogLifecycle(unittest.TestCase):
    """统计对话框：聚合线程 wrapper 被 deleteLater 删除后，close/刷新不得崩。

    历史缺陷（0xC0000409）：closeEvent → `t.isRunning()` on deleted wrapper
    → RuntimeError → PyQt6 abort。
    """

    def _make_dialog(self):
        from PyQt6 import sip

        from gcm.db.repo_db import Database
        from gcm.ui.statistics_dialog import StatisticsDialog
        tmp = tempfile.mkdtemp(prefix="gcm_rev22_stats_")
        db = Database(os.path.join(tmp, "repos.db"))
        dlg = StatisticsDialog(db=db)
        dlg.show()
        return dlg, db, sip

    def test_close_after_aggregation_finished_is_safe(self):
        from PyQt6.QtTest import QTest
        dlg, db, sip = self._make_dialog()
        try:
            QTest.qWait(400)  # 空库聚合应已完成 → finished→deleteLater 执行
            self.assertTrue(sip.isdeleted(dlg._agg_thread),
                            "前置不成立：聚合线程未被 deleteLater 删除，"
                            "此时测不到 wrapper-deleted 崩溃路径")
            dlg.close()  # 修复前：RuntimeError → 进程 abort
        finally:
            db.close()

    def test_week_switch_after_finished_is_safe(self):
        from PyQt6.QtTest import QTest
        dlg, db, sip = self._make_dialog()
        try:
            QTest.qWait(400)
            self.assertTrue(sip.isdeleted(dlg._agg_thread))
            dlg._run_scorecard_async()  # 修复前：old.isRunning() 抛 RuntimeError
            QTest.qWait(300)
            dlg.close()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
