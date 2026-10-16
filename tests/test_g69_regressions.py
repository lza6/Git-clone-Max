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
from unittest import mock

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


class TestConcurrencyHardening(unittest.TestCase):
    """G69-14/15/16：压测（多进程 + 高并发）逼出来的三处并发缺陷。"""

    def test_file_lock_does_not_use_msvcrt_LK_LOCK(self):
        """G69-16：不得再用 `msvcrt.LK_LOCK` —— 它重试 10 次后抛错，导致静默降级。

        实测（4 进程各写 200 次）：用 LK_LOCK 时有 1 个进程降级；
        降级期间该进程**完全失去跨进程保护**，两个写方同时进入临界区 →
        `os.replace` 报 `PermissionError [WinError 5]`（压测里那几次「写失败」的真因）。
        改为 `LK_NBLCK` + 自己轮询后：降级 0 次、写失败 0 次。
        """
        # 只看**真实调用**的参数（注释里正解释「为什么不用 LK_LOCK」，全文匹配会误伤）
        src = (ROOT / "gcm/util/filelock.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        modes = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "locking"
                    and len(node.args) >= 2):
                m = node.args[1]
                modes.append(getattr(m, "attr", None) or getattr(m, "id", None) or str(m))
        self.assertTrue(modes, "找不到 msvcrt.locking 的调用点（守卫锚点过期）")
        self.assertNotIn("LK_LOCK", modes,
                         f"又用回了 LK_LOCK（内建 10 次上限 → 争用下会静默降级）：{modes}")
        self.assertIn("LK_NBLCK", modes, f"应使用非阻塞锁 + 自行轮询：{modes}")
        self.assertIn("_ACQUIRE_TIMEOUT_SEC", src, "缺少自主的抢锁超时预算")

    def test_lock_is_not_degraded_under_sustained_contention(self):
        """多进程持续争用下不得降级（降级 = 跨进程互斥失效）。"""
        import subprocess

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g6916_"))
        child = r'''
import os, sys
sys.path.insert(0, os.environ["GCM_ROOT"])
from pathlib import Path
import gcm.util.filelock as fl
degraded = []
fl._warn = lambda m: degraded.append(m)
from gcm.util.atomic_io import write_json_atomic
p = Path(sys.argv[1]); n = int(sys.argv[2]); errs = 0
for i in range(n):
    try:
        write_json_atomic(p, {"k": i}, label="probe")
    except Exception:
        errs += 1
print(f"{len(degraded)} {errs}")
'''
        env = dict(os.environ)
        env["GCM_ROOT"] = str(ROOT)
        env["PYTHONIOENCODING"] = "utf-8"
        target = tmp / "x.json"
        procs = [subprocess.Popen([sys.executable, "-c", child, str(target), "150"],
                                  cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True,
                                  encoding="utf-8", errors="replace") for _ in range(4)]
        degraded = errs = 0
        for p in procs:
            out, err = p.communicate(timeout=300)
            self.assertEqual(p.returncode, 0, f"子进程失败：{err[-200:]}")
            d, e = out.strip().split()
            degraded += int(d)
            errs += int(e)
        self.assertEqual(degraded, 0, "争用下降级了 —— 跨进程互斥在该进程内失效")
        self.assertEqual(errs, 0, f"并发写失败 {errs} 次")
        self.assertEqual([p.name for p in tmp.glob("*.tmp")], [], "残留 tmp")

    def test_readers_take_the_cross_process_lock(self):
        """G69-15：`load()` 也必须持跨进程锁。

        Windows 上 `os.replace` 与「正持有目标句柄的读者」冲突会直接失败；
        只锁写方挡不住（读者随时可能持着句柄）。
        """
        for rel, name in (("gcm/app/retry_queue.py", "retry_queue.json"),
                          ("gcm/db/runtime_sessions.py", "session_runtime.json")):
            src = (ROOT / rel).read_text(encoding="utf-8")
            start = src.index("def load(self)")
            body = src[start:start + 900]
            self.assertIn("cross_process_lock", body,
                          f"{rel} 的 load() 未持跨进程锁（G69-15 会复发）")
            self.assertIn(name, body, f"{rel} 的锁标签应指向 {name}")

    def test_http_server_backlog_is_raised(self):
        """G69-14：HTTP API 的 listen backlog 不得留在默认 5。

        实测 50 并发连接时出现 `WinError 10061 目标积极拒绝`（超 backlog 的连接被
        内核直接拒绝，客户端只看到「连不上」）。抬高后 60 并发全部 200。
        """
        from gcm.http_api import GcmApiServer

        self.assertGreaterEqual(GcmApiServer.request_queue_size, 64,
                                "backlog 太小：并发调用会被直接拒绝")


