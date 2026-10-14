"""G67 收尾批次的守卫测试（G67-3 / G67-4 / G67-10）。

这些守卫的共同点：**断言的是「接线真的通了」**，而不是「函数被调用过」。
历史教训（G67 轮次）：本轮的多数漏网缺陷都是
「代码写了但没有任何生产调用点」或「守卫只覆盖了我做的那一侧」。
所以每个用例都尽量打到真实行为，并配一条**反向自检**证明守卫不是空壳。
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ============================================================ G67-3
class TestRetryReusesBatchContext(unittest.TestCase):
    """G67-3：自动重投必须重建 host 上下文，否则污染 host 列 / 克隆语义错位。"""

    def _engine(self, tmp: pathlib.Path):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        from gcm.app.engine import SyncEngine

        QApplication.instance() or QApplication([])
        return SyncEngine(tmp)

    def test_launch_records_batch_context_snapshot(self):
        """`launch()` 必须快照 host_by_key / fetch_depth / unshallow / mirror。"""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g673_ctx_"))
        eng = self._engine(tmp / "root")
        started: list = []
        eng._start_worker = lambda *a, **k: started.append(a)  # type: ignore[method-assign]
        from gcm.models import RepoSpec

        spec = RepoSpec(owner="gitea", repo="demo",
                        url_https="https://git.corp.example/gitea/demo.git")
        n = eng.launch([spec], host_by_key={"gitea/demo": "git.corp.example"},
                       fetch_depth=1, unshallow=True, mirror=True,
                       check_existing=False)
        self.assertEqual(n, 1, "应调度 1 个仓库")
        self.assertEqual(len(started), 1)
        ctx = eng._last_batch_ctx
        self.assertEqual(ctx.get("host_by_key"), {"gitea/demo": "git.corp.example"},
                         "host_by_key 未快照 —— 重投会把 host 落成 github.com")
        self.assertEqual(ctx.get("fetch_depth"), 1)
        self.assertIs(ctx.get("unshallow"), True)
        self.assertIs(ctx.get("mirror"), True)

    def test_context_survives_release_tasks(self):
        """`_release_tasks()` 会清 `_host_by_key`，但**不得**清掉快照。

        重投定时器常常在该批结束**之后**才到期 —— 正是需要快照的时刻。
        """
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g673_rel_"))
        eng = self._engine(tmp / "root")
        eng._last_batch_ctx = {"host_by_key": {"gitea/demo": "git.corp.example"},
                               "fetch_depth": 1, "unshallow": False, "mirror": False}
        eng._host_by_key = {"gitea/demo": "git.corp.example"}
        eng._release_tasks()
        self.assertEqual(eng._host_by_key, {}, "_release_tasks 应清空本批 host 映射")
        self.assertEqual(eng._last_batch_ctx.get("host_by_key"),
                         {"gitea/demo": "git.corp.example"},
                         "快照被一起清掉了 —— 重投仍会丢 host")

    def test_retry_tick_passes_context_to_launch(self):
        """**核心断言**：`_retry_tick` 调 `launch` 时必须带上快照里的上下文。"""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g673_tick_"))
        eng = self._engine(tmp / "root")
        eng._last_batch_ctx = {"host_by_key": {"gitea/demo": "git.corp.example"},
                               "fetch_depth": 1, "unshallow": True, "mirror": True}

        from gcm.models import RepoSpec

        spec = RepoSpec(owner="gitea", repo="demo",
                        url_https="https://git.corp.example/gitea/demo.git")

        class _Q:
            enabled = True

            @staticmethod
            def due_tasks():
                return [{"key": "gitea/demo"}]

            @staticmethod
            def to_spec(_rec):
                return spec

            @staticmethod
            def mark_success(_k):
                pass

        eng._retry_queue = _Q()
        captured: dict = {}

        def _fake_launch(specs, **kw):
            captured["specs"] = list(specs)
            captured.update(kw)
            return len(captured["specs"])

        eng.launch = _fake_launch  # type: ignore[method-assign]
        eng.busy = False
        eng._retry_tick()

        self.assertEqual([s.repo for s in captured.get("specs", [])], ["demo"])
        self.assertEqual(captured.get("host_by_key"), {"gitea/demo": "git.corp.example"},
                         "重投未传 host_by_key → 非 GitHub 仓库会被落库成 github.com")
        self.assertEqual(captured.get("fetch_depth"), 1, "重投丢了浅克隆语义")
        self.assertIs(captured.get("unshallow"), True)
        self.assertIs(captured.get("mirror"), True)

    def test_retry_tick_without_prior_batch_still_works(self):
        """快照为空时不得崩（首次启动即有待重投队列的边界场景）。"""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g673_empty_"))
        eng = self._engine(tmp / "root")
        eng._last_batch_ctx = {}

        from gcm.models import RepoSpec

        spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b.git")

        class _Q:
            enabled = True

            @staticmethod
            def due_tasks():
                return [{"key": "a/b"}]

            @staticmethod
            def to_spec(_rec):
                return spec

        eng._retry_queue = _Q()
        captured: dict = {}
        eng.launch = lambda specs, **kw: captured.update(kw) or 1  # type: ignore[method-assign]
        eng.busy = False
        eng._retry_tick()  # 不得抛异常
        self.assertEqual(captured.get("host_by_key"), {})
        self.assertEqual(captured.get("fetch_depth"), 0)


class TestProgressTableOutOfRange(unittest.TestCase):
    """G67-3 后半：重投批次 index 超出进度表行数时不得抛 AttributeError。"""

    def test_result_for_index_beyond_rows_pads_instead_of_crashing(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import tempfile

        from PyQt6.QtWidgets import QApplication

        from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus
        from gcm.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g673_pt_"))
        win = None
        try:
            win = MainWindow(tmp)
            pt = win.progress_panel
            pt._prepare_table(1)          # 表里只有 1 行
            rows_before = pt.table.rowCount()

            spec = RepoSpec(owner="x", repo="y", url_https="https://github.com/x/y.git")
            res = SyncResult(spec=spec, status=SyncStatus.SUCCESS,
                             action=SyncAction.CLONED, message="完成")
            # index=3 远超 rows_before → 原实现在 table.item(3,2) 拿到 None 后崩
            pt._on_worker_result(3, res)
            app.processEvents()

            self.assertGreater(pt.table.rowCount(), rows_before,
                               "越界结果应补出行，而不是被丢弃")
            # 排序开启时补出的行会立刻被重排 → 不能假设「行号 == engine index」，
            # 必须按行内藏的 engine index 反查真实行（这正是修复的一部分）
            row = pt._row_for_engine_index(3)
            self.assertIsNotNone(row, "补出的行应能被 engine index 反查到")
            item = pt.table.item(row, 2)
            self.assertIsNotNone(item, "补行后该单元格必须存在")
            self.assertIn("成功", item.text(), "状态列应写入结果状态")
        finally:
            if win is not None:
                win.close()
                win.deleteLater()
                app.processEvents()


# ============================================================ G67-4
class TestExtraHostsUsesRealDataDir(unittest.TestCase):
    """G67-4：`url_lib._extra_hosts` 必须复用 `get_data_dir()`。

    原实现自行拼 `<exe>/data` 或 `<源码>/data`，**漏掉默认的
    `%APPDATA%/Git-clone-Max`** —— 而设置页正是写到那里。干净安装（无 `data/`
    目录）时自建主机白名单**永久失效**。
    """

    def test_extra_hosts_calls_get_data_dir(self):
        """静态守卫：不得再自行拼路径（防止有人把这段逻辑再内联回去）。"""
        src = (ROOT / "gcm/app/url_lib.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_extra_hosts"), None)
        self.assertIsNotNone(fn, "找不到 _extra_hosts")
        names: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                names.add(getattr(node.func, "attr", None) or getattr(node.func, "id", None) or "")
            if isinstance(node, ast.ImportFrom):
                names.update(a.name for a in node.names)
        self.assertIn("get_data_dir", names,
                      "_extra_hosts 未使用 get_data_dir() —— 又会漏掉 %APPDATA% 数据目录")

    def test_whitelist_follows_get_data_dir(self):
        """行为断言：白名单**只**跟随 `get_data_dir()` 的结果。"""
        import gcm.__main__ as main_mod
        import gcm.app.url_lib as ul

        src_dir = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g674_src_"))
        other_dir = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g674_other_"))
        (src_dir / "settings.json").write_text(
            json.dumps({"custom_hosts": ["GitLab.Corp.Example", "gitea.internal"]}),
            encoding="utf-8")
        # 反向自检用的诱饵：另一目录里有**不同**的白名单
        (other_dir / "settings.json").write_text(
            json.dumps({"custom_hosts": ["decoy.example"]}), encoding="utf-8")

        # 前提：仓库里确实存在 <repo>/data/settings.json（旧实现正是读它）
        self.assertTrue((ROOT / "data" / "settings.json").exists(),
                        "前提不成立：仓库无 data/settings.json，旧 bug 无法复现")

        original = main_mod.get_data_dir
        try:
            main_mod.get_data_dir = lambda *a, **k: src_dir
            self.assertEqual(ul._extra_hosts(), ("gitlab.corp.example", "gitea.internal"),
                             "未读取 get_data_dir() 指向的数据目录")
            self.assertTrue(ul.is_supported_host("gitlab.corp.example"))
            self.assertFalse(ul.is_supported_host("decoy.example"),
                             "读到了非 get_data_dir() 的目录（路径逻辑仍未统一）")

            # 换成诱饵目录 → 结果必须跟着变（证明「只认 get_data_dir」）
            main_mod.get_data_dir = lambda *a, **k: other_dir
            self.assertEqual(ul._extra_hosts(), ("decoy.example",))
            self.assertFalse(ul.is_supported_host("gitlab.corp.example"))

            # 目录里没有 settings.json → 空元组，且**绝不**回退到 <repo>/data
            empty = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g674_empty_"))
            main_mod.get_data_dir = lambda *a, **k: empty
            self.assertEqual(ul._extra_hosts(), (),
                             "回退读了 <repo>/data —— 安装版会因此永久失效")
        finally:
            main_mod.get_data_dir = original

    def test_installed_layout_whitelist_actually_works(self):
        """E2E 形状：模拟「安装版」—— 数据目录在别处、仓库无 data/ 可读。

        这是本 bug 的真实触发形态：干净安装时 `<安装目录>/data/settings.json`
        不存在，而设置在 `%APPDATA%`。
        """
        import gcm.__main__ as main_mod
        import gcm.app.url_lib as ul

        appdata = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g674_appdata_"))
        (appdata / "settings.json").write_text(
            json.dumps({"custom_hosts": ["git.company.example"]}), encoding="utf-8")
        original = main_mod.get_data_dir
        try:
            main_mod.get_data_dir = lambda *a, **k: appdata
            from gcm.app.url_lib import parse_any_repo_url
            specs = parse_any_repo_url("https://git.company.example/team/proj.git")
            self.assertTrue(specs, "安装版布局下自建 GitLab 地址应能被解析（原为「没有可用的 GitHub 地址」）")
        finally:
            main_mod.get_data_dir = original


# ============================================================ G67-10
_CHILD_RMW = r"""
import json, os, sys, time
from pathlib import Path

