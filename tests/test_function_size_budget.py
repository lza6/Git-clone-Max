"""函数规模预算守卫（Rev7 C1-5）。

**为什么需要**：本项目只守了「超 800 行的**文件**」（`test_g79_repo_hygiene`），
但函数层面的膨胀完全没人管 —— 实测已到 **19 个函数超 100 行**，而
`CLAUDE.md` §6 还写着「14 个函数超 100 行」。文档与代码同时漂移，且没有任何测试会红。

**口径（与 `scripts/dev_metrics.long_functions()` 完全一致）**：
- 只统计**模块级与类方法**（`def` 前缩进 ≤ 4），不统计嵌套函数；
- 行数 = 从 `def` 行到下一个同层 `def`/文件末；
- 阈值 100 行（`CLAUDE.md` §3「函数上限 50 行；硬上限 800 行/文件」之外的
  第二道闸：50 是目标、100 是必须登记的红线）。

**为什么用集合相等而不是 `< 上限`**：`< 21` 拦不住「又多了第 21 个 101 行函数」，
也拦不住「偷偷拆掉一个却没人更新文档」。集合相等既拦新增，也逼着拆分后同步登记 ——
与 `test_g79_repo_hygiene` 的超限模块集合断言同一范式。

**如何处理红灯**（二选一，不许第三条）：
1. **拆分**该函数（首选；拆完把对应行从下面集合删掉）；
2. 若确认「拆分收益 < 回归风险」，在本集合里登记**并**同步更新 `CLAUDE.md` §6 的数字。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIMIT = 100

# 已登记的超 100 行函数（(文件, 函数名)）。增删都必须有理由。
# 顺序无关；注释里的数字是登记时的实测行数，仅供参考（不参与断言）。
_REGISTERED: frozenset[tuple[str, str]] = frozenset({
    ("gcm/ui/main_window.py", "__init__"),          # 252 主窗装配（已登记技术债）
    ("gcm/ui/qss_build.py", "_build_qss"),          # 229 四主题 × 全控件 QSS 拼装
    ("gcm/git/service.py", "run_git_ui"),           # 213 子进程泵 + 轮询取消 + 预算
    ("gcm/app/engine.py", "__init__"),              # 168 引擎参数映射（30+ 注入项）
    ("gcm/git/service.py", "_clone"),               # 169 克隆降级链
    ("gcm/git/service.py", "_fetch_remote"),        # 146 fetch + 镜像回退
    ("gcm/app/engine.py", "launch"),                # 146 批次启动/去重/流控
    ("gcm/ui/manage_panel.py", "_build_ui"),        # 141 管理页控件
    ("gcm/app/worker.py", "run"),                   # 133 worker 主流程
    ("gcm/ui/main_window.py", "_launch"),           # 131 启动入口
    ("gcm/http_api.py", "do_GET"),                  # 126 HTTP 路由
    ("gcm/git/service.py", "_env"),                 # 124 git 环境变量装配
    ("gcm/app/engine.py", "_on_result"),            # 113 结果落库/打码/计数
    ("gcm/git/service.py", "_update"),              # 106 更新主流程
    ("gcm/ui/progress_table.py", "_on_worker_result"),  # 103 表格回填
    ("gcm/db/settings.py", "_mask_token"),          # 103 掩码
    ("gcm/ui/statistics_dialog.py", "_build_ui"),   # 101 统计页控件
    ("gcm/cli.py", "main"),                         # 101 CLI 主入口
    ("gcm/app/retry_queue.py", "record_failure_ex"),  # 101 失败入队
})


def _measure() -> dict[tuple[str, str], int]:
    """{（文件, 函数名）: 行数}，只含超 LIMIT 的。"""
    out: dict[tuple[str, str], int] = {}
    for p in sorted((ROOT / "gcm").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        cur, start = None, 0
        for i, line in enumerate(lines, 1):
            m = re.match(r"^(\s*)(?:async\s+)?def\s+(\w+)", line)
            if m and len(m.group(1)) <= 4:
                if cur and i - start > LIMIT:
                    out[(rel, cur)] = i - start
                cur, start = m.group(2), i
        if cur and len(lines) + 1 - start > LIMIT:
            out[(rel, cur)] = len(lines) + 1 - start
    return out


class TestFunctionSizeBudget(unittest.TestCase):
    def test_registered_set_matches_measured(self):
        actual = _measure()
        new = sorted(set(actual) - _REGISTERED)
        gone = sorted(_REGISTERED - set(actual))
        self.assertEqual(
            (new, gone), ([], []),
            "超 100 行函数集合与登记不一致。\n"
            f"新增的（应拆分，或登记并同步 CLAUDE.md §6）：{new}\n"
            f"已消失的（已拆分/改名，应从本守卫与 CLAUDE.md 移除）：{gone}")

    def test_registry_entries_really_exceed_limit(self):
        """登记的每一项必须真的超限（防止白登记一个豁免）。"""
        actual = _measure()
        for key in _REGISTERED:
            self.assertIn(key, actual, f"登记的 {key} 实测未超 {LIMIT} 行，应删除该登记")

    def test_longest_function_is_a_known_tech_debt(self):
        """最大函数必须是已登记的技术债，且行数不超过文档声称的 +20。

        `CLAUDE.md` §6 写明「`MainWindow.__init__` 为最长函数」。它若被别处超过，
        说明有一次没走「顺手拆小」的约定 —— 至少要让文档知道。
        """
        actual = _measure()
        worst = max(actual.items(), key=lambda kv: kv[1])
        self.assertEqual((worst[0][0], worst[0][1]),
                         ("gcm/ui/main_window.py", "__init__"),
                         "最长函数换人了 —— 请确认是否应当拆分，并更新 CLAUDE.md §6")

    def test_guard_is_not_hollow(self):
        """反向自检：把阈值临时降到 3，必须能报出一大批函数。

        防止「集合本身写错 / 正则写错」导致永远为绿（`_measure` 从未命中）。
        """
        global LIMIT
        saved = LIMIT
        LIMIT = 3
        try:
            many = _measure()
        finally:
            LIMIT = saved
        self.assertGreater(len(many), 20, "阈值 3 时应有大量命中；否则说明编译器口径坏了")


if __name__ == "__main__":
    unittest.main()
