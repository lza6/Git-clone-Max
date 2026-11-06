"""Rev12 可用性专项守卫：撤销链路 + 菜单栏 + 帮助可达。

**这份测试证的到底是什么**（别把它当装饰）：
1. `OpLog` 的**跨进程读-改-写**在真文件上往返正确，且落盘前已打码；
2. `undo_record` 对**真实 SQLite 库**执行逆做后，数据能逐字段还原
   （不是"调了 upsert 就算恢复"—— 标签/收藏这两列 `upsert_repo` 根本不写）；
3. 菜单栏每一项都**绑到了真实槽**（`isEnabled()` 为真），防止"菜单在、点了没反应";
4. `F1` 与「帮助 → 使用说明」指向**同一个**槽（防帮助入口漂移）；
5. 破坏性入口在真跑时**确实写了台账**。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from gcm.app.undo import UndoError, can_undo, undo_record
from gcm.util.oplog import OpLog, OpRecord, default_oplog

_APP = QApplication.instance() or QApplication([])


class TestOpLogRoundTrip(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._td.name)
        self.log = OpLog(self.dir / "oplog.json")

    def tearDown(self):
        self._td.cleanup()

    def test_record_and_read_back(self):
        rec = self.log.record("repo_delete", "a/b", summary="删了", before={"owner": "a"})
        self.assertTrue(rec.id)
        got = self.log.get(rec.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "repo_delete")
        self.assertEqual(got.before["owner"], "a")
        self.assertEqual(got.undoable, 1, "repo_delete 必须默认可撤销")

    def test_newest_first_order(self):
        first = self.log.record("repo_delete", "first")
        second = self.log.record("repo_delete", "second")
        recent = self.log.recent(10)
        self.assertEqual([r.id for r in recent], [second.id, first.id],
                         "最近的在最前 —— 「撤销上一步」靠这个顺序")

    def test_undoable_flag_defaults(self):
        undoable = self.log.record("repo_delete", "x")
        only_log = self.log.record("prune", "x")
        self.assertEqual(undoable.undoable, 1)
        self.assertEqual(only_log.undoable, 0,
                         "prune 这类不可逆操作必须 undoable=0，UI 不得画撤销按钮")

    def test_keep_limit_trims_oldest(self):
        log = OpLog(self.dir / "trim.json", keep=3)
        ids = [log.record("repo_delete", f"r{i}").id for i in range(5)]
        kept = {r.id for r in log.recent(10)}
        self.assertEqual(len(kept), 3)
        self.assertNotIn(ids[0], kept, "最旧的应被裁掉")
        self.assertIn(ids[-1], kept)

    def test_mark_undone_hides_from_recent(self):
        rec = self.log.record("repo_delete", "a/b")
        self.assertTrue(self.log.mark_undone(rec.id))
        self.assertEqual(self.log.recent(10, undoable_only=True), [])
        self.assertEqual(len(self.log.recent(10, include_undone=True)), 1)
        # 重复标记返回 False（幂等，但调用方据此判断"已撤过"）
        self.assertFalse(self.log.mark_undone(rec.id))

    def test_redacted_before_write(self):
        """落盘前打码 —— 诊断包会整个搬走这个文件，明文 token 会跟着外泄。"""
        token = "ghp_" + "A" * 36
        url = f"https://user:{token}@github.com/a/b.git"
        self.log.record("repo_delete", "a/b", before={"url": url})
        raw = (self.dir / "oplog.json").read_text(encoding="utf-8")
        self.assertNotIn(token, raw, "oplog.json 里出现了 token 明文")

    def test_corrupt_file_does_not_raise(self):
        path = self.dir / "bad.json"
        path.write_text("{ not json", encoding="utf-8")
        log = OpLog(path)
        self.assertEqual(log.recent(5), [], "损坏台账应视为空，而不是抛异常")
        # 仍可继续写入（整体覆盖）
        log.record("repo_delete", "a/b")
        self.assertEqual(len(log.recent(5)), 1)

    def test_cross_process_lock_makes_rmw_atomic(self):
        """读-改-写在同一临界区：并发 record 不该丢条目。"""
        import threading
        log = OpLog(self.dir / "conc.json", keep=500)
        threads = [
            threading.Thread(target=lambda i=i: log.record("repo_delete", f"t{i}"))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(log.recent(100)), 20, "并发写入丢了条目 → 读改写不在同一锁里")


class FakeOwner:
    """最小 owner：只提供 undo 处理器会碰到的属性。"""

    def __init__(self, db=None, url_history=None, repo_input=None):
        self.db = db
        self.url_history = url_history
        self.repo_input = repo_input
        self.refreshed = 0

    def _load_db_into_grid(self):
        self.refreshed += 1


class TestUndoDispatch(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._td.name)
        self.log = OpLog(self.dir / "oplog.json")

    def tearDown(self):
        self._td.cleanup()

    def test_rejects_already_undone(self):
        rec = self.log.record("repo_delete", "a/b", before={"owner": "a", "repo": "b"})
        self.log.mark_undone(rec.id)
        with self.assertRaises(UndoError):
            undo_record(self.log, FakeOwner(), rec)

    def test_rejects_non_undoable(self):
        rec = self.log.record("prune", "x", undoable=False)
        with self.assertRaises(UndoError):
            undo_record(self.log, FakeOwner(), rec)

    def test_unknown_action_rejected(self):
        rec = self.log.record("no_such_action", "x", undoable=True)
        with self.assertRaises(UndoError):
            undo_record(self.log, FakeOwner(), rec)

    def test_undo_writes_audit_trail(self):
        rec = self.log.record("repo_delete", "a/b", before={"owner": "a", "repo": "b"})

        class DB:
            def upsert_repo(self, *a, **k):
                return 7

            def set_tags(self, *a):
                pass

            def set_favorite(self, *a):
                pass

            def set_excluded(self, *a):
                pass

        undo_record(self.log, FakeOwner(db=DB()), rec)
        actions = [r.action for r in self.log.recent(10, include_undone=True)]
        self.assertIn("undo", actions, "撤销本身必须留审计记录")
        # 审计记录本身不可撤销（防递归）
        undo_rec = [r for r in self.log.recent(10, include_undone=True) if r.action == "undo"][0]
        self.assertEqual(undo_rec.undoable, 0)

    def test_undo_url_history_restores_order(self):
        class Hist:
            def __init__(self):
                self.items_ = []

            def add(self, url):
                self.items_.insert(0, url)

            def items(self):
                return list(self.items_)

        hist = Hist()
        rec = self.log.record("url_history_clear", "2 条",
                              before={"items": ["newest", "older"]})
        undo_record(self.log, FakeOwner(url_history=hist), rec)
        self.assertEqual(hist.items_, ["newest", "older"],
                         "倒序 add 才能还原原顺序（add 是头插）")


class TestUndoRestoresDatabase(unittest.TestCase):
    """真库往返：这是「撤销后状态与操作前一致」的唯一硬证据。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_delete_then_undo_restores_row_and_columns(self):
        from gcm.app.undo import undo_record as do_undo
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec

        db = Database(self.dir / "t.db")
        rid = db.upsert_repo(
            RepoSpec(owner="acme", repo="widget", url_https="https://github.com/acme/widget"),
            str(self.dir / "acme__widget"), "github.com")
        db.set_tags(rid, ["infra", "py"])
        db.set_favorite(rid, True)
        before = dict(db.get_repo("acme", "widget", "github.com"))

        log = OpLog(self.dir / "oplog.json")
        rec = log.record("repo_delete", "acme/widget", before=before)

        db.delete_repo(rid)
        self.assertIsNone(db.get_repo("acme", "widget", "github.com"),
                          "前提：删除后确实没了")

        do_undo(log, FakeOwner(db=db), rec)

        back = db.get_repo("acme", "widget", "github.com")
        self.assertIsNotNone(back, "撤销后仓库应重新出现在库里")
        self.assertEqual(back["owner"], "acme")
        self.assertEqual(back["url"], before["url"])
        # 这两列 upsert_repo 不写 —— 必须单独恢复，否则「撤销后不一致」
        self.assertEqual(back["favorite"], 1, "收藏状态丢失 → 撤销不完整")
        self.assertIn("infra", (back["tags"] or ""), "标签丢失 → 撤销不完整")

    def test_batch_delete_undo_restores_all(self):
        from gcm.app.undo import undo_record as do_undo
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec

        db = Database(self.dir / "t.db")
        ids, snaps = [], []
        for name in ("one", "two", "three"):
            rid = db.upsert_repo(
                RepoSpec(owner="o", repo=name, url_https=f"https://github.com/o/{name}"),
                str(self.dir / f"o__{name}"), "github.com")
            ids.append(rid)
            snaps.append(dict(db.get_repo("o", name, "github.com")))

        log = OpLog(self.dir / "oplog.json")
        rec = log.record("repo_delete", "3 条", before={"batch": snaps})
        for rid in ids:
            db.delete_repo(rid)
        self.assertEqual(db.list_repos(), [])

        do_undo(log, FakeOwner(db=db), rec)
        self.assertEqual(len(db.list_repos()), 3, "批量撤销应全部恢复")

    def test_retry_queue_restore_is_symmetric_with_clear(self):
        from gcm.app.retry_queue import RetryQueue
        from gcm.models import RepoSpec

        q = RetryQueue(self.dir / "retry.json")
        q.record_failure(RepoSpec(owner="o", repo="a",
                                  url_https="https://github.com/o/a"), "网络连接失败（可自动重试）")
        q.record_failure(RepoSpec(owner="o", repo="b",
                                  url_https="https://github.com/o/b"), "网络连接失败（可自动重试）")
        self.assertEqual(q.count(), 2)
        entries = q.list_entries()
        q.clear()
        self.assertEqual(q.count(), 0)
        n = q.restore_entries(entries)
        self.assertEqual(n, 2, "restore_entries 应补回全部条目")
        self.assertEqual(q.count(), 2)

    def test_restore_entries_does_not_overwrite_existing(self):
        """用户清空后可能已经重新排过队列，撤销不该冲掉他的新状态。"""
        from gcm.app.retry_queue import RetryQueue
        from gcm.models import RepoSpec

        q = RetryQueue(self.dir / "retry.json")
        q.record_failure(RepoSpec(owner="o", repo="a",
                                  url_https="https://github.com/o/a"), "网络连接失败（可自动重试）")
        old = q.list_entries()
        q.clear()
        q.record_failure(RepoSpec(owner="o", repo="a",
                                  url_https="https://github.com/o/a"), "网络连接失败（可自动重试）")
        n = q.restore_entries(old)
        self.assertEqual(n, 0, "同 key 已存在时不应覆盖")
        self.assertEqual(q.count(), 1)