target = Path(sys.argv[1]); ready = Path(sys.argv[2])
n = int(sys.argv[3]); mode = sys.argv[4]

if mode == "lock":
    from gcm.util.filelock import cross_process_lock
    cm = cross_process_lock(target, label="g67-10-test")
else:
    import contextlib
    @contextlib.contextmanager
    def _nop():
        yield
    cm = _nop()

# 启动屏障：两进程都就绪后才继续，消除「进程启动耗时不同」造成的偶然性
(ready / str(os.getpid())).write_text("1", encoding="utf-8")
deadline = time.time() + 30
while len(list(ready.iterdir())) < n:
    if time.time() > deadline:
        raise SystemExit("barrier timeout")
    time.sleep(0.01)

with cm:
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"n": 0}
    time.sleep(0.4)          # 读与写之间留窗口：无锁时两个进程必然读到同一个旧值
    data["n"] = data.get("n", 0) + 1
    target.write_text(json.dumps(data), encoding="utf-8")
"""


class TestCrossProcessLock(unittest.TestCase):
    """G67-10：`cross_process_lock` 必须真的提供**跨进程**互斥。

    这不是「函数被调用了」的断言 —— 而是复现真实故障形态（读-改-写丢失更新），
    用两个真进程跑，并用**无锁对照组**证明这个测试确实能失败。
    """

    def _run_two(self, mode: str) -> int:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"gcm_g6710_{mode}_"))
        target = tmp / "state.json"
        ready = tmp / "ready"
        ready.mkdir()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONIOENCODING"] = "utf-8"
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", _CHILD_RMW, str(target), str(ready), "2", mode],
                cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace")
            for _ in range(2)
        ]
        for p in procs:
            out, err = p.communicate(timeout=90)
            self.assertEqual(p.returncode, 0, f"子进程失败：{err or out}")
        data = json.loads(target.read_text(encoding="utf-8"))
        return int(data.get("n", 0))

    def test_control_without_lock_loses_update(self):
        """**反向自检**：不加锁时必然丢更新（最终值为 1）。

        没有这条，`test_lock_preserves_both_writes` 可能是「无论怎样都会过」的空壳。
        """
        got = self._run_two("nolock")
        self.assertEqual(got, 1,
                         f"无锁对照组期望丢更新（1），实际 {got} —— 测试环境不满足前提，"
                         "下面的加锁断言就不再有说服力")

    def test_lock_preserves_both_writes(self):
        """加锁后两次读-改-写都保留（最终值为 2）。"""
        got = self._run_two("lock")
        self.assertEqual(got, 2,
                         f"跨进程锁失效：最终值 {got}（应为 2）—— 两个进程的更新被互相覆盖")

    def test_nested_acquire_in_same_thread_does_not_deadlock(self):
        """同线程重入不得自锁死等（msvcrt 按句柄加锁，重复加会死等）。

        用一个看门狗线程超时判失败，避免测试挂死整个 CI。
        """
        from gcm.util.filelock import cross_process_lock

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g6710_nest_"))
        target = tmp / "x.json"
        done = threading.Event()
        error: list = []

        def _work():
            try:
                with cross_process_lock(target, label="outer"):
                    with cross_process_lock(target, label="inner"):
                        pass
            except Exception as e:  # noqa: BLE001
                error.append(e)
            finally:
                done.set()

        th = threading.Thread(target=_work, daemon=True)
        th.start()
        self.assertTrue(done.wait(15),
                        "同线程嵌套获取死锁了 —— 重入保护失效")
        self.assertEqual(error, [], f"嵌套获取抛异常：{error}")

    def test_settings_store_takes_cross_process_lock(self):
        """静态守卫：`SettingsStore.load/save` 都必须在跨进程临界区内。"""
        src = (ROOT / "gcm/db/settings.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.ClassDef) and n.name == "SettingsStore"), None)
        self.assertIsNotNone(cls, "找不到 SettingsStore")
        used: dict[str, bool] = {}
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and node.name in ("load", "save"):
                used[node.name] = any(
                    (getattr(c.func, "id", None) == "cross_process_lock")
                    for c in ast.walk(node) if isinstance(c, ast.Call))
        self.assertTrue(used.get("load"), "SettingsStore.load 未加跨进程锁")
        self.assertTrue(used.get("save"), "SettingsStore.save 未加跨进程锁")

    def test_settings_store_concurrent_save_is_serialized(self):
        """行为断言：并发 save 后文件可读且为**最后一次**完整写入（非半截 JSON）。

        真正防的是「一个进程写 tmp 的同时另一个 replace」这类交错 ——
        用多线程快速连写来打概率，同时断言最终文件始终是合法 JSON。
        """
        from gcm.db.settings import Settings, SettingsStore

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g6710_store_"))
        store = SettingsStore(tmp / "settings.json")
        errors: list = []

        def _writer(i: int):
            try:
                for _ in range(5):
                    s = store.load()
                    s.concurrency = 1 + i
                    store.save(s)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"并发保存抛异常：{errors}")
        raw = (tmp / "settings.json").read_text(encoding="utf-8")
        json.loads(raw)  # 半截 JSON 会在这里抛 —— 这就是断言本身
        got = store.load()
        self.assertIn(got.concurrency, (1, 2, 3, 4))


# ============================================================ G67-22
_CHILD_IP = r"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.environ["GCM_ROOT"])
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from gcm.app.engine import SyncEngine
from gcm.models import RepoSpec
from gcm.db.repo_db import load_progress

base = Path(tempfile.mkdtemp())
remotes = {}
def sh(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)
for i in range(4):
    r = base / f"rem{i}"; r.mkdir()
    sh(["git", "init", "--bare", "-q"], r)
    w = base / f"w{i}"; w.mkdir()
    sh(["git", "init", "-q"], w)
    sh(["git", "config", "user.email", "a@b.c"], w)
    sh(["git", "config", "user.name", "t"], w)
    (w / "f.txt").write_text("x")
    sh(["git", "add", "."], w); sh(["git", "commit", "-qm", "i"], w)
    sh(["git", "remote", "add", "origin", str(r)], w)
    sh(["git", "push", "-q", "origin", "HEAD:main"], w)
    remotes[i] = r

d = base / "data"; d.mkdir(); pp = d / "progress.json"
eng = SyncEngine(base / "clones", db=None, progress_path=pp, concurrency=2)
specs = [RepoSpec(owner=f"o{i}", repo=f"r{i}", url_https=str(remotes[i])) for i in range(4)]
eng.launch(specs)
mid = list(load_progress(pp).get("in_progress", {}).keys())
eng.shutdown()            # 不得等：立刻 shutdown
after = list(load_progress(pp).get("in_progress", {}).keys())
print(json.dumps({"mid": mid, "after": after}))
"""


