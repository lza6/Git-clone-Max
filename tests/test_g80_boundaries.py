"""G80-B6 边界守卫：**冻结三条边界不再恶化**（只加守卫，不搬一行生产代码）。

三条边界（对应差距 G-04 / G-06）：
1. **Qt**：`gcm/app` 层目前 4 个文件耦合 PyQt6（历史原因），新代码不得再引入；
2. **subprocess**：不得再散落到 `gcm/ui`，且 `["git"` 字面量不得出现在 `gcm/git/` 之外的新文件；
3. **SQL**：只允许出现在 `gcm/db/`（唯一例外是已登记的 `reports.py`，见白名单理由）。

为什么用**白名单 + 集合相等**而不是"黑名单匹配"：黑名单只能挡住你想到的名字，
白名单能挡住"悄悄多出来的第 N+1 处" —— 这是本项目 `test_g79_repo_hygiene` 已验证有效的形态。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GCM = ROOT / "gcm"

# 白名单 1：app 层允许 import PyQt6 的现存文件（**不再增加**）
QT_IN_APP = {
    "gcm/app/engine.py": "QThreadPool/QTimer/pyqtSignal 驱动批次（历史耦合，搬迁属 L3）",
    "gcm/app/worker.py": "QRunnable + pyqtSignal（与 engine 配套的 QRunnable 模型）",
    "gcm/app/watchdog.py": "QTimer 定时看门狗",
    "gcm/app/clipboard_watcher.py": "QTimer 轮询剪贴板 + QApplication 取文本",
    "gcm/app/diag.py": "诊断报告里取 QT_VERSION_STR 用于环境自检展示（纯版本信息，不驱动 UI）",
}

# 白名单 2：gcm/git/ 之外仍直接起 git 子进程的文件（**不再增加**）
# 每条都必须写清"为什么现在不进 git 层" —— 没有正当理由就该在 B6 之后收敛。
GIT_SUBPROCESS_OUTSIDE = {
    "gcm/app/diag.py": "git --version 环境自检（只读探测，无同步语义）",
    "gcm/app/host_probe.py": "ls-remote 匿名可读性探测（host 识别用，非克隆）",
    "gcm/app/mirror_probe.py": "镜像测速 ls-remote（自带独立超时/缓存/单飞，语义不同于同步）",
    "gcm/app/scanner.py": "扫描时读 origin/HEAD（纯文件/只读 git 查询，非同步）",
    "gcm/app/updater.py": "git credential fill 取 GitHub token（凭据读取，非仓库操作）",
    "gcm/ui/main_window.py": "自检面板里 git --version（诊断展示，已有 B2 收敛计划）",
    "gcm/ui/repo_detail_dialog.py": "仓库详情「检查远端」ls-remote（只读）",
}

# 白名单 3：gcm/db/ 之外执行 SQL 的现存文件（**不再增加**）
SQL_OUTSIDE_DB = {
    "gcm/reports.py": "报表 LEFT JOIN 查询（历史遗留，应改为调 repo_db 公开方法，已登记）",
}

# 白名单 4：gcm/ui 内仍直接使用 subprocess 的文件（**不再增加**）
UI_SUBPROCESS = {
    "gcm/ui/main_window.py": "自检面板 git --version（诊断）",
    "gcm/ui/repo_detail_dialog.py": "检查远端 ls-remote（只读）",
}


def _py_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in str(p))


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports_pyqt6(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.startswith("PyQt6") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("PyQt6"):
                return True
    return False


def _uses_git_literal(tree: ast.Module) -> bool:
    """是否出现把 "git" 作为子进程命令的列表字面量（["git", ...]）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "git":
                return True
    return False


def _uses_subprocess(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("run", "Popen", "call", "check_output"):
            if isinstance(node.value, ast.Name) and node.value.id == "subprocess":
                return True
    return False


def _executes_sql(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("execute", "executemany", "executescript"):
            return True
    return False


class TestQtBoundary(unittest.TestCase):
    def test_app_layer_qt_importers_are_exactly_whitelisted(self):
        hits = {_rel(p) for p in _py_files(GCM / "app") if _imports_pyqt6(_parse(p))}
        self.assertEqual(hits, set(QT_IN_APP),
                         "app 层 Qt 依赖有变化。新增耦合：请改用回调/信号解耦，或先把设计发给评审；"
                         "移除耦合：请同时删掉白名单里对应条目")
        for name, reason in QT_IN_APP.items():
            self.assertTrue((ROOT / name).is_file(), f"白名单指向不存在的文件 {name}")
            self.assertTrue(reason.strip(), f"{name} 的白名单理由不能为空")


class TestSubprocessBoundary(unittest.TestCase):
    def test_no_new_git_subprocess_outside_git_layer(self):
        hits = set()
        for p in _py_files(GCM):
            if _rel(p).startswith("gcm/git/"):
                continue
            if _uses_git_literal(_parse(p)):
                hits.add(_rel(p))
        self.assertEqual(hits, set(GIT_SUBPROCESS_OUTSIDE),
                         "git 子进程调用点有变化。新增：请优先复用 gcm/git/service.py 的封装"
                         "（那里统一了 encoding、取消、超时、镜像与凭据边界）")

    def test_ui_subprocess_is_not_growing(self):
        hits = {_rel(p) for p in _py_files(GCM / "ui") if _uses_subprocess(_parse(p))}
        self.assertEqual(hits, set(UI_SUBPROCESS),
                         "UI 里新增子进程调用。长任务请走 worker 线程 + 信号，"
                         "「打开目录」请用 gcm/util/openfolder.py")


class TestSqlBoundary(unittest.TestCase):
    def test_sql_only_lives_in_db_layer(self):
        hits = set()
        for p in _py_files(GCM):
            if _rel(p).startswith("gcm/db/"):
                continue
            if _executes_sql(_parse(p)):
                hits.add(_rel(p))
        self.assertEqual(hits, set(SQL_OUTSIDE_DB),
                         "SQL 越层了：查询请放到 gcm/db 的公开方法里（reports.py 是已登记的历史例外）")


class TestGuardsAreNotSelfDefeating(unittest.TestCase):
    """反向自检：白名单机制本身能抓到违规（否则这些守卫等于装饰）。"""

    def test_ast_helpers_detect_violations(self):
        bad_qt = _imports_pyqt6(ast.parse("from PyQt6.QtCore import QObject\n"))
        self.assertTrue(bad_qt)
        bad_git = _uses_git_literal(ast.parse('subprocess.run(["git", "ls-remote", u])'))
        self.assertTrue(bad_git)
        bad_sql = _executes_sql(ast.parse("db._conn.execute('SELECT 1')"))
        self.assertTrue(bad_sql)
        good = _imports_pyqt6(ast.parse("import sqlite3\n"))
        self.assertFalse(good)


if __name__ == "__main__":
    unittest.main()