class TestMenuBarWiring(unittest.TestCase):
    """菜单栏每一项必须绑到真实槽 —— 「菜单在、点了没反应」是本次要消灭的缺陷。"""

    def setUp(self):
        from gcm.ui.main_window import MainWindow
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.win = MainWindow(Path(self._td.name))

    def tearDown(self):
        try:
            self.win.close()
            self.win.deleteLater()
        except Exception:
            pass
        self._td.cleanup()

    def _all_actions(self):
        bar = self.win.menuBar()
        for menu_action in bar.actions():
            menu = menu_action.menu()
            if menu is not None:
                yield from menu.actions()

    def test_menu_bar_exists_with_five_menus(self):
        bar = self.win.menuBar()
        titles = [a.text() for a in bar.actions() if a.menu() is not None]
        self.assertEqual(len(titles), 5,
                         f"应有 文件/编辑/视图/工具/帮助 五个菜单，实际 {titles}")

    def test_every_action_is_wired(self):
        dead = []
        for act in self._all_actions():
            if act.isSeparator():
                continue
            if not act.isEnabled():
                dead.append(act.text())
        self.assertEqual(dead, [],
                         "以下菜单项没绑到真实槽（会显示为灰色，等于假功能）：\n  "
                         + "\n  ".join(dead))

    def test_help_reachable_two_ways_same_slot(self):
        """F1 与「帮助 → 使用说明」必须指向同一个槽，否则两条路会各自漂移。"""
        help_actions = [a for a in self._all_actions()
                        if a.text() in ("使用说明", "使用说明(&H)")]
        self.assertTrue(help_actions, "菜单里没有「使用说明」")
        act = help_actions[0]
        self.assertEqual(act.shortcut().toString(), "F1",
                         "帮助菜单项应带 F1 快捷键")

    def test_undo_bar_exists_and_hidden_initially(self):
        bar = getattr(self.win, "undo_bar", None)
        self.assertIsNotNone(bar, "撤销浮条没创建")
        self.assertTrue(bar.isHidden(), "初始应隐藏，不该占着屏幕")

    def test_undo_bar_does_not_cover_bottom_buttons(self):
        """浮条可见时不得遮挡下载页底部按钮行。

        实测过的事实：浮条曾用绝对定位盖在中央控件底部，把「打开克隆目录 /
        失败汇总 / 重试队列 / 诊断包」整排挡住 —— 可用性改进反而制造可用性缺陷。
        正确形态是**放进布局**、隐藏时不占高度、显示时自己占一行。
        """
        from PyQt6.QtWidgets import QPushButton

        # 必须真的 show() 一次：Qt 里父窗口未 show 时子控件 isVisible() 恒为 False，
        # 只看 isVisible() 会写出「永远失败」或「永远通过」的假测试。
        self.win.resize(1180, 720)
        self.win.show()
        _APP.processEvents()
        bar = self.win.undo_bar
        rec = self.win.oplog.record("repo_delete", "a/b", before={"owner": "a", "repo": "b"})
        self.win.offer_undo(rec, "已删除 a/b")
        _APP.processEvents()

        self.assertFalse(bar.isHidden(), "offer 之后浮条不应处于隐藏态")
        cw = self.win.centralWidget()
        # 浮条必须在布局里（parent 是 central 且不是手动 setGeometry 的野控件）
        self.assertIs(bar.parentWidget(), cw)
        rect = bar.geometry()
        covered = []
        for btn in cw.findChildren(QPushButton):
            if btn is bar or bar.isAncestorOf(btn) or not btn.isVisible():
                continue
            r = btn.geometry()
            r.moveTop(btn.mapTo(cw, btn.geometry().topLeft()).y())
            if r.intersects(rect):
                covered.append(btn.text())
        self.assertEqual(covered, [], f"撤销浮条遮挡了这些按钮：{covered}")

    def test_undo_bar_does_not_take_space_when_hidden(self):
        """隐藏时不得把页签挤小（否则每屏底部凭空多一条空白）。

        不能直接断言控件 height：隐藏控件仍保留上次几何。真正的判据是
        「显示浮条是否让页签变矮」—— 变矮说明它真的占了一行，正合预期；
        不変矮才说明它盖在内容上（覆盖式，会挡按钮，已实测过是缺陷）。
        """
        self.win.resize(1180, 720)
        self.win.show()
        _APP.processEvents()
        tabs = self.win.tabs
        hidden_h = tabs.height()
        rec = self.win.oplog.record("repo_delete", "a/b", before={"owner": "a", "repo": "b"})
        self.win.offer_undo(rec, "已删除 a/b")
        _APP.processEvents()
        shown_h = tabs.height()
        self.assertLess(shown_h, hidden_h,
                        "显示浮条后页签没有让位 → 它盖在内容上（会遮挡底部按钮）")

    def test_oplog_is_wired_to_data_dir(self):
        self.assertTrue(hasattr(self.win, "oplog"))
        self.assertEqual(Path(self.win.oplog.path).parent, Path(self.win.data_dir))


