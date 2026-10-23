"""G68-1 设置页拆分守卫：控件集合、分组结构、**配置值真的被读出**。

## 为什么需要这个文件（真实教训）
`build_settings_ui` 原本是**单个 432 行函数**（项目 50 行上限的 8.6 倍），
拆成「外壳 + 7 个分组构建器」（`gcm/ui/settings_groups.py`）时，我用脚本按行区间搬运代码 ——
**边界差一行，把 `_custom_hosts_text` 的函数体整段漏掉了**，它变成静默 `return None`，
「常用主机」设置页永远空白。

更值得记的是**为什么第一次没抓住**：我写了个「新旧两条路径构造的控件对比」脚本做等价性验证，
但用的是**默认配置** —— 默认 `custom_hosts` 是空元组，正确实现返回 `""`、坏实现返回 `None`，
`QLineEdit` 两者都显示空串，**看起来一致**。把配置换成非默认值后才暴露。

所以本文件的核心是 `test_configured_values_reach_widgets`：
**每一项设置都写成非默认值，断言它真的显示在对应控件上**。
没有它，任何「读配置的代码被悄悄删掉」都不会有测试变红。
"""
from __future__ import annotations

import os
import pathlib
import re
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])

# 设置页必须存在的控件（拆分/重构时被误删即红灯）。
EXPECTED_WIDGETS = (
    # 启动与后台运行
    "ck_autostart", "ck_start_min", "ck_auto_clear", "ck_clipboard",
    "ck_finish_sound", "ck_animations",
    # G76 接线收口（关窗行为 / 数据目录 / clone 超时 / 克隆后钩子 / 静默续跑 / 报表格式）
    "ck_minimize_to_tray", "lbl_data_dir", "btn_migrate_data_dir",
    "spin_clone_timeout", "edit_post_hook",
    "ck_watchdog_silent", "combo_sched_format",
    # 并行与网络
    "spin_concurrency", "spin_fetch_timeout", "spin_retries", "edit_proxy",
    "btn_detect_proxy", "spin_rate", "ck_unshallow", "ck_submodule", "ck_lfs",
    "spin_auto_update", "edit_token", "edit_host_tokens", "ck_show_host_tokens",
    "cb_auth_header", "edit_ssh_key", "btn_ssh_key", "edit_mirror",
    "edit_custom_hosts", "ck_precheck", "ck_single_branch", "ck_force_ipv4",
    # 外观
    "theme_combo", "lang_combo", "accent_combo", "ck_reduced_motion",
    "btn_font_reset", "btn_reset_defaults",
    # 关于与更新
    "lbl_version", "btn_check_update", "btn_selftest", "btn_diag",
    "btn_statistics", "btn_export", "btn_help", "btn_open_data",
    # Webhook
    "ck_notify", "edit_webhook_url", "edit_webhook_token",
    # G53 数据洞察
    "ck_g53_alerts", "spin_g53_interval", "spin_g53_history",
    "btn_g53_clean", "ck_g53_sched",
    # 外壳
    "settings_search", "settings_scroll",
)

EXPECTED_GROUP_TITLES = (
    "启动与后台运行（开机自启 / 托盘）",
    "并行与网络（并发 / 超时 / 重试 / 代理 / Token）",
    "外观（主题）",
    "关于与更新",
    "完成通知 Webhook",
    # G71-9：GUI 内置本地只读 API（含 SSE 事件流）
    "本地 API / 事件流",
)
# G75：分组标题去掉工程代号（"G53 "）—— 工程编号对用户零价值，且会漏进左导航
EXPECTED_EXTRA_TITLES = ("数据洞察（告警 / 历史保留 / 定时报表）",)