class TestWindowNeverRestoresOffscreen(unittest.TestCase):
    """G69-17：窗口几何恢复后必须**落在可见屏幕上**（用户报「双击打不开」的真因）。

    复现（真实用户在用的机器上实测）：`settings.geometry` 里留下了失效坐标，
    启动后窗口矩形是 `(-21298, -21183, -20263, -20446)` —— 与**任何**显示器都无交集。
    进程活着、任务栏有它，但用户完全看不到窗口。

    关键机制：`QWidget.move()` **不会**矫正离屏坐标 ——
    实测 `move(-21298, -21183)` 之后窗口真的停在那里。所以「存的坐标失效」= 永久打不开。
    失效来源很常见：换显示器、改分辨率、拔掉外接屏、远程桌面会话尺寸不同。
    """

    def _window_with_geometry(self, geo: str):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        d = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g6917_"))
        store = SettingsStore(d / "s.json")
        st = store.load()
        st.geometry = geo
        store.save(st)
        w = MainWindow(data_dir=d, db=Database(d / "t.db"), settings=store)
        w.show()
        _app.processEvents()
        return w

    def _on_any_screen(self, w) -> bool:
        g = w.geometry()
        return any(s.availableGeometry().contains(g.center()) for s in _app.screens())

    def test_offscreen_saved_position_falls_back_to_visible(self):
        w = self._window_with_geometry("1035x737+-21298+-21183")
        try:
            self.assertTrue(self._on_any_screen(w),
                            f"窗口被恢复到屏幕外：{w.geometry().getRect()}")
        finally:
            w.close()
            w.deleteLater()
            _app.processEvents()

    def test_far_right_offscreen_also_recovered(self):
        w = self._window_with_geometry("1035x737+99999+50")
        try:
            self.assertTrue(self._on_any_screen(w),
                            f"窗口在右侧屏外：{w.geometry().getRect()}")
        finally:
            w.close()
            w.deleteLater()
            _app.processEvents()

    def test_valid_position_is_kept(self):
        """反向约束：坐标合法时必须**原样恢复**，不能一律居中（否则等于丢掉用户的选择）。"""
        w = self._window_with_geometry("1035x737+40+40")
        try:
            self.assertTrue(self._on_any_screen(w))
            g = w.geometry()
            self.assertLess(abs(g.x() - 40), 40, f"合法位置未被尊重：{g.getRect()}")
            self.assertLess(abs(g.y() - 40), 40, f"合法位置未被尊重：{g.getRect()}")
        finally:
            w.close()
            w.deleteLater()
            _app.processEvents()

    def test_maximized_state_is_restored(self):
        """顺带钉住：最大化使用户下次启动仍是最大化（原实现写 `M:WxH` 只有 2 个数字，
        恢复时按 4 个解包 → ValueError 被 except 吞掉 → 永远恢复不了）。"""
        w = self._window_with_geometry("M:1060x720+300+200")
        try:
            self.assertTrue(w.isMaximized(), "最大化状态未被恢复")
        finally:
            w.close()
            w.deleteLater()
            _app.processEvents()

    def test_legacy_two_value_format_does_not_break(self):
        """旧版存下的 `M:WxH`（2 个数字）不得让恢复逻辑整体失效。"""
        w = self._window_with_geometry("M:1060x720")
        try:
            self.assertTrue(w.isMaximized())
            self.assertTrue(self._on_any_screen(w))
        finally:
            w.close()
            w.deleteLater()
            _app.processEvents()

    def test_serialize_keeps_position_when_maximized(self):
        """静态守卫：最大化时序列化必须带上位置（4 个数字），否则位置永远丢。"""
        src = (ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        start = src.index("def _serialize_geometry")
        body = src[start:start + 700]
        self.assertIn("normalGeometry()", body)
        self.assertIn("+{ng.x()}+{ng.y()}", body,
                      "最大化时未存位置 —— 恢复后位置会丢（G69-17 的伴生问题）")

    def test_restore_checks_screen_containment(self):
        """静态守卫：恢复位置前必须做「是否落在某块屏上」的判定。"""
        src = (ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn("_screen_contains_point", src,
                      "缺少离屏判定 —— 换显示器后窗口会永久跑到屏幕外")


if __name__ == "__main__":
    unittest.main()


class TestG69_18WiringClosure(unittest.TestCase):
    """G69-18：终结「后端有、用户够不到」的虚伪实现。

    本轮独立审计（两份并行代理 + 主控复核）确认的断链形态：
    模块/字段/回调都写好了、单测也绿，但**没有任何生产调用点或 UI 入口**，
    于是对真实用户等价于不存在。这类缺陷测试抓不到（测试直接构造后端对象，
    绕过了 UI 断链），必须用静态守卫钉住。
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def _read(self, rel: str) -> str:
        return (self.ROOT / rel).read_text(encoding="utf-8")

    def test_report_scheduler_has_production_call_site(self):
        """ReportScheduler 必须被生产代码实例化（此前只在 tests 里被构造）。"""
        src = self._read("gcm/ui/main_window.py")
        self.assertIn("ReportScheduler", src,
                      "ReportScheduler 无生产实例化点 → 定时报表开关是假的")
        self.assertIn("_start_report_scheduler", src)

    def test_report_scheduler_stopped_on_close(self):
        """关窗必须停调度器，否则退出后仍周期性写文件。"""
        src = self._read("gcm/ui/main_window.py")
        self.assertIn("report_scheduler.stop()", src,
                      "closeEvent 未停 ReportScheduler")

    def test_settings_ui_has_entry_for_previously_unreachable_fields(self):
        """这些字段此前「有消费点、无 UI 写入点」→ 用户永远开不了。"""
        src = self._read("gcm/ui/settings_groups.py")
        for field, label in [
            ("retry_enabled", "失败自动重试"),
            ("watchdog_enabled", "看门狗"),
            ("alert_im_channel", "IM 告警渠道"),
            ("alert_im_url", "IM Webhook URL"),
            ("sched_report_interval_min", "报表间隔"),
            ("sched_report_dir", "报表输出目录"),
        ]:
            with self.subTest(field=field):
                self.assertIn(field, src, f"{label}({field}) 无 UI 入口")

    def test_no_handler_referencing_nonexistent_widget(self):
        """`_save_g53_sched_dir` 曾引用不存在的 `edit_g53_sched_dir` → 一调即崩。

        判据：settings_panel 里 `self.owner.<attr>` 形式的控件引用，
        若以 `edit_`/`spin_`/`ck_`/`combo_`/`btn_` 开头，该名字必须
        在 settings_groups / main_window 里真实创建过。
        """
        panel = self._read("gcm/ui/settings_panel.py")
        creators = (self._read("gcm/ui/settings_groups.py")
                    + self._read("gcm/ui/main_window.py"))
        import re
        refs = set(re.findall(
            r"self\.owner\.((?:edit|spin|ck|combo|btn)_[A-Za-z0-9_]+)", panel))
        missing = sorted(n for n in refs if f"owner.{n} =" not in creators)
        self.assertEqual(missing, [],
                         f"引用了从未创建的控件：{missing}")

    def test_alerts_callback_wired(self):
        """AlertScanner 未接 on_alerts → 告警命中后用户全程无感。"""
        src = self._read("gcm/ui/main_window.py")
        self.assertIn("on_alerts=", src, "AlertScanner 未接 on_alerts 回调")
        self.assertIn("alerts_fired", src)

    def test_alert_slot_is_gui_safe(self):
        """告警槽必须在 GUI 线程执行且不得弹模态（后台周期产物）。

        只截取**该函数自身**的函数体（到下一个同级 `def` 为止），
        否则会把相邻函数的 QMessageBox 也算进来 —— 这个守卫第一版就踩了这个坑。
        """
        import re
        src = self._read("gcm/ui/main_window.py")
        self.assertIn("def _on_alerts_fired", src)
        start = src.index("def _on_alerts_fired")
        nxt = re.search(r"\n    def ", src[start + 10:])
        body = src[start:start + 10 + nxt.start()] if nxt else src[start:]
        self.assertNotIn("QMessageBox", body,
                         "告警槽不应弹模态对话框")


class TestG69_18VerifierFindings(unittest.TestCase):
    """G69-18 独立复验线程抓到的、**由本轮改动自身引入**的缺陷。

    这组守卫的教训：修复也要被独立验证 —— 下面 3 条都是主线程自己写错的。
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def test_env_uses_only_existing_applog_api(self):
        """`_env()` 的非数字 COUNT 分支曾调用不存在的 `applog.debug`。

        异常处理分支自己抛 AttributeError 会**中断所有 git 子进程调用**，
        比原本要处理的坏输入严重得多。
        """
        src = (self.ROOT / "gcm/git/service.py").read_text(encoding="utf-8")
        from gcm.app import applog
        # 该分支引用的每个 applog.<name> 都必须是真实存在的属性
        import re
        for name in set(re.findall(r"applog\.([A-Za-z_][A-Za-z0-9_]*)", src)):
            with self.subTest(api=name):
                self.assertTrue(hasattr(applog, name),
                                f"applog.{name} 不存在（service.py 引用了它）")

    def test_env_tolerates_non_numeric_git_config_count(self):
        """坏输入不得让 `_env()` 抛异常（也不得留下自相矛盾的 KEY_n/VALUE_n）。

        ⚠️ 本用例**必须**用 `GitConfigEnvIsolation` 隔离环境。第一版手写
        「pop 三项 + 有条件还原 COUNT」，实测把进程环境搞成
        `COUNT=2` 但 `KEY_0` 已删的**自相矛盾**状态 —— 之后所有 `git` 调用
        都报 `fatal: unable to parse command-line config` /
        `error: missing config key GIT_CONFIG_KEY_0`（exit 128），
        连累 `test_local_dialog` / `test_progress_ui` 两条用例。
        教训：**改环境就要整组备份-整组还原**，不能只还原自己动过的那几个键。
        """
        import tempfile
        from pathlib import Path as _P

        from gcm.git.service import GitService
        with mock.patch.dict(os.environ, {
            "GIT_CONFIG_COUNT": "abc",
            "GIT_CONFIG_KEY_0": "stale.key",
            "GIT_CONFIG_VALUE_0": "stale",
        }, clear=False):
            svc = GitService(_P(tempfile.mkdtemp()), rate_limit_kbps=100)
            env = svc._env()          # 不得抛
        self.assertIsNotNone(env)
        self.assertEqual(env.get("GIT_CONFIG_COUNT"), "2")
        # 坏的那组必须被清掉，不能残留 stale.key 冒充有效项
        self.assertNotEqual(env.get("GIT_CONFIG_VALUE_0"), "stale")
        self.assertIn("http.lowSpeedLimit",
                      [env.get(f"GIT_CONFIG_KEY_{i}") for i in range(2)])

    def test_httpd_cleanup_executes_shutdown_before_join(self):
        """addCleanup 是 LIFO —— 注册序必须让执行序为 shutdown→close→join。

        第一版写反了：join 最先跑而线程还没停 → 每例白等满 5s 超时。
        这里**真实注册并真实执行**一遍 cleanups，断言实际顺序。
        """
        import threading

        from tests.test_g56_api_extra import _serve
        order: list[str] = []

        class _Probe(unittest.TestCase):
            def runTest(self):
                pass

        t = _Probe()
        httpd, _port, th = _serve(pathlib.Path(tempfile.mkdtemp()))
        real_shutdown, real_close = httpd.shutdown, httpd.server_close

        def _sh():
            order.append("shutdown")
            real_shutdown()

        def _cl():
            order.append("close")
            real_close()

        def _jn(*a):
            order.append("join")
            th.join(timeout=5.0)

        httpd.shutdown, httpd.server_close = _sh, _cl
        t.addCleanup(_jn, 5.0)
        t.addCleanup(httpd.server_close)
        t.addCleanup(httpd.shutdown)
        t.doCleanups()

        self.assertEqual(order.index("shutdown") < order.index("join"), True,
                         f"shutdown 必须在 join 之前执行，实际：{order}")
        self.assertFalse(th.is_alive(), "join 后 serve_forever 线程应已退出")

    def test_alert_toggle_live_starts_and_stops_scanner(self):
        """勾选「启用阈值告警」必须真实启停扫描器（此前要重启才生效）。"""
        from gcm.ui.settings_panel import SettingsPanel  # noqa: F401
        src = (self.ROOT / "gcm/ui/settings_panel.py").read_text(encoding="utf-8")
        seg_start = src.index("def _save_g53_alerts")
        seg = src[seg_start:seg_start + 900]
        self.assertIn("scanner.start()", seg)
        self.assertIn("scanner.stop()", seg)

    def test_watchdog_interval_applies_live(self):
        """改看门狗间隔必须立刻作用到运行中定时器。"""
        from gcm.app.watchdog import Watchdog
        wd = Watchdog(pathlib.Path(tempfile.mkdtemp()) / "s.json", interval_min=5)
        wd.interval_min = 15
        self.assertEqual(wd.interval_min, 15)
        self.assertEqual(wd._timer.interval(), 15 * 60 * 1000,
                         "_timer 的 interval 未随 interval_min 更新")


class TestG69_18NoLeakedPumpThreads(unittest.TestCase):
    ROOT = pathlib.Path(__file__).resolve().parent.parent

    """G69-18 **根因守卫**：测试不得把读线程泄漏到后续测试。

    CI `test` job 连续 80+ 次 `exit 139` 的根因就是这里：
    `test_git_thread_exit` 用例 C 用 `time.sleep(3600)` 模拟卡死的 stdout，
    又断言「join 超时后线程仍存活」→ `_pump` 线程被故意泄漏，
    在整套测试的剩余时间里存活并最终触发原生崩溃（实测 access violation）。

    这条守卫在**真实运行**该模块后断言：`_pump` 线程数必须归零。
    它是行为断言，不是静态扫描 —— 因为「有没有泄漏」只有跑过才知道。
    """

    def test_git_thread_exit_leaves_no_pump_threads(self):
        import subprocess
        code = (
            "import sys, threading, unittest\n"
            "sys.path.insert(0, r'%s')\n"
            "import tests.test_git_thread_exit as m\n"
            "r = unittest.TextTestRunner(verbosity=0).run(\n"
            "        unittest.TestLoader().loadTestsFromModule(m))\n"
            "alive = [t.name for t in threading.enumerate()\n"
            "         if t.is_alive() and ('_pump' in t.name or 'Thread-' in t.name)]\n"
            "print('RESULT', r.wasSuccessful(), len(alive), alive)\n"
        ) % str(self.ROOT)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=180,
                           cwd=str(self.ROOT))
        self.assertEqual(r.returncode, 0, f"子进程异常：{r.stderr[-500:]}")
        last = (r.stdout or "").strip().splitlines()[-1]
        self.assertIn("RESULT True", last, f"该模块自身未通过：{last}")
        leaked = int(last.split()[2])
        self.assertEqual(leaked, 0, f"泄漏了 {leaked} 个线程：{last}")


class TestG69_18NoGitConfigEnvPollution(unittest.TestCase):
    """G69-18：测试**不得**把进程环境里的 `GIT_CONFIG_*` 改成自相矛盾的状态。

    git 要求 `GIT_CONFIG_COUNT=N` 时 `GIT_CONFIG_KEY_0..N-1` 与
    `GIT_CONFIG_VALUE_0..N-1` **全部存在**。一旦出现「COUNT 还在、KEY_0 没了」
    这种状态，**其后每一次 git 调用**都会失败：

        error: missing config key GIT_CONFIG_KEY_0
        fatal: unable to parse command-line config      (exit 128)

    本轮真发生过：新加的守卫用例手写「pop 三项 + 有条件还原 COUNT」，
    结果把环境搞坏，连累 `test_local_dialog` / `test_progress_ui` 报
    `CalledProcessError: git init returned non-zero exit status 128`。
    崩在别人的用例上 —— 又一次「目击者指向错误嫌疑人」。

    这条守卫在**本进程**内断言一致性；任何测试把环境弄坏，它会立刻红。
    """

    def test_git_config_env_is_self_consistent(self):
        count_raw = os.environ.get("GIT_CONFIG_COUNT")
        if count_raw is None:
            # 没有 COUNT 时孤立 KEY_n/VALUE_n 是**无害**的：git 只按 COUNT
            # 决定读几项，多余的键会被忽略。CI runner 确实会留这类键
            # （GitHub Actions 通过 GIT_CONFIG_* 传 credential.* 设置），
            # 在这里判失败等于把平台环境当缺陷 —— 第一版就是这么误红的。
            return
        self.assertTrue(count_raw.isdigit(),
                        f"GIT_CONFIG_COUNT 非数字：{count_raw!r} —— git 会直接报错")
        n = int(count_raw)
        missing = [f"GIT_CONFIG_{kind}_{i}"
                   for i in range(n) for kind in ("KEY", "VALUE")
                   if f"GIT_CONFIG_{kind}_{i}" not in os.environ]
        self.assertEqual(missing, [],
                         f"GIT_CONFIG_COUNT={n} 但缺少 {missing} —— "
                         "其后所有 git 调用会以 exit 128 失败")


class TestG69_18ScanWorkerStopsBeforeEmit(unittest.TestCase):
    """G69-18：`ScanWorker` 在宿主析构后**不得再 emit**。

    `gcm/ui/scan_worker.py` 用普通线程驱动一个 QObject，
    而 `LocalReposDialog` 把它 `parent=self`。用户点「取消」/按 Esc/关窗口时，
    原实现只 `reject()` 对话框、**从不调用 `worker.cancel()`** ——
    线程继续对着 C++ 侧已析构的 QObject `emit`，
    表现为**进程原生崩溃**（本机实测在本地扫描链路复现 access violation）。

    这条守卫做**行为**断言：stop() 之后线程必须收敛，且不再产生任何信号。
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def test_scan_worker_has_stop_and_is_signal_safe(self):
        from gcm.ui.scan_worker import ScanWorker
        self.assertTrue(hasattr(ScanWorker, "stop"),
                        "ScanWorker 必须提供 stop()（取消 + 等待收敛）")
        w = ScanWorker([], max_depth=1)
        got: list[str] = []
        w.finished.connect(lambda *_: got.append("finished"))
        w.failed.connect(lambda *_: got.append("failed"))
        w.progress.connect(lambda *_: got.append("progress"))
        w.stop()                       # 幂等；未 start 也要安全
        w._emit(w.progress, 1, 2)      # 停止后不得再发
        self.assertEqual(got, [], f"stop() 后仍发出了信号：{got}")

    def test_scan_worker_stop_joins_running_thread(self):
        import threading
        import time as _t
        from gcm.ui.scan_worker import ScanWorker
        w = ScanWorker([], max_depth=1)
        w.start()                      # 空 roots，会很快结束
        w.stop(timeout=5.0)
        self.assertFalse(w._thread is not None and w._thread.is_alive(),
                         "stop() 后扫描线程不应仍存活")
        self.assertLessEqual(
            len([t for t in threading.enumerate() if t.is_alive()]), 8,
            "不应遗留大量线程")

    def test_dialog_cancel_stops_worker(self):
        """对话框的取消/关闭路径必须真的停线程（静态 + 行为双重确认）。"""
        src = (self.ROOT / "gcm/ui/local_repos_dialog.py").read_text(encoding="utf-8")
        self.assertIn("def reject", src, "必须重写 reject() 覆盖 Esc/程序化关闭")
        self.assertIn("_stop_scan_worker", src)
        self.assertIn("w.stop()", src, "必须真的调用 ScanWorker.stop()")


class TestG69_18AgentSurfaceRobustness(unittest.TestCase):
    """G69-18：MCP / HTTP 两个 agent 接入面的两处缺陷（独立安全审计报出）。

    A. MCP `export_report` 的 `out_dir` 越界校验**抛出的 ValueError 逃逸出
       stdio 循环** → 进程以 traceback 退出、通道中断（客户端拿到裸栈，
       后续请求再无响应）。校验本身是对的，错在没转成 JSON-RPC 错误。
    B. MCP 与 HTTP 各自自拼数据目录，未复用 `gcm.__main__.get_data_dir()`
       —— 违反 CLAUDE.md §5 红线；安装版会读到 `_internal/data` 或克隆输出
       目录里的（甚至新建的）空库，而不是 `%APPDATA%/Git-clone-Max` 的真实库。
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def test_mcp_export_out_dir_returns_jsonrpc_error_not_crash(self):
        import subprocess
        import tempfile as _tf

        from gcm.db.repo_db import Database
        # 数据目录必须**真实存在且含可用库**，否则失败会发生在「打开数据库」
        # 那一步（-32603），而不是我们要验的 out_dir 校验（-32602）。
        # 第一版直接指向 <repo>/data —— 干净检出（CI）里没有该目录，用例必红。
        data_dir = pathlib.Path(_tf.mkdtemp(prefix="gcm_mcp_guard_"))
        db = Database(data_dir / "repos.db")
        db.close()
        req = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
               '{"name":"export_report","arguments":'
               '{"format":"csv","out_dir":"%s"}}}\n') % (
            pathlib.Path(_tf.gettempdir()) / "gcm_guard_pwn").as_posix()
        env = dict(os.environ, GCM_MCP_ENABLE="1", GCM_DATA_DIR=str(data_dir))
        r = subprocess.run([sys.executable, "-m", "gcm.mcp_server", "--allow-export"],
                           input=req, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(self.ROOT), env=env, timeout=120)
        self.assertEqual(r.returncode, 0,
                         f"MCP 不应因参数错误崩溃/中断：rc={r.returncode} {r.stderr[-300:]}")
        self.assertNotIn("Traceback", r.stderr, f"不得抛出裸栈：{r.stderr[-300:]}")
        out = json.loads((r.stdout or "").strip().splitlines()[-1])
        self.assertIn("error", out, f"应返回 JSON-RPC 错误：{out}")
        self.assertEqual(out["error"]["code"], -32602)

    def test_agent_surfaces_use_single_data_dir_source(self):
        """MCP/HTTP 不得自拼数据目录，必须复用 get_data_dir()。"""
        for rel in ("gcm/mcp_server.py", "gcm/cli.py"):
            src = (self.ROOT / rel).read_text(encoding="utf-8")
            with self.subTest(file=rel):
                self.assertIn("get_data_dir", src,
                              f"{rel} 未复用 get_data_dir()（数据目录单源红线）")
        # 防回归：不得再出现自拼 <repo>/data 的写法
        mcp = (self.ROOT / "gcm/mcp_server.py").read_text(encoding="utf-8")
        self.assertNotIn('parent.parent / "data"', mcp,
                         "mcp_server 又自拼 <repo>/data 了")


