"""Rev16 B1 守卫：验收矩阵 §11 的五个 P1（核心链路只读复审发现）。

**C1（跨实例覆盖）**：`changelog 9.18.9` 声称的 `SettingsStore.update()` 实际不存在，
`save()` 用调用方的整份旧快照覆盖磁盘 —— 两个实例同时开着时，「随手拖一下列宽」
会把另一个实例刚改的并发数回滚掉。本守卫断言：`update()` 存在、锁内读-改-写、
以及旧快照 `save()` 不再回滚他人字段。

**C2（_DEFAULTS 污染）**：`dict(_DEFAULTS)` 浅拷贝使嵌套 dict（`ui_state` /
`host_tokens`）被就地改写后污染模块级默认值。本守卫断言：新 `SettingsStore`
的默认值不被上一个实例写脏。

**C3（CLI 超时假成功）**：600s 兜底到期后引擎仍在跑，CLI 只按已收结果算退出码
→ `ok=true` / exit 0。本守卫断言：超时后 cancel + 未完成项给终态 + 退出码非 0。

**C4（重试结果错行）**：自动重投用新 index 走 `launch()`，UI 表格残留上一批的
`UserRole+1` 旧 index → `_row_for` 反查命中旧行，结果写到别的仓库那一行，
且收尾播报因 `owner.busy` 已 False 被跳过。本守卫断言：重投批次 UI 重建行 +
`busy` 置回 True + 行号与 `engine.specs` 严格对齐。

**A7（MCP 审计缺失）**：`agent-contract.md` §10.2「放行后的写操作必须审计」，
但 MCP `clone_batch` / `job_cancel` 没有审计行（HTTP 侧有 `_audit`）。
本守卫断言：放行路径真实落 `.gcm-mcp-audit.jsonl`，只读路径不落。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db import settings as S  # noqa: E402


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._td.name)
        self.addCleanup(self._td.cleanup)


# ============================================================
# C1: SettingsStore.update() —— 锁内 read-modify-write
# ============================================================

class TestSettingsStoreUpdate(_TmpCase):
    def _two_instances(self):
        """A/B 两个独立 SettingsStore 实例（模拟两个窗口/进程）。"""
        path = self.dir / "settings.json"
        a = S.SettingsStore(path)
        sa = a.load()
        sa.concurrency = 8
        a.save(sa)
        b = S.SettingsStore(path)
        sb = b.load()
        sb.concurrency = 16
        b.save(sb)
        return path, a, sa, b, sb

    def test_update_method_exists(self):
        store = S.SettingsStore(self.dir / "s.json")
        self.assertTrue(hasattr(store, "update"),
                        "changelog 9.18.9 声称的 update() 不存在（假闭环）")

    def test_update_writes_only_given_fields(self):
        path = self.dir / "s.json"
        store = S.SettingsStore(path)
        s = store.load()
        s.concurrency = 4
        s.download_dir = str(self.dir / "dl")
        store.save(s)
        # 只改 concurrency，不能把 download_dir 抹回默认
        store.update(concurrency=9)
        reread = S.SettingsStore(path).load()
        self.assertEqual(reread.concurrency, 9)
        self.assertEqual(reread.download_dir, str(self.dir / "dl"),
                         "update() 丢掉了未提及字段")

    def test_update_survives_stale_snapshot_save(self):
        """核心场景：A 用 update() 改列宽，B 刚 update() 的并发数不得被回滚。"""
        path, a, sa, b, _sb = self._two_instances()
        # B 用 update() 把并发改到 16（A 的快照仍是 8）
        b.update(concurrency=16)
        # A 用 update() 保存列宽（多写者场景的正确姿势）—— 只动 ui_state
        a.update(ui_state={"manage_columns": {"3": 200}})
        reread = S.SettingsStore(path).load()
        self.assertEqual(reread.concurrency, 16,
                         "A 的 update() 把 B 刚改的并发数回滚了")
        self.assertEqual(reread.ui_state.get("manage_columns"), {"3": 200})

    def test_workspace_writers_use_update_not_save(self):
        """UI 工作区写入点（列宽/分割比例）必须走 update()，不许 save(旧快照)。"""
        mt = (ROOT / "gcm" / "ui" / "manage_table.py").read_text(encoding="utf-8")
        sw = (ROOT / "gcm" / "ui" / "settings_workspace.py").read_text(encoding="utf-8")
        self.assertIn("store.update(", mt,
                      "manage_table._save_column_widths 应改用 store.update(ui_state=…)")
        self.assertIn("store.update(", sw,
                      "settings_workspace._save_sizes 应改用 store.update(ui_state=…)")
        # 反向自检：不许再出现真实调用（注释里的历史记录不算）
        mt_calls = [l for l in mt.splitlines()
                    if "store.save(" in l and not l.strip().startswith(("#", "//"))]
        sw_calls = [l for l in sw.splitlines()
                    if "store.save(" in l and not l.strip().startswith(("#", "//"))]
        self.assertEqual(mt_calls, [],
                         f"manage_table 仍在用 save(旧快照)：{mt_calls}")
        self.assertEqual(sw_calls, [],
                         f"settings_workspace 仍在用 save(旧快照)：{sw_calls}")


# ============================================================
# C2: _DEFAULTS 浅拷贝污染
# ============================================================

class TestDefaultsNotPolluted(_TmpCase):
    def test_ui_state_write_does_not_dirty_defaults(self):
        path = self.dir / "s.json"
        store = S.SettingsStore(path)
        s = store.load()
        s.ui_state = {"manage_columns": {"3": 150}}
        store.save(s)
        # 新实例的默认值必须是干净的
        fresh = S.SettingsStore(self.dir / "fresh.json").load()
        self.assertEqual(fresh.ui_state, {},
                         "_DEFAULTS['ui_state'] 被上一个实例写脏")

    def test_defaults_deepcopy_in_source(self):
        src = (ROOT / "gcm" / "db" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("deepcopy", src,
                      "settings.py 必须对嵌套默认值做深拷贝（dict() 浅拷贝会被就地污染）")


# ============================================================
# C3: CLI 超时不假成功
# ============================================================

class TestCliTimeoutNoFalseSuccess(_TmpCase):
    def _run_stub(self, code_patch: str) -> subprocess.CompletedProcess:
        """起一个「引擎永远 busy」的 stub CLI 进程，把 600s 上限压到 2s。"""
        prog = self.dir / "stub_cli.py"
        prog.write_text(code_patch, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "-I", str(prog)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=90, env=env,
            cwd=str(ROOT))

    TIMEOUT_PATCH = r'''
import json, pathlib, sys, time
sys.path.insert(0, r"{root}")
os_dir = r"{tmp}"
from PyQt6.QtWidgets import QApplication
from gcm import cli
from gcm.app.engine import SyncEngine
from gcm.models import RepoSpec

cli._CLI_TIMEOUT_SEC = 2.0

app = QApplication.instance() or QApplication([])

class StubEngine(SyncEngine):
    def launch(self, specs, **kw):
        from gcm.app.worker import CancelFlag
        self.busy = True
        self._pending = len(specs)
        for i, sp in enumerate(specs):
            self.specs[i] = sp
            self.flags[i] = CancelFlag()
            self._pending_queue.append((i, sp, self.flags[i]))
        return len(specs)

eng = StubEngine(root=pathlib.Path(os_dir) / "clones", db=None,
                 progress_path=None, concurrency=1)
specs = [RepoSpec(owner="demo", repo="repo",
                  url_https="https://github.com/demo/repo")]
results = []
eng.result.connect(lambda i, r: results.append(r))
eng.launch(specs)
deadline = time.time() + cli._CLI_TIMEOUT_SEC
while eng.busy and time.time() < deadline:
    app.processEvents()
    time.sleep(0.02)
timed_out = eng.busy
print("TIMEOUT_FLAG", timed_out, flush=True)
cli._timeout_finalize(app, eng, results, json_mode=True)
print("POST_STATE", json.dumps({
    "busy": eng.busy,
    "n_results": len(results),
    "statuses": sorted(r.status.value for r in results),
}, ensure_ascii=False), flush=True)
'''

    def test_timeout_finalize_exists_and_cancels(self):
        """超时收尾函数必须存在且把引擎拉回非 busy。"""
        prog = self.TIMEOUT_PATCH.replace("{root}", str(ROOT)).replace("{tmp}", str(self.dir))
        proc = self._run_stub(prog)
        out = proc.stdout or ""
        self.assertIn("TIMEOUT_FLAG True", out, "stub 引擎应当超时（还 busy）")
        state = json.loads(out.split("POST_STATE ", 1)[1].splitlines()[0])
        self.assertFalse(state["busy"], "超时收尾后引擎必须回到非 busy")
        self.assertGreaterEqual(state["n_results"], 1, "未完成项必须收到合成终态")
        self.assertIn("cancelled", state["statuses"],
                      "超时后未完成项必须收到 CANCELLED 终态")

    def test_run_clone_exit_nonzero_on_timeout(self):
        """run_clone 超时路径必须返回非 0 退出码（不能按部分结果算成功）。"""
        from gcm import cli

        # 有 cancelled 时退出码必须是 1（agent 不能把部分成功当全成功）
        counts = {"success": 1, "cancelled": 2}
        self.assertEqual(cli._exit_code_of(counts), 1)


# ============================================================
# C4: 重试结果写对行
# ============================================================

class TestRetryRowsRebuilt(_TmpCase):
    def setUp(self):
        super().setUp()
        from PyQt6.QtWidgets import QApplication
        self._app = QApplication.instance() or QApplication([])
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        store = SettingsStore(self.dir / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        s.download_dir = str(self.dir / "clones")
        s.concurrency = 1
        s.retries = 0
        store.save(s)
        (self.dir / "clones").mkdir(parents=True, exist_ok=True)
        self.win = MainWindow(data_dir=self.dir)
        self.win.show()
        self._app.processEvents()

    def tearDown(self):
        try:
            # C4 测试会把 busy 置 True（模拟运行中批次）—— 必须先复位，
            # 否则 closeEvent 走 QMessageBox.question（offscreen 下无人应答 → 挂死）
            self.win.busy = False
            eng = getattr(self.win, "engine", None)
            if eng is not None:
                eng.busy = False
                eng._pending_queue.clear()
            self.win.close()
            self.win.deleteLater()
        finally:
            self._app.processEvents()

    def test_retry_batch_resets_rows_and_busy(self):
        """重投批次（retry_batch_started 信号）必须重建行 + busy 置回 True。"""
        w = self.win
        engine = w.engine
        # 模拟「上一批已收尾」：busy=False，表格残留 3 行旧行
        w.busy = False
        from gcm.models import RepoSpec
        stale = RepoSpec(owner="demo", repo="alpha",
                         url_https="https://github.com/demo/alpha")
        w._prepare_table(3)
        for i in range(3):
            w.progress_panel._add_table_row(i, stale)
            w.row_specs[i] = stale
        # 引擎新批次 specs = {0: beta, 1: gamma}
        from gcm.app.worker import CancelFlag
        beta = RepoSpec(owner="demo", repo="beta",
                        url_https="https://github.com/demo/beta")
        gamma = RepoSpec(owner="demo", repo="gamma",
                         url_https="https://github.com/demo/gamma")
        engine.busy = True
        engine.specs.clear()
        engine.specs[0] = beta
        engine.specs[1] = gamma
        engine.flags.clear()
        engine.flags[0] = CancelFlag()
        engine.flags[1] = CancelFlag()
        # 引擎发重投批次开始信号（_retry_tick 在 launch 成功后 emit；这里直发，
        # 信号链与真实路径一致：engine.retry_batch_started → UI 重建）
        engine.retry_batch_started.emit(2)
        self._app.processEvents()
        # 断言 1：busy 置回 True（收尾播报才不会被打断）
        self.assertTrue(w.busy, "重投批次开始后 owner.busy 必须为 True")
        # 断言 2：表格行按 engine.specs 重建（3 行旧行被替换成 2 行新行）
        table = w.progress_panel.table
        self.assertEqual(table.rowCount(), 2,
                         "重投批次必须重建行，旧行残留会让结果写错行")

    def test_row_lookup_prefers_current_batch(self):
        """同一 index 在新批次里指向新 spec —— 旧行的 UserRole 必须被清掉。"""
        w = self.win
        engine = w.engine
        from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus
        # 上一批：index 0 = alpha（旧行）
        old_spec = RepoSpec(owner="demo", repo="alpha",
                            url_https="https://github.com/demo/alpha")
        w._prepare_table(1)
        w.progress_panel._add_table_row(0, old_spec)
        w.row_specs[0] = old_spec
        # 新批次重投 beta：engine.specs = {0: beta}
        new_spec = RepoSpec(owner="demo", repo="beta",
                            url_https="https://github.com/demo/beta")
        engine.busy = True
        engine.specs.clear()
        engine.specs[0] = new_spec
        # 重投批次开始信号 → UI 重建行（旧行 alpha 被替换为 beta）
        engine.retry_batch_started.emit(1)
        self._app.processEvents()
        # 引擎报 index 0 的结果（beta 成功）
        res = SyncResult(spec=new_spec, status=SyncStatus.SUCCESS,
                         action=SyncAction.CLONED, message="ok")
        w.progress_panel._on_worker_result(0, res)
        self._app.processEvents()
        # beta 那行必须拿到结果；旧行（alpha）不能被误写
        table = w.progress_panel.table
        self.assertEqual(table.rowCount(), 1)
        status_item = table.item(0, 2)
        self.assertIsNotNone(status_item)
        self.assertIn("成功", status_item.text(),
                      "新批次 index 0 的结果被写到了上一批 alpha 的行里")


# ============================================================
# A7: MCP 写操作审计
# ============================================================

class TestMcpMutateAudit(_TmpCase):
    def _call(self, name, args, allow_mutate):
        from gcm import mcp_server
        return mcp_server._handle_call(
            msg_id=1, params={"name": name, "arguments": args},
            data_dir=self.dir, allow_export=False, allow_mutate=allow_mutate)

    def _audit_lines(self):
        f = self.dir / ".gcm-mcp-audit.jsonl"
        if not f.exists():
            return []
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_clone_batch_writes_audit_line(self):
        """放行的 clone_batch 必须落审计行（工具名 + 打码参数）。

        审计发生在**调度子进程之前**（_handle_call 进 clone 分支即落），
        patch 掉 subprocess 不触网也能断言审计语义。
        """
        from gcm.db.repo_db import Database
        db = Database(self.dir / "repos.db")
        db.close()
        with mock.patch("subprocess.run") as _run:
            _run.return_value = mock.Mock(returncode=0, stdout="{}")
            resp = self._call("clone_batch", {"urls": ["https://github.com/demo/x"]},
                              allow_mutate=True)
        self.assertNotIn("error", resp, f"clone_batch 放行路径不应报错：{resp}")
        lines = self._audit_lines()
        self.assertTrue(lines, "放行的 clone_batch 没有落审计行")
        self.assertEqual(lines[0]["tool"], "clone_batch")
        # 负载打码过（redact 不抛即可；URL 无凭据则原样）
        self.assertIn("demo", lines[0]["args"])

    def test_dry_run_no_audit(self):
        """dry_run 是纯预览，不落审计。"""
        from gcm.db.repo_db import Database
        db = Database(self.dir / "repos.db")
        db.close()
        self._call("clone_batch", {"urls": ["https://github.com/demo/x"],
                                   "dry_run": True}, allow_mutate=True)
        self.assertEqual(self._audit_lines(), [], "dry_run 不应落审计行")

    def test_readonly_tools_never_audit(self):
        """只读工具永不落审计。"""
        from gcm.db.repo_db import Database
        db = Database(self.dir / "repos.db")
        db.close()
        self._call("list_repos", {}, allow_mutate=True)
        self._call("status", {}, allow_mutate=True)
        self.assertEqual(self._audit_lines(), [])


    def test_audit_redacts_before_truncation(self):
        """复审 P1 回归：先打码后截断 —— 凭据不能因 [:500] 切断而明文落盘。

        复现方式（复审实测）：多 URL 使 json.dumps 超过 500 字符，且最后一个
        URL 带内嵌凭据 → 旧实现先截断，token 残段原样穿过 redact。
        """
        from gcm.db.repo_db import Database
        db = Database(self.dir / "repos.db")
        db.close()
        # 凭据 URL 放在前列（确保在 500 字符截断窗内被打码），filler 撑爆总量
        cred_url = "https://ghp_" + "1" * 36 + "@github.com/u/r"
        filler = "https://github.com/x/" + "a" * 600
        args = {"urls": [cred_url, filler]}
        with mock.patch("subprocess.run") as _run:
            _run.return_value = mock.Mock(returncode=0, stdout="{}")
            self._call("clone_batch", args, allow_mutate=True)
        lines = self._audit_lines()
        self.assertTrue(lines, "放行的 clone_batch 应落审计行")
        payload = lines[0]["args"]
        self.assertNotIn("ghp_1", payload,
                         "凭据残段明文出现在审计行（截断发生在打码之前）")
        self.assertIn("***", payload, "打码标记必须存在")

    def test_denied_mutate_no_audit(self):
        """未放行的 mutate 拒绝路径不落审计（拒绝本身没有执行写操作）。"""
        from gcm.db.repo_db import Database
        db = Database(self.dir / "repos.db")
        db.close()
        resp = self._call("clone_batch", {"urls": ["https://github.com/demo/x"]},
                          allow_mutate=False)
        self.assertIn("error", resp)
        self.assertEqual(self._audit_lines(), [])


# ============================================================
# B6 拾遗：settings 域复审 P2（记账缺口 + ui_state 粒度）
# ============================================================

class TestUndecryptableAccounting(_TmpCase):
    """复审 7.1 P2-1：记账生命周期补全。

    ① 解密成功时必须清掉陈旧记账（否则清空字段时旧密文被复活）；
    ② proxy 读回必须走记账链路（此前走 _decrypt_or_keep，换机器场景
       proxy 密文解不开会被空串覆盖，且 undecryptable_fields 不提示）。
    """

    GARBAGE = "enc:v1:%%%not-base64%%%"

    def _store(self):
        return S.SettingsStore(self.dir / "s.json")

    def _write(self, payload: dict) -> None:
        (self.dir / "s.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _read(self) -> dict:
        return json.loads((self.dir / "s.json").read_text(encoding="utf-8"))

    def test_decrypt_success_clears_stale_entry(self):
        self._write({"token": self.GARBAGE})
        store = self._store()
        s = store.load()
        self.assertIn("token", store.undecryptable_fields())
        # 用户重填新 token（有效明文）→ 保存后陈旧记账必须清掉
        s.token = "valid-plaintext-token"
        store.save(s)
        self.assertNotIn("token", store.undecryptable_fields(),
                         "解密成功后陈旧记账未清除")
        # 再 update(token="") 显式清空 → 空值落盘（不再被旧密文复活）
        store.update(token="")
        self.assertEqual(self._read()["token"], "",
                         "清空 token 后旧密文被记账复活")

    def test_proxy_undecryptable_is_tracked(self):
        self._write({"proxy": self.GARBAGE})
        store = self._store()
        s = store.load()
        self.assertIn("proxy", store.undecryptable_fields(),
                      "proxy 密文解不开应进入记账（否则 save 会把它覆盖成空）")
        self.assertEqual(s.proxy, "")
        # 改个无关字段保存 → proxy 密文必须原样保回
        s.concurrency = 8
        store.save(s)
        self.assertEqual(self._read()["proxy"], self.GARBAGE,
                         "proxy 密文被空串覆盖（换机器场景数据丢失）")


class TestUpdateUiStateMerge(_TmpCase):
    """复审 7.1 P2-2：update() 对 ui_state 做 dict 合并（浅合并）。

    多开场景：A 写 manage_columns，B 写 settings_split —— 两者都该保留。
    host_tokens 保持整值替换语义（用户删 host 行 = 明确要删）。
    """

    def _store(self):
        return S.SettingsStore(self.dir / "s.json")

    def test_ui_state_keys_merge_across_instances(self):
        path = self.dir / "s.json"
        a = S.SettingsStore(path)
        a.update(ui_state={"manage_columns": {"3": 200}})
        b = S.SettingsStore(path)
        b.update(ui_state={"settings_split": [0.72, 0.28]})
        reread = S.SettingsStore(path).load()
        self.assertEqual(reread.ui_state.get("manage_columns"), {"3": 200},
                         "A 写的 manage_columns 被 B 的 update 整体覆盖了")
        self.assertEqual(reread.ui_state.get("settings_split"), [0.72, 0.28])

    def test_same_key_newer_write_wins(self):
        path = self.dir / "s.json"
        a = S.SettingsStore(path)
        a.update(ui_state={"manage_columns": {"3": 200}})
        b = S.SettingsStore(path)
        b.update(ui_state={"manage_columns": {"3": 300}})
        reread = S.SettingsStore(path).load()
        self.assertEqual(reread.ui_state.get("manage_columns"), {"3": 300},
                         "后写者应当赢（同 key 语义不变）")