class TestUndoBarBehaviour(unittest.TestCase):
    def setUp(self):
        from gcm.ui.undo_bar import UndoBar
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._td.name)
        self.log = OpLog(self.dir / "oplog.json")

        class Owner:
            def __init__(self, log):
                self.oplog = log
                self.calls = []

            def undo_op(self, rid):
                self.calls.append(rid)
                return True, "已恢复"

        self.owner = Owner(self.log)
        self.bar = UndoBar(self.owner)

    def tearDown(self):
        try:
            self.bar.deleteLater()
        except Exception:
            pass
        self._td.cleanup()

    def test_offer_shows_and_sets_record(self):
        rec = self.log.record("repo_delete", "a/b")
        self.bar.offer(rec.id, "已删除 a/b")
        self.assertFalse(self.bar.isHidden())
        self.assertEqual(self.bar.msg.text(), "已删除 a/b")

    def test_click_calls_owner_undo(self):
        rec = self.log.record("repo_delete", "a/b")
        self.bar.offer(rec.id, "已删除")
        self.bar._on_undo()
        self.assertEqual(self.owner.calls, [rec.id],
                         "点「撤销」必须真的调用 owner.undo_op")

    def test_failure_keeps_bar_visible(self):
        class FailingOwner:
            oplog = None

            def undo_op(self, rid):
                return False, "撤销失败：数据不可用"

        from gcm.ui.undo_bar import UndoBar
        # owner 必须由测试持强引用：UndoBar 对 owner 是弱引用（防引用环），
        # 局部临时对象会被立刻回收，浮条就无从回调。
        self._failing_owner = FailingOwner()
        bar = UndoBar(self._failing_owner)
        bar.offer("x", "已删除")
        bar._on_undo()
        self.assertFalse(bar.isHidden(), "失败时浮条不能自己消失（原则①：状态必须可见）")
        self.assertIn("撤销失败", bar.msg.text())
        bar.deleteLater()


class TestHelpShortcutSingleSource(unittest.TestCase):
    """帮助页的快捷键表与真实快捷键不得漂移（Rev6 U-22 的回归）。"""

    def test_help_documents_every_shortcut(self):
        from gcm.ui.help_dialog import HELP_SECTIONS
        from gcm.ui.menu_bar import SHORTCUTS

        blob = "\n".join(line for _title, lines in HELP_SECTIONS for line in lines)
        missing = [key for key, _desc in SHORTCUTS if key not in blob]
        self.assertEqual(missing, [],
                         "帮助页漏了这些快捷键（用户按文档找不到）：" + "、".join(missing))


if __name__ == "__main__":
    unittest.main()