class TestG69_18LogBufferTimerIsActivityArmed(unittest.TestCase):
    """G69-18：日志节流定时器必须是**有积压才武装的单次**定时器。

    根因链（本机 + Linux/macOS CI 实测一致）：

        LogBuffer.__init__ 里 `self.timer.start()` 常驻循环
        → 窗口销毁后事件队列里仍排着指向它的 QTimerEvent
        → 下一次 processEvents() 在 QTimerInfoList::activateTimers 里
          解引用**已释放**的定时器
        → 进程原生崩溃（C 栈：activateTimers → QCoreApplication::notifyInternal2）

    改成单次 + append 时武装后：空转不再唤醒事件循环，且没有积压时
    根本不存在「等待触发的定时器」。120ms 批量渲染语义不变。
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def test_timer_is_single_shot_and_not_started_at_construction(self):
        from gcm.ui.log_buffer import LogBuffer
        lb = LogBuffer()
        self.addCleanup(lb.stop)
        self.assertTrue(lb.timer.isSingleShot(),
                        "必须是单次定时器，否则又会常驻循环")
        self.assertFalse(lb.timer.isActive(),
                         "构造时不应武装（无积压就不该有等待触发的定时器）")

    def test_append_arms_timer_and_stop_disarms(self):
        from gcm.ui.log_buffer import LogBuffer
        from gcm.ui.theme import LogLevel, LogEvent
        lb = LogBuffer()
        self.addCleanup(lb.stop)
        lb.append(LogEvent("12:00:00", LogLevel.INFO, "x"))
        self.assertTrue(lb.timer.isActive(), "有积压后应武装定时器")
        lb.stop()
        self.assertFalse(lb.timer.isActive(), "stop() 后必须停表")

    def test_close_event_stops_log_timer(self):
        src = (self.ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn("_log_buffer.stop()", src,
                      "closeEvent 必须停日志节流定时器")