def _non_default_store(data_dir: pathlib.Path):
    """构造一份**每一项都非默认**的设置，用于验证「配置真的被读进控件」。"""
    from gcm.db.settings import SettingsStore

    store = SettingsStore(data_dir / "settings.json")
    s = store.load()
    s.concurrency = 17
    s.fetch_timeout = 111
    s.retries = 3
    s.proxy = "http://127.0.0.1:7890"
    s.rate_limit_kbps = 512
    s.fetch_unshallow = True
    s.submodule = True
    s.lfs_enabled = True
    s.auto_update_minutes = 45
    s.host_tokens = {"gitlab.com": "glpat-XXX", "gitea.corp": "tok2"}
    s.ssh_key_path = r"C:\Users\me\.ssh\id_ed25519"
    s.mirror_prefix = {"github.com": "https://ghproxy.com"}
    s.custom_hosts = ("gitlab.corp.example", "gitea.internal")
    s.precheck_remote = True
    s.single_branch = True
    s.force_ipv4 = True
    s.theme = "nord"
    s.language = "en"
    s.accent_preset = "violet"
    s.animations = False
    s.start_minimized = True
    s.notify_webhook_url = "https://hooks.example.com/x"
    s.notify_webhook_token = "whsec_ABC"
    store.save(s)
    return store


class SettingsUiCase(unittest.TestCase):
    """共享的窗口构造（每个用例独立临时目录，避免互相污染）。"""

    def _make_window(self, store=None):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        w = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                       settings=store or SettingsStore(self.tmp / "settings.json"))
        # 测试隔离：`__init__` 里挂了 `singleShot(2500, _check_update_silent)` 与
        # `singleShot(300, _maybe_show_onboarding)`。不掐掉它们，窗口在本用例结束后
        # 仍可能被**后续模块**的事件泵推到点 —— 实测过一次真事故：一个迟到的
        # `update_checked` 信号让 `test_progress_ui` 挂死 7 分钟。
        w._check_update_silent = lambda *a, **k: None
        w._maybe_show_onboarding = lambda *a, **k: None
        w._maybe_apply_system_theme = lambda *a, **k: None
        return w

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g68_sui_"))
        self.win = self._make_window()

    def tearDown(self):
        try:
            self.win.close()
            self.win.deleteLater()
        except Exception:
            pass
        _app.processEvents()


class TestSettingsWidgetContract(SettingsUiCase):
    """控件与分组结构契约：拆分/重构不得悄悄丢控件。"""

    def test_all_expected_widgets_exist(self):
        missing = [n for n in EXPECTED_WIDGETS if not isinstance(getattr(self.win, n, None), QWidget)]
        self.assertEqual(missing, [], f"设置页缺少控件：{missing}")

    def test_group_structure_is_stable(self):
        titles = tuple(g.title() for g in self.win.settings_groups)
        self.assertEqual(titles, EXPECTED_GROUP_TITLES)
        extra = tuple(g.title() for g in self.win.settings_groups_extra)
        self.assertEqual(extra, EXPECTED_EXTRA_TITLES)
        # G53-2 约定：扩展组不进 groups（否则会破坏既有解构）
        self.assertNotIn(EXPECTED_EXTRA_TITLES[0], titles)
        # G75：用户可见标题里不得出现内部工程代号（G数字-数字）
        import re as _re
        for title in titles + extra:
            self.assertIsNone(_re.search(r"G\d+-?\d*", title),
                              f"分组标题暴露了内部代号：{title}")

    def test_every_group_has_a_tooltip(self):
        """分组 tooltip 是小白唯一的说明来源，空 tooltip 等于没解释。"""
        empty = [g.title() for g in self.win.settings_groups if not g.toolTip().strip()]
        self.assertEqual(empty, [], f"这些分组没有 tooltip：{empty}")


