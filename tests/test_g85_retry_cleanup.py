# -*- coding: utf-8 -*-
"""G85 守卫：超时重试链三处缺陷的修复闭环。

实测日志（2026-11-14，任务[9] JonyQY/rotation-captcha-solver，LFS 120MiB 撞 600s
clone_timeout）暴露的失败链：

1. `run_git_ui` 超时/网络失败后原样重跑 clone，**重试前不清半成品残留目录**
   → 重试必 `fatal: destination path ... already exists`
   → tail 被污染，`_classify_failure` 误报「目标目录已存在」。
2. `run_git_ui` 的 deadline 在重试循环外计算 → 重试轮共用第一轮的 deadline，
   **重试瞬间被判超时秒死**，超时重试机制从未真正工作。
3. 超时失败没有任何结构化信号进入 tail（`Filtering content` 不含任何网络关键词）
   → `_is_networkish_error` 不命中 → treeless/depth1 降级链不可达；
   引擎侧 `classify_error` 把误报的 already exists 判成 platform 不可重投，
   可恢复的慢网络失败被呈现成终态。

三条守卫各自钉住一处；文案守卫防止「timed out」标记与诚实的 detail 在后续
改文案时被无意删掉。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.git.service import (  # noqa: E402
    GitService,
    _classify_failure,
    _is_networkish_error,
    run_git_ui,
)
from gcm.models import RepoSpec, SyncResult, SyncStatus  # noqa: E402


def _spec(url: str = "https://github.com/a/b.git") -> RepoSpec:
    return RepoSpec(owner="a", repo="b", url_https=url, folder_name="a__b")


class TestDeadlineResetsPerRetry(unittest.TestCase):
    """G85-②：deadline 必须每轮重试重算，否则重试轮秒死。"""

    def test_second_retry_gets_full_timeout(self):
        # Windows 上 taskkill /T 耗时 ~0.85s，纯耗时断言会被 kill 开销淹没
        # （旧实现理论 2+0.2+0.85≈3.05s，恰好压线 3.0 阈值）。
        # 改用「第一轮超时到总结束的间隔」判定：旧实现第二轮瞬间秒死（间隔≈kill+退避
        # ≈1.0s），新实现第二轮获得完整 2s（间隔≈2.0+kill+退避≈3.3s）。1.5s 分界两侧
        # 均有 >0.5s 余量。
        first_timeout_at = [None]

        def mark_first(c):
            if first_timeout_at[0] is None and "timed out" in c.text:
                first_timeout_at[0] = time.time()

        start = time.time()
        rc, _ = run_git_ui(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tempfile.gettempdir(),
            on_line=mark_first,
            timeout=2.0, retries=1, backoff=(0.2,),
        )
        elapsed = time.time() - start
        self.assertNotEqual(rc, 0)
        self.assertIsNotNone(first_timeout_at[0], "必须发出第一轮超时标记")
        gap = elapsed - (time.time() - first_timeout_at[0]) if False else None
        # 直接比较：第一轮超时标记发出后，到整个 run 结束还经历了多久
        gap = elapsed - (first_timeout_at[0] - start)
        self.assertGreaterEqual(
            gap, 1.5,
            f"第一轮超时后 {gap:.2f}s 就结束（应≥1.5s = 第二轮完整 timeout 2s"
            "减去 kill/退避噪声）—— 重试轮秒死说明 deadline 没按轮重置")

    def test_retry_timeout_marker_is_emitted(self):
        # 超时标记行必须发出（G85-③ 的 tail 信号来源）。
        lines: list[str] = []
        run_git_ui(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tempfile.gettempdir(),
            on_line=lambda c: lines.append(c.text),
            timeout=0.3, retries=1, backoff=(0.05,),
        )
        markers = [t for t in lines if "timed out" in t]
        self.assertTrue(
            markers,
            "超时标记行必须包含 'timed out'（_is_networkish_error 靠它把"
            "超时失败归入网络类，treeless/depth1 降级链才可达）")


class TestBeforeRetryHook(unittest.TestCase):
    """G85-①：重试前必须先清半成品残留目录。"""

    def test_before_retry_called_on_timeout(self):
        calls: list[int] = []
        run_git_ui(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tempfile.gettempdir(),
            on_line=lambda c: None,
            timeout=0.3, retries=1, backoff=(0.05,),
            before_retry=lambda: calls.append(1),
        )
        self.assertEqual(calls, [1],
                         "超时重试前必须调用 before_retry 清理残留")

    def test_before_retry_called_on_network_error(self):
        # 命令立刻退出且输出网络类错误 → 走 networkish 重试分支。
        calls: list[int] = []
        run_git_ui(
            [sys.executable, "-c",
             "import sys; print('fatal: unable to access "
             "'\\''https://x/'\\'': Connection reset by peer'); sys.exit(1)"],
            cwd=tempfile.gettempdir(),
            on_line=lambda c: None,
            timeout=10, retries=1, backoff=(0.05,),
            before_retry=lambda: calls.append(1),
        )
        self.assertEqual(calls, [1],
                         "网络类失败重试前必须调用 before_retry 清理残留")

    def test_no_retry_no_hook_call(self):
        # 一次成功：hook 不应被调用。
        calls: list[int] = []
        rc, _ = run_git_ui(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            cwd=tempfile.gettempdir(),
            on_line=lambda c: None,
            before_retry=lambda: calls.append(1),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])


class TestCloneWiresCleanupHook(unittest.TestCase):
    """_clone 调 run_git_ui 时必须把清残留回调接上。"""

    def test_clone_passes_before_retry(self):
        d = Path(tempfile.mkdtemp(prefix="gcm_g85_hook_"))
        svc = GitService(d)
        svc._current_host = "github.com"
        svc._emit = lambda *a, **k: None
        spec = _spec()
        captured: dict = {}

        def fake_run(cmd, cwd, on_line, **kw):
            captured["before_retry"] = kw.get("before_retry")
            return (1, None)  # 一次性失败（retries 默认 2 会被 fake 吞掉节奏，不影响断言）

        with mock.patch("gcm.git.service.run_git_ui", side_effect=fake_run), \
                mock.patch.object(svc, "_head_sha", return_value="abc"), \
                mock.patch.object(svc, "_safe_rmtree_partial", return_value=None):
            svc._clone(spec, SyncResult(spec=spec, status=SyncStatus.RUNNING))
        self.assertIsNotNone(captured.get("before_retry"),
                             "_clone 必须向 run_git_ui 传 before_retry 清残留回调")


class TestTimeoutTailClassifiesAsNetwork(unittest.TestCase):
    """G85-③：超时标记行进 tail 后，分类与降级链判据必须命中网络类。"""

    def test_marker_line_hits_networkish(self):
        tail = ("Filtering content:  43% (7/16), 120.00 MiB | 9.73 MiB/s\n"
                "操作超时（timed out），1s 后自动重试（1/2）…\n"
                "Filtering content:  6% (1/16)")
        self.assertTrue(_is_networkish_error(tail),
                        "含 timed out 标记的 tail 必须被判网络类（降级链可达）")

    def test_timeout_failure_classifies_retryable(self):
        msg, detail = _classify_failure(
            "Filtering content:  43% (7/16)\n"
            "操作超时（timed out），1s 后自动重试（2/2）…")
        self.assertIn("网络", msg)
        info = __import__("gcm.app.errors", fromlist=["classify_error"]) \
            .classify_error(msg, detail)
        self.assertTrue(info.retryable,
                        "超时失败必须可自动重试（此前被判 other 永不重投）")


class TestDirExistsDetailIsHonest(unittest.TestCase):
    """detail 不得声称「已自动清理后重新尝试」——失败路径上什么都没发生。"""

    def test_detail_no_false_claim(self):
        msg, detail = _classify_failure(
            "fatal: destination path 'D:\\\\x' already exists "
            "and is not an empty directory.")
        self.assertEqual(msg, "目标目录已存在（重复提交或残留）")
        self.assertNotIn("已重新尝试", detail)
        self.assertNotIn("已自动", detail,
                         "detail 不得描述未发生的自动清理行为")
        self.assertIn("残留", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
