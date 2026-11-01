"""G80-B2 工程卫生守卫：裸吞异常预算 / 死符号 / 单一实现 / i18n 对称。

为什么需要预算而不是清零
------------------------
`gcm/` 里 568 个 `except` 中有 283 个是 `pass`-only。全清完会碰 20+ 个文件、回归面过大；
但「继续增长」是必须拦住的（CLAUDE.md §3 明令禁止静默吞异常）。
所以这里用**预算制**：数字只能往下走，新增裸吞立即红——把"逐步收敛"变成可强制的约束。
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GCM = ROOT / "gcm"

# 实测基线。两条都只能降不能升：
# - B2 修完之后是 except=567 / pass-only=280；
# - B8 新增 7 个 handler（engine._repo_ssh_key 读库失败降级、ui 三个保存/浏览失败提示、
#   util/sshkey 的读文件与 stat 失败），**全部会记日志或弹提示**，没有一个是裸吞，
#   所以 except 基线抬到 574；pass-only 仍是 280（这条才是真正的红线）。
# - B4-1 再加 2 个（service._git_out 探针失败返回 -1、_probe_checkout 的 OSError），
#   都是「探针失败按未知处理」而非静默吞错误，pass-only 依然没涨。
# - B5 再加 3 个（设置校验/退避保存回调的提示性 handler，无裸吞）；
# - B4-1b 又加 2 个（scanner._looks_halfcloned 与 halfclone_actions.looks_halfcloned
#   的 OSError 兜底）——这是同一类「探测失败返回 False」的防御分支，也不是裸吞。
# - **跨平台口径说明**：POSIX 下 msvcrt/fcntl 平台分支多 1 个 pass-only
#   （本机 Windows 数不到，CI macos/ubuntu 才可见）→ 基线以 281 为准。
# - B7-1 再增加若干（后台任务登记的 try 包裹），同样全部带日志/降级，pass-only 不变。
# - B7-3 S4b 再增加 1 个（创建队列 UI 的 except ValueError → UI 警告，带日志语义）
# - B7-3 S3 再增加 4 个（队列登记/推送失败/引擎忙闲查询失败/tick 槽兜底），
#   全部带日志或降级路径，pass-only 仍是 280 没动。
# - G81（N-6 仓库画像 + N-5 可解释字段）再增加 11 个，**全部是带日志或降级路径的
#   handler，pass-only 未涨（281 不变）**：
#     knowledge.py +4（探测边界 OSError / README 读取 / package.json 解析 / Cargo.toml 解析）
#     db/repo_profile.py +2（坏 profile_json 退化 / 聚合时跳坏行）
#     app/engine.py +1（画像落库失败 → applog.warn）
#     app/worker.py +2（探测失败 → applog.warn；独立落库失败 → applog.warn）
#     ui/repo_detail_dialog.py +1（无 db/坏数据 → 返回空画像，详情页照常打开）
#     ui/statistics_dialog.py +1（非 list 返回值 → 当无数据处理）
#   依据：新增能力不可能零异常处理；红线是「不许静默吞」，不是「不许写 except」。
# - G82（A5-4）+1：settings_panel._clean_g53_history 的 except (TypeError, ValueError)
#   → days=90 降级路径（有出口非静默）。
# - G82 终局修复 +4（except 614 / pass-only 283）：
#   worker.py +1（闸门 acquire 异常 → 降级不限流，绝不卡死任务）
#   statistics_dialog.py +3（QThread 聚合三段 try → 全部有「不可用」兜底渲染出口；
#     其中 pass-only +2 是空画像/存储两处静默退化 —— 渲染层无日志面板可写，
#     退化为 UI 兜底文案即「可观测出口」，符合红线原意）
#   本次所有 handler 都有可观测出口，因此只上调总数、不上调 pass-only。
BASELINE_EXCEPT = 614
BASELINE_PASS_ONLY = 283


def _py_files() -> list[Path]:
    return sorted(p for p in GCM.rglob("*.py") if "__pycache__" not in str(p))


def _handlers(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            yield node


def _pass_only(node: ast.ExceptHandler) -> bool:
    return len(node.body) == 1 and isinstance(node.body[0], ast.Pass)


class TestBareExceptBudget(unittest.TestCase):
    def test_pass_only_except_does_not_grow(self):
        total = 0
        for p in _py_files():
            total += sum(1 for h in _handlers(p) if _pass_only(h))
        self.assertLessEqual(
            total, BASELINE_PASS_ONLY,
            f"裸吞异常从 {BASELINE_PASS_ONLY} 涨到 {total}：请改成「记日志」或「给可操作提示」，"
            "确需保留请在 CLAUDE.md §3 登记理由并下调本预算（预算只能降不能升）",
        )

    def test_except_total_does_not_grow(self):
        total = sum(len(list(_handlers(p))) for p in _py_files())
        self.assertLessEqual(total, BASELINE_EXCEPT,
                             f"except 总数从 {BASELINE_EXCEPT} 涨到 {total}")

    def test_result_persist_failure_is_logged(self):
        """engine._on_result 的落库失败必须留痕（修复前注释谎称记日志，实际 pass）。"""
        src = (GCM / "app" / "engine.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        target = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "_on_result")
        passes = [h for h in ast.walk(target)
                  if isinstance(h, ast.ExceptHandler) and _pass_only(h)]
        self.assertEqual(passes, [], "_on_result 里不得再出现 pass-only 异常处理")
        self.assertIn("结果落库失败", src)


class TestDeadSymbolsStayDead(unittest.TestCase):
    """B2 删掉的符号不得复活（复活通常意味着旧代码被整段粘回来）。"""

    REMOVED = (
        "iter_sse_frames",
        "is_retryable_message",
        "_is_known_host",
        "summarize_events",
        "_is_detached",
    )

    def test_removed_functions_do_not_reappear(self):
        """符号不得以**可执行定义**复活。changelog（gcm/__init__.py 的 __changelog__）
        里出现名字是**历史记录**，不是代码 —— 扫描时排除它，否则守卫会被自己的
        变更说明误伤（G80-B2 删符号当轮就写过 changelog，下一轮守卫转红）。"""
        def _is_changelog(p: Path) -> bool:
            # G81：changelog 已从 gcm/__init__.py 拆到 gcm/_changelog_data.py
            # （纯数据模块，由 scripts/changelog.py build 生成）。两处都要排除 ——
            # 判据是「这个文件只承载历史文本，不可能是活代码」，而不是文件名。
            return p.parent == GCM and p.name in ("__init__.py", "_changelog_data.py")

        blobs = {p: p.read_text(encoding="utf-8") for p in _py_files() if not _is_changelog(p)}
        for name in self.REMOVED:
            for path, src in blobs.items():
                self.assertNotIn(name, src, f"{name} 又出现在 {path.relative_to(ROOT)}")

    def test_token_module_is_gone(self):
        self.assertFalse((GCM / "app" / "token.py").exists(),
                         "gcm/app/token.py 已删除（0 importer），不要重新加回来")

    def test_instance_host_token_method_is_gone(self):
        src = (GCM / "git" / "service.py").read_text(encoding="utf-8")
        self.assertNotIn("def _host_token(", src,
                         "实例方法 _host_token 已删；注意别误删静态方法 resolve_host_token")
        self.assertIn("def resolve_host_token(", src, "resolve_host_token 是活的，不能删")

    def test_classify_error_is_still_the_single_retry_judge(self):
        src = (GCM / "app" / "errors.py").read_text(encoding="utf-8")
        self.assertIn("def classify_error(", src)
        self.assertNotIn("def is_retryable_message(", src,
                         "重试判据必须唯一（app.errors.classify_error）")


class TestSingleImplementation(unittest.TestCase):
    def test_only_openfolder_touches_startfile(self):
        hits = {p.relative_to(ROOT).as_posix() for p in _py_files()
                if "os.startfile" in p.read_text(encoding="utf-8")}
        self.assertEqual(hits, {"gcm/util/openfolder.py"},
                         "「打开目录」必须只有一处实现（gcm/util/openfolder.py）")

    def test_openfolder_has_no_qt_dependency(self):
        src = (GCM / "util" / "openfolder.py").read_text(encoding="utf-8")
        self.assertNotIn("PyQt6", src, "util 层不得依赖 Qt（CLAUDE.md §2）")


class TestNewI18nKeys(unittest.TestCase):
    def test_cannot_open_folder_key_exists_in_both_languages(self):
        zh = (GCM / "i18n" / "zh.py").read_text(encoding="utf-8")
        en = (GCM / "i18n" / "en.py").read_text(encoding="utf-8")
        self.assertIn("'无法打开目录'", zh)
        self.assertIn("'无法打开目录'", en)


if __name__ == "__main__":
    unittest.main()