class TestShutdownClearsInProgress(unittest.TestCase):
    """G67-22：`shutdown()` 后 `in_progress` 必须真的为空。

    原实现只 `cancel_all()` + 清空 + 落盘，**不等在跑 worker 收敛**。
    在跑的 worker 稍后会回调 `_on_result`，把 `in_progress[key]` 又写回去 ——
    于是「已清空」只在快机器上偶然成立，慢一步就留下残留，
    下次启动误报「上次未完成」。

    这里**立刻** shutdown（不等自然完成），正是最能暴露竞态的时序；
    并且断言 launch 时确实登记过（否则等于什么都没验证）。
    """

    def test_immediate_shutdown_leaves_no_in_progress(self):
        import subprocess

        env = dict(os.environ)
        env["GCM_ROOT"] = str(ROOT)
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run([sys.executable, "-c", _CHILD_IP], env=env, cwd=str(ROOT),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        self.assertEqual(r.returncode, 0, f"子进程失败：{r.stderr[-800:]}")
        payload = json.loads((r.stdout or "").strip().splitlines()[-1])
        self.assertTrue(payload["mid"],
                        "前提不成立：launch 后应登记过 in_progress（否则本测试没验证任何东西）")
        self.assertEqual(payload["after"], [],
                         f"shutdown 后仍有残留 in_progress：{payload['after']} —— "
                         "晚到的 _on_result 把已清空的清单写回去了")


class TestCrossProcessLockNesting(unittest.TestCase):
    """G68-2：**不同路径**嵌套获取时，内层锁必须仍然生效。

    `cross_process_lock` 最初用「重入深度计数」判断是否跳过文件锁 —— 那是错的：
    `RLock` 可重入、文件锁不可重入，所以**同一路径**重入要跳过（否则自锁死等），
    但**不同路径**嵌套时若也跳过，内层那条路径就毫无跨进程保护。

    **实测证据（修复前）**：P1 持锁 A 后进入锁 B（不同路径），P2 独立抢占锁 B ——
    P1 临界区 [0, 2.51s] 与 P2 [1.43, 3.94s] **重叠 1.08 秒**，两个进程同时读-改-写 B。
    修复后同一个探针：P2 在 P1 结束后 0.74s 才进入，不重叠。

    判定方式刻意用**区间重叠**而不是「最终计数」：计数依赖两次操作恰好交错，
    进程启动耗时会让它假通过（本测试的早期版本就是这么假 PASS 的）。
    """

    _NEST_CHILD = r"""
import os, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["GCM_ROOT"])
from gcm.util.filelock import cross_process_lock
A = Path(sys.argv[1]); B = Path(sys.argv[2]); bar = Path(sys.argv[3]); hold = float(sys.argv[4])
with cross_process_lock(A, label="A"):
    (bar / "p1_ready").write_text("1", encoding="utf-8")
    with cross_process_lock(B, label="B"):          # 嵌套、**不同**路径
        (bar / "p1_in").write_text(str(time.time()), encoding="utf-8")
        time.sleep(hold)
        (bar / "p1_out").write_text(str(time.time()), encoding="utf-8")
"""

    _PLAIN_CHILD = r"""
import os, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["GCM_ROOT"])
from gcm.util.filelock import cross_process_lock
B = Path(sys.argv[2]); bar = Path(sys.argv[3]); hold = float(sys.argv[4])
while not (bar / "p1_ready").exists():
    time.sleep(0.005)
with cross_process_lock(B, label="B"):
    (bar / "p2_in").write_text(str(time.time()), encoding="utf-8")
    time.sleep(hold)
    (bar / "p2_out").write_text(str(time.time()), encoding="utf-8")
"""

    def test_inner_lock_of_a_different_path_is_still_exclusive(self):
        import subprocess
        import time as _t

        HOLD = "2.0"
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g682_"))
        lock_a, lock_b, bar = tmp / "a.json", tmp / "b.json", tmp / "bar"
        bar.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["GCM_ROOT"] = str(ROOT)
        env["PYTHONIOENCODING"] = "utf-8"

        def spawn(code):
            return subprocess.Popen(
                [sys.executable, "-c", code, str(lock_a), str(lock_b), str(bar), HOLD],
                cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace")

        p1 = spawn(self._NEST_CHILD)
        try:
            # 等 P1 **真正进入** B 再启动 P2 —— 否则进程启动耗时会让区间假不重叠
            deadline = _t.monotonic() + 30
            while not (bar / "p1_in").exists() and _t.monotonic() < deadline:
                _t.sleep(0.01)
            self.assertTrue((bar / "p1_in").exists(), "P1 未能在 30s 内进入内层锁")
            p2 = spawn(self._PLAIN_CHILD)
            o2, e2 = p2.communicate(timeout=120)
            self.assertEqual(p2.returncode, 0, f"P2 失败：{e2 or o2}")
            o1, e1 = p1.communicate(timeout=120)
            self.assertEqual(p1.returncode, 0, f"P1 失败：{e1 or o1}")
        finally:
            for p in (p1,):
                if p.poll() is None:
                    p.kill()

        def _ts(name):
            f = bar / name
            return float(f.read_text(encoding="utf-8")) if f.exists() else None

        i1, o_1, i2, o_2 = _ts("p1_in"), _ts("p1_out"), _ts("p2_in"), _ts("p2_out")
        self.assertNotIn(None, (i1, o_1, i2, o_2), "有进程没走完，判定不可靠")
        overlap = max(i1, i2) < min(o_1, o_2)
        self.assertFalse(
            overlap,
            f"内层锁未生效：两段临界区重叠（P1 [{i1:.3f},{o_1:.3f}] P2 [{i2:.3f},{o_2:.3f}]）"
            " —— 不同路径嵌套时不得跳过文件锁")

    def test_same_path_nesting_still_reentrant(self):
        """反向约束：**同一路径**重入仍必须直接复用外层锁（否则 msvcrt 自锁死等）。"""
        from gcm.util.filelock import cross_process_lock

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g682_same_"))
        target = tmp / "x.json"
        done = threading.Event()
        err: list = []

        def _work():
            try:
                with cross_process_lock(target, label="outer"):
                    with cross_process_lock(target, label="inner"):
                        pass
            except Exception as e:  # noqa: BLE001
                err.append(e)
            finally:
                done.set()

        threading.Thread(target=_work, daemon=True).start()
        self.assertTrue(done.wait(15), "同一路径重入死锁了 —— 重入保护失效")
        self.assertEqual(err, [], f"重入抛异常：{err}")


class TestConcurrentSettingsSave(unittest.TestCase):
    """G67-23：同进程多线程并发 save 不得失败（tmp 名必须唯一）。

    原实现固定用 `settings.json.tmp`：并发保存时两个线程同时打开同一 tmp，
    一个 `os.replace` 走了它，另一个就撞 `PermissionError [WinError 5]`
    （Windows 不允许替换被占用的文件）。**压测实测**：8 线程全速 churn 出 3 次失败。
    不丢已有数据（原子替换本身安全），但**那一次保存被静默丢弃**，
    用户表现为「刚改的设置没保存」。
    """

    def test_eight_threads_churn_without_error(self):
        import json as _json

        from gcm.db.settings import SettingsStore

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g6723_"))
        store = SettingsStore(tmp / "settings.json")
        errors: list[str] = []
        stop = threading.Event()

        def _churn(k: int):
            try:
                while not stop.is_set():
                    s = store.load()
                    s.concurrency = 1 + (k % 31)
                    store.save(s)
                    # 半截 JSON 会在这里抛 —— 断言「任何时刻文件都是完整可解析的」
                    _json.loads((tmp / "settings.json").read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=_churn, args=(i,), daemon=True) for i in range(8)]
        for t in threads:
            t.start()
        time.sleep(6)
        stop.set()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors[:5], [], f"并发保存失败 {len(errors)} 次：{errors[:5]}")
        self.assertTrue(_json.loads((tmp / "settings.json").read_text(encoding="utf-8")) is not None)
        self.assertEqual([p.name for p in tmp.glob("*.tmp")], [],
                         "残留 tmp 文件 —— 覆盖路径可能仍共用固定名")

    def test_tmp_name_is_unique_per_process_and_thread(self):
        """静态守卫：tmp 名必须含 pid 与线程 id（防止有人改回固定名）。"""
        src = (ROOT / "gcm/db/settings.py").read_text(encoding="utf-8")
        self.assertIn("threading.get_ident()", src,
                      "tmp 名未含线程 id —— 同进程并发保存会互相踩")
        self.assertIn("os.getpid()", src, "tmp 名未含 pid —— 多实例并发保存会互相踩")
        self.assertNotIn('with_suffix(".json.tmp")', src,
                         "又回到固定 tmp 名了（G67-23 回归）")


if __name__ == "__main__":
    unittest.main()
