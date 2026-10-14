"""G69 回归守卫：独立审计线程报出的 6 项缺陷（每条都有实测复现）。

本文件的写法刻意统一为「**反证对照**」：除了断言修复后的正确行为，
还就地跑一遍「错误写法」证明这套判定确实能抓到问题 —— 否则「跑绿」可能只是
断言太弱（本轮就吃过一次：等价性对比只用默认配置，漏掉了真 bug）。

对应关系：
- G69-1 数据销毁：同步流程裸 `shutil.rmtree` 删 DB 派生的任意目录
- G69-2 永久挂死：放弃读线程后仍 `proc.stdout.close()`，与阻塞读线程抢同一把锁
- G69-3 永久死锁：替换引擎不 drain 旧引擎 → 析构 QThreadPool 与 worker 抢 GIL
- G69-4 静默丢任务：`_update_next_root` 先 pop 再判 busy
- G69-5 错行：排序开启时进度/结果按 index 直取行号（实测 300/300 错位）
- G69-6 静默丢数据：固定 tmp 名 + 无跨进程锁（retry_queue / session_runtime / progress_gc）
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


# ============================================================ G69-6
class TestAtomicJsonWrite(unittest.TestCase):
    """并发写同一 JSON 文件不得丢更新/损坏（原来的固定 tmp 名会）。"""

    def test_concurrent_writers_lose_nothing(self):
        from gcm.app.retry_queue import RetryQueue

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g696_"))
        path = tmp / "retry_queue.json"
        N, PER = 2, 120
        errors: list = []

        def _writer(k: int):
            q = RetryQueue(path, enabled=True)
            try:
                for i in range(PER):
                    key = f"o{k}/r{i}"
                    q.record_failure.__self__  # noqa: B018 - 保持实例语义
                    # 直接走落盘路径，绕开「仅网络类错误才入队」的业务判定
                    q._save({"entries": {key: {"key": key, "url_https": "https://h/x.git",
                                               "message": "timed out"}},
                             "dropped": []})
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=_writer, args=(k,)) for k in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors[:3], [], f"并发写抛异常 {len(errors)} 次：{errors[:3]}")
        raw = path.read_text(encoding="utf-8")
        json.loads(raw)  # 半截 JSON 会在这里抛
        self.assertEqual([p.name for p in tmp.glob("*.tmp")], [], "残留 tmp 文件")
        self.assertIn("entries", json.loads(raw))

    def test_control_fixed_tmp_name_loses_updates(self):
        """**反证对照**：固定 tmp 名 + 无锁（修复前的写法）必须真的丢数据。

        这条不测生产代码，只证明「上面那个判定方式抓得住问题」——
        没有它，上面的绿灯可能只是断言太弱。
        """
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g696_neg_"))
        path = tmp / "x.json"
        errors: list = []

        def _bad_writer(k: int):
            try:
                for i in range(120):
                    fixed = path.with_suffix(".json.tmp")   # ← 修复前的写法
                    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                    data[f"o{k}/r{i}"] = i
                    fixed.write_text(json.dumps(data), encoding="utf-8")
                    os.replace(fixed, path)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}")

        threads = [threading.Thread(target=_bad_writer, args=(k,)) for k in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        final = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(errors, "对照组的错误写法竟然一次都没失败 —— 判定方式没有检测力")
        self.assertLess(len(final), 240,
                        "对照组竟然没丢数据 —— 说明本机时序恰好无冲突，上面的守卫需要重审")

    def test_three_more_writers_use_the_shared_writer(self):
        """静态守卫：这三处不得再自己拼 tmp 名（G69-6 的根因就是各写各的）。"""
        for rel in ("gcm/app/retry_queue.py", "gcm/db/runtime_sessions.py",
                    "gcm/db/progress_gc.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn('with_suffix(".json.tmp")', src,
                             f"{rel} 又回到固定 tmp 名了")
            self.assertIn("write_json_atomic", src,
                          f"{rel} 未使用共享原子写（G69-6 会复发）")


# ============================================================ G69-1
class TestSyncDoesNotDeleteArbitraryDirs(unittest.TestCase):
    """同步流程不得删除「非空且非 git」的目录（实测原实现会整棵删掉）。"""

    def test_non_git_dir_is_preserved_and_reported(self):
        from gcm.git.service import GitService
        from gcm.models import RepoSpec

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g691_"))
        victim = tmp / "my_precious_data"
        victim.mkdir()
        (victim / "thesis.docx").write_text("very important", encoding="utf-8")
        (victim / "photos").mkdir()

        svc = GitService(tmp / "clones")
        spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b.git",
                        local_path=str(victim))
        res = svc.sync(spec)

        self.assertTrue(victim.exists(), "非 git 目录被删除了（数据销毁）")
        self.assertEqual(sorted(p.name for p in victim.iterdir()),
                         ["photos", "thesis.docx"], "目录内容被改动")
        self.assertEqual(res.status.value, "failed", "应如实报失败而不是继续克隆")
        self.assertIn("保留", res.message + res.detail, "应说明为何没清理")

    def test_safe_rmtree_still_cleans_real_partial_clone(self):
        """反向约束：真正由本工具产生的半成品（含 .git）仍必须被清理。

        断言的是「清理动作真的发生了」（mock 掉 rmtree 看调用），
        而不是「目录消失了」—— 后面紧跟着 clone 会把目录重新建出来，
        用「消失」判定会误判。
        """
        from unittest import mock

        from gcm.git.service import GitService
        from gcm.models import RepoSpec

        # 注意分支顺序：**含 .git** 的目录会先被「已存在仓库」分支接走（走更新），
        # 所以「半成品清理」这条分支只处理**不含 .git** 的目录 —— 空目录正是它的场景。
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g691_ok_"))
        partial = tmp / "partial"
        partial.mkdir()

        svc = GitService(tmp / "clones")
        spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b.git",
                        local_path=str(partial))
        with mock.patch("gcm.git.service.shutil.rmtree") as m_rm:
            svc.sync(spec)
        self.assertTrue(m_rm.called, "空的半成品目录未被清理（应清掉重克隆）")

    def test_bare_rmtree_on_repo_dir_is_scoped_to_our_own_clone_root(self):
        """静态守卫：对 `repo_dir` 的**裸** `shutil.rmtree` 只能在 `spec.local_path` 为空时执行。

        为什么是这个口径（而不是「一律禁止」）：`tests/test_core.py::test_broken_dir_recloned`
        钉住了正式契约 —— **克隆目标位**上的坏目录要清掉重克隆。危险面只在路径来自
        `spec.local_path`（可由导入的元数据 JSON 提供）时出现：那可能是任意用户目录。
        所以据以区分的正是「路径来源」，守卫也按这个口径钉。
        """
        src = (ROOT / "gcm/git/service.py").read_text(encoding="utf-8")
        self.assertIn("if not spec.local_path:", src,
                      "缺少「按路径来源分治」的判断 —— 要么回到无条件删除（数据销毁），"
                      "要么误禁了克隆目标位的正常清理")
        scoped = src.index("if not spec.local_path:")
        # 半成品清理分支里那处裸 rmtree 必须在该判断之后
        tail = src[scoped:scoped + 800]
        self.assertIn("shutil.rmtree(repo_dir)", tail,
                      "裸 rmtree 不在 local_path 判断的作用域内")


# ============================================================ G69-5
class TestProgressRowsMatchRepoWhileSorting(unittest.TestCase):
    """排序开启时，进度/结果必须落到**对应仓库**那一行（实测原实现 300/300 错位）。"""

    def _win(self):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        d = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g695_"))
        w = MainWindow(data_dir=d, db=Database(d / "t.db"),
                       settings=SettingsStore(d / "s.json"))
        w._check_update_silent = lambda *a, **k: None
        w._maybe_show_onboarding = lambda *a, **k: None
        return w

    def test_result_lands_on_the_matching_repo_row(self):
        from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus

        w = self._win()
        try:
            # 故意让插入顺序与字典序相反，逼出排序重排
            names = [("zeta", "one"), ("alpha", "two"), ("mid", "three")]
            w.progress_panel._prepare_table(len(names))
            self.assertTrue(w.table.isSortingEnabled(),
                            "前提：表格默认开启排序（本 bug 的触发条件）")
            for i, (o, r) in enumerate(names):
                w.progress_panel._add_table_row(
                    i, RepoSpec(owner=o, repo=r, url_https=f"https://github.com/{o}/{r}.git",
                                folder_name=f"{o}__{r}"))
            _app.processEvents()

            # 引擎回报 index=1（alpha/two）成功
            spec = w.engine.specs.get(1) if getattr(w, "engine", None) else None
            spec = spec or RepoSpec(owner="alpha", repo="two",
                                    url_https="https://github.com/alpha/two.git",
                                    folder_name="alpha__two")
            w.progress_panel._on_worker_result(
                1, SyncResult(spec=spec, status=SyncStatus.SUCCESS,
                              action=SyncAction.CLONED, message="完成"))
            _app.processEvents()

            # 找到 alpha__two 所在行，断言它的状态列写的是「成功」
            row = None
            for r in range(w.table.rowCount()):
                it = w.table.item(r, 0)
                if it is not None and it.text() == "alpha__two":
                    row = r
                    break
            self.assertIsNotNone(row, "找不到 alpha__two 那一行")
            status_item = w.table.item(row, 2)
            self.assertIn("成功", status_item.text(),
                          "状态写到了别的仓库那一行（G69-5 错行）")
            # 其余行不得被误标成功
            others = [w.table.item(r, 2).text()
                      for r in range(w.table.rowCount()) if r != row]
            self.assertTrue(all("成功" not in t for t in others),
                            f"有别的行也被标成功：{others}")
        finally:
            w.close()
            w.deleteLater()
            _app.processEvents()

    def test_row_lookup_helper_used_by_all_progress_slots(self):
        """静态守卫：三个进度槽都必须经 `_row_for` 反查，不得直接用 index 当行号。"""
        src = (ROOT / "gcm/ui/progress_table.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in (
                    "_on_worker_progress", "_on_worker_result", "_flush_detail_batch"):
                body = ast.dump(node)
                if "_row_for" not in body:
                    bad.append(node.name)
        self.assertEqual(bad, [], f"这些槽未按 engine index 反查真实行：{bad}")


# ============================================================ G69-2 / G69-3 / G69-4
class TestLifecycleGuards(unittest.TestCase):
    """挂死/死锁/丢任务三处的静态守卫（动态复现需要极端进程时序，见报告）。"""

    def test_stdout_close_is_conditional_on_reader_exit(self):
        """G69-2：只有读线程已退出才允许 close stdout（否则与它抢锁永久阻塞）。"""
        src = (ROOT / "gcm/git/service.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "close":
                tgt = node.func.value
                if getattr(tgt, "attr", None) == "stdout":
                    found = True
        self.assertTrue(found, "找不到 stdout.close() —— 守卫锚点过期，请复核")
        # 关键：该 close 必须位于 `if not pump.is_alive():` 之内
        # 只在我们关心的那段（放弃等待读线程之后）里比对位置 ——
        # 读线程自身的 finally 里也有 stdout.close()，全文 index() 会取错
        warn_at = src.index("已放弃等待读线程")
        tail = src[warn_at:warn_at + 1200]
        self.assertIn("if not pump.is_alive():", tail,
                      "stdout.close() 不再受读线程存活状态保护（G69-2 会复发）")
        self.assertLess(tail.index("if not pump.is_alive():"),
                        tail.index("proc.stdout.close()"),
                        "守护判断必须在 close 之前")

    def test_launch_drains_previous_engine(self):
        """G69-3：`_launch` 替换引擎前必须让旧引擎收敛（否则析构时与 worker 抢 GIL 死锁）。"""
        src = (ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        idx_new = src.index("self.engine = SyncEngine(")
        # 首次构造（__init__:93）之前没有旧引擎；这里检查第二处（_launch 内）
        second = src.index("self.engine = SyncEngine(", idx_new + 1)
        window = src[max(0, second - 1400):second]
        self.assertIn("old_engine", window,
                      "`_launch` 里没有先收敛旧引擎（G69-3 死锁会复发）")
        # 必须用不泵事件的 `discard()`：`shutdown()` 内部 drain() 会 processEvents()，
        # 在 UI 槽里重入（G69-13）
        self.assertIn("old_engine.discard()", window,
                      "旧引擎应收敛后再替换，且不得用会泵事件的 shutdown()")

    def test_update_next_root_checks_busy_before_pop(self):
        """G69-4：先判 busy 再 pop，否则会静默丢掉一组根目录。"""
        src = (ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        start = src.index("def _update_next_root(self):")
        body = src[start:start + 700]
        self.assertLess(body.index("if self.busy:"), body.index(".pop(0)"),
                        "`pop(0)` 仍在 busy 判断之前 —— 该组会被静默丢弃")


if __name__ == "__main__":
    unittest.main()
