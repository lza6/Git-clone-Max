"""S-07：`update_status` 读-改-写必须单临界区（Rev10 W2 P1）。

旧行为：`self.load()`（独立跨进程临界区）→ 修改 → `self._save(data)`（另一
临界区）—— 两临界区之间可被其它线程/进程插入 → 并发更新互相覆盖，
续跑清单条目凭空消失。修法：统一走 `_rmw`（A5-2/C-5 同一范式）。
守卫：多线程并发 update_status 后，全部条目必须仍在（或按终态规则移除）。
"""
import tempfile
import threading
import unittest
from pathlib import Path

from gcm.db.runtime_sessions import RuntimeSessionStore
from gcm.models import RepoSpec


class TestUpdateStatusAtomicRmw(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="s07_")
        self.addCleanup(self._tmp.cleanup)
        self.store = RuntimeSessionStore(Path(self._tmp.name) / "s.json")
        specs = [RepoSpec("o", f"r{i}", f"https://github.com/o/r{i}")
                 for i in range(20)]
        self.store.replace(specs)
        self.keys = [t["key"] for t in self.store.load()["tasks"]]

    def test_concurrent_updates_keep_all_entries(self):
        """20 线程并发标记失败：20 条必须全部仍在。"""
        threads = [threading.Thread(target=self.store.mark_failed, args=(k, "x"))
                   for k in self.keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        tasks = self.store.load()["tasks"]
        self.assertEqual(len(tasks), len(self.keys),
                         "并发 update_status 丢条目 = 读-改-写不在同一临界区")

    def test_terminal_status_removes_exactly_one(self):
        """终态移除语义在并发下仍精确：标记 1 条成功 → 恰好少 1 条。"""
        self.store.update_status(self.keys[0], "success")
        tasks = self.store.load()["tasks"]
        self.assertEqual(len(tasks), len(self.keys) - 1)
        self.assertNotIn(self.keys[0], {t["key"] for t in tasks})

    def test_failure_message_redacted(self):
        self.store.update_status(self.keys[0], "failed",
                                 "https://u:token@github.com/o/r")
        t = next(t for t in self.store.load()["tasks"]
                 if t["key"] == self.keys[0])
        self.assertNotIn("token", t.get("message", ""))


if __name__ == "__main__":
    unittest.main()


class TestRmwGuard(unittest.TestCase):
    """AST 守卫：_rmw 存在的意义就是单临界区；load()+_save() 手拼写法回归即红。"""

    def test_update_status_uses_rmw(self):
        import ast
        src = Path("gcm/db/runtime_sessions.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "update_status":
                calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
                funcs = set()
                for c in calls:
                    f = c.func
                    if isinstance(f, ast.Attribute):
                        funcs.add(f.attr)
                    elif isinstance(f, ast.Name):
                        funcs.add(f.id)
                self.assertIn("_rmw", funcs, "update_status 必须走 _rmw")
                self.assertNotIn("load", funcs,
                                 "update_status 内不得手拼 load()+_save()（S-07 洞）")
                self.assertNotIn("_save", funcs)
                return
        self.fail("找不到 update_status")