class TestConfiguredValuesReachWidgets(SettingsUiCase):
    """**核心**：写非默认配置 → 构造窗口 → 断言值真的显示在控件上。

    这一条就是「`_custom_hosts_text` 函数体被漏掉」的直接克星：
    空配置下坏实现与好实现都显示空串，只有非默认值能区分。
    """

    def setUp(self):
        super().setUp()
        # 顺序很重要：`closeEvent` 会 `settings_store.save()`（持久化窗口几何），
        # 所以必须**先关旧窗口、再写非默认配置**，否则刚写进去的配置会被旧窗口覆盖。
        try:
            self.win.close()
            self.win.deleteLater()
        except Exception:
            pass
        _app.processEvents()
        self.store = _non_default_store(self.tmp)
        self.win = self._make_window(self.store)

    def test_configured_values_reach_widgets(self):
        w = self.win
        checks = [
            ("spin_concurrency", lambda x: x.value(), 17),
            ("spin_fetch_timeout", lambda x: x.value(), 111),
            ("spin_retries", lambda x: x.value(), 3),
            ("edit_proxy", lambda x: x.text(), "http://127.0.0.1:7890"),
            ("spin_rate", lambda x: x.value(), 512),
            ("ck_unshallow", lambda x: x.isChecked(), True),
            ("ck_submodule", lambda x: x.isChecked(), True),
            ("ck_lfs", lambda x: x.isChecked(), True),
            ("spin_auto_update", lambda x: x.value(), 45),
            ("edit_ssh_key", lambda x: x.text(), r"C:\Users\me\.ssh\id_ed25519"),
            # ↓ 这三项是本测试存在的理由：文本拼装函数一旦被删/写坏，只有非默认值能暴露
            ("edit_mirror", lambda x: x.text(), "github.com=https://ghproxy.com"),
            ("edit_custom_hosts", lambda x: x.text(), "gitlab.corp.example, gitea.internal"),
            ("ck_precheck", lambda x: x.isChecked(), True),
            ("ck_single_branch", lambda x: x.isChecked(), True),
            ("ck_force_ipv4", lambda x: x.isChecked(), True),
            ("ck_animations", lambda x: x.isChecked(), False),
            ("ck_start_min", lambda x: x.isChecked(), True),
            ("edit_webhook_url", lambda x: x.text(), "https://hooks.example.com/x"),
            ("edit_webhook_token", lambda x: x.text(), "whsec_ABC"),
        ]
        bad = []
        for name, get, want in checks:
            widget = getattr(w, name, None)
            self.assertIsNotNone(widget, f"控件 {name} 不存在")
            got = get(widget)
            if got != want:
                bad.append(f"{name}: 期望 {want!r}，实际 {got!r}")
        self.assertEqual(bad, [], "配置值没有正确显示在控件上：\n  " + "\n  ".join(bad))

    def test_host_tokens_and_webhook_token_are_masked(self):
        """凭据类输入框必须是密码回显（默认不回显明文）。"""
        from PyQt6.QtWidgets import QLineEdit

        w = self.win
        for name in ("edit_token", "edit_host_tokens", "edit_webhook_token"):
            self.assertEqual(getattr(w, name).echoMode(), QLineEdit.EchoMode.Password,
                             f"{name} 未使用密码回显 —— 凭据会在界面上明文可见")

    def test_multi_line_settings_render_one_per_line(self):
        """`host_tokens` / `mirror_prefix` 每项一行（少了换行会把多个主机挤成一条）。"""
        txt = self.win.edit_host_tokens.text()
        self.assertEqual(txt.splitlines(), ["gitlab.com=glpat-XXX", "gitea.corp=tok2"])
        self.assertEqual(self.win.edit_mirror.text().splitlines(),
                         ["github.com=https://ghproxy.com"])

    def test_combo_boxes_select_configured_option(self):
        w = self.win
        self.assertEqual(w.theme_combo.currentData(), "nord")
        self.assertEqual(w.lang_combo.currentData(), "en")
        self.assertEqual(w.accent_combo.currentData(), "violet")


class TestNoModalFromHiddenWindow(unittest.TestCase):
    """G68-4：**窗口不可见时不得弹模态** —— 无交互界面下 `exec()` 会永久阻塞。

    真实事故（faulthandler 抓到的栈）：
    ```
    main_window.py:930 in _on_update_checked   ← 卡在 QMessageBox.question()
    test_progress_ui.py:129 in _pump           ← 被后续模块的 processEvents 派发
    ```
    成因：`update_checked` 是**跨线程异步**信号（G67-9 改的），某个测试发起检查后
    没等槽执行就结束了；信号在**下一个模块**的事件泵里才送达，此时窗口已 close()、
    测试的 mock 也已退出 → 真实模态弹窗把整个进程挂死 7 分钟以上。

    这里用**线程 + 超时**复现：即使守卫失效，也只是本用例失败，不会挂死套件。
    """

    def _pump_for(self, app, seconds, predicate=None):
        import time as _t
        deadline = _t.monotonic() + seconds
        while _t.monotonic() < deadline:
            app.processEvents()
            if predicate is not None and predicate():
                return True
            _t.sleep(0.01)
        return predicate() if predicate else True

    def test_hidden_window_does_not_open_modal_and_cannot_hang(self):
        import threading
        from unittest import mock

        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g684_"))
        win = MainWindow(data_dir=tmp, db=Database(tmp / "t.db"),
                         settings=SettingsStore(tmp / "s.json"))
        win._check_update_silent = lambda *a, **k: None
        win.show()
        _app.processEvents()
        win.close()          # ← 关键：窗口已关闭（信号却还会迟到）
        _app.processEvents()

        done = threading.Event()
        calls: list = []

        def _work():
            with mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
                try:
                    win._on_update_checked(False, "", "", "")
                finally:
                    calls.append(m_msg.information.called)
                    done.set()

        th = threading.Thread(target=_work, daemon=True)
        th.start()
        # 守卫生效 → 立刻返回；守卫失效 → 卡在真实模态里，5s 后本用例失败（不挂死套件）
        self.assertTrue(done.wait(5),
                        "窗口已关闭却仍去弹模态 —— 在 offscreen/CI 下会永久阻塞进程")
        self.assertEqual(calls, [False], "不可见窗口不应调用 QMessageBox")

    def test_visible_window_still_attempts_dialog(self):
        """反向约束：窗口**可见**时照常弹（别把守卫做得太宽而吞掉正常提示）。"""
        from unittest import mock

        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g684v_"))
        win = MainWindow(data_dir=tmp, db=Database(tmp / "t.db"),
                         settings=SettingsStore(tmp / "s.json"))
        win._check_update_silent = lambda *a, **k: None
        win.show()
        _app.processEvents()
        try:
            with mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
                m_msg.StandardButton.Yes = __import__(
                    "PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes
                win._on_update_checked(False, "", "", "")
                self.assertTrue(m_msg.information.called,
                                "可见窗口应照常弹「已是最新」提示")
            self.assertFalse(win._update_checking,
                             "无论走哪条分支都必须复位防连点标记")
        finally:
            win.close()
            win.deleteLater()
            _app.processEvents()


class TestSettingsModuleSplit(unittest.TestCase):
    """G68-1 拆分本身的守卫：模块边界与函数规模。"""

    def _lines(self, rel):
        return len((ROOT / rel).read_text(encoding="utf-8").splitlines())

    def test_panel_reexports_builder(self):
        """`settings_panel` 必须原样再导出 `build_settings_ui`（main_window 的导入路径不变）。"""
        import gcm.ui.settings_panel as sp

        self.assertTrue(callable(getattr(sp, "build_settings_ui", None)))
        src = (ROOT / "gcm/ui/settings_panel.py").read_text(encoding="utf-8")
        self.assertIn("from .settings_groups import build_settings_ui", src,
                      "再导出被删 —— main_window 的 `from .settings_panel import build_settings_ui` 会断")

    def test_both_modules_under_line_cap(self):
        """两个模块都必须在 800 行硬上限内（拆分的目的就是这个）。"""
        for rel in ("gcm/ui/settings_panel.py", "gcm/ui/settings_groups.py"):
            n = self._lines(rel)
            self.assertLessEqual(n, 800, f"{rel} 又长到 {n} 行（上限 800）")

    def test_builder_functions_are_small(self):
        """`build_settings_ui` 及其分组构建器都不得回到「几百行巨型函数」。"""
        import ast

        tree = ast.parse((ROOT / "gcm/ui/settings_groups.py").read_text(encoding="utf-8"))
        big = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                n = (node.end_lineno or node.lineno) - node.lineno + 1
                if n > 100:
                    big.append(f"{node.name} ({n} 行)")
        self.assertEqual(big, [], f"这些构建器过长（上限 100）：{big}")

    def test_helpers_have_bodies(self):
        """防「搬运时漏掉函数体」——本轮真发生过：`_custom_hosts_text` 变成空函数。

        一个只有 docstring、没有实际语句的函数，在这里直接报错。
        """
        import ast

        tree = ast.parse((ROOT / "gcm/ui/settings_groups.py").read_text(encoding="utf-8"))
        hollow = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                body = [s for s in node.body
                        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
                if not body:
                    hollow.append(f"{node.name} (line {node.lineno})")
        self.assertEqual(hollow, [], f"这些函数只有 docstring 没有实现（搬运漏了函数体）：{hollow}")


if __name__ == "__main__":
    unittest.main()
