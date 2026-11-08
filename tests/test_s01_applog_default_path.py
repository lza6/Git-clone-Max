"""S-01：生产环境 app.log 必须真实生成（Rev10 W2）。

背景：全库 28 处 `applog.info/warn/error(...)` 都不传路径，而 handler 挂载权
此前交给「调用方传路径」→ 生产入口从不传 → app.log 从未生成，28 处留痕
全部丢进黑洞（Rev10 part5 审计独立复现）。

修法（decisions D-39 同一映射只留一份）：不挨个补 28 个参数，而是加
`set_default_log_path`（进程级默认），生产入口 `__main__` 启动第一件事设置它。
"""
import logging
import tempfile
import unittest
from pathlib import Path

from gcm.app import applog


class TestDefaultLogPath(unittest.TestCase):
    def setUp(self):
        applog.reset_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(applog.reset_for_tests)

    def test_default_path_makes_info_write_to_file(self):
        """核心验收：入口设了默认路径后，无参 info() 必须真实落盘。"""
        log_file = Path(self._tmp.name) / "app.log"
        applog.set_default_log_path(log_file)
        applog.info("probe-s01")
        lg = applog.get_logger()
        handlers = [h for h in lg.handlers if isinstance(h, logging.Handler)]
        self.assertTrue(handlers, "设了默认路径后 handlers 必须非空（此前恒为空）")
        # handler 关闭后再读文件，确保内容真的写进去
        applog.reset_for_tests()
        self.assertIn("probe-s01", log_file.read_text(encoding="utf-8"))

    def test_explicit_path_wins_over_default(self):
        explicit = Path(self._tmp.name) / "explicit.log"
        default = Path(self._tmp.name) / "default.log"
        applog.set_default_log_path(default)
        applog.info("via-explicit", log_path=explicit)
        applog.reset_for_tests()
        self.assertIn("via-explicit", explicit.read_text(encoding="utf-8"))
        self.assertFalse(default.exists(), "显式路径优先，默认文件不应被创建")

    def test_set_none_disables_default(self):
        applog.set_default_log_path(Path(self._tmp.name) / "a.log")
        applog.set_default_log_path(None)
        applog.info("no-file")
        self.assertEqual(
            [h for h in applog.get_logger().handlers if isinstance(h, logging.Handler)],
            [], "set_default_log_path(None) 应恢复不落盘")

    def test_idempotent_same_path(self):
        log_file = Path(self._tmp.name) / "same.log"
        applog.set_default_log_path(log_file)
        applog.info("one")
        applog.info("two")
        n = sum(1 for h in applog.get_logger().handlers
                if isinstance(h, logging.FileHandler))
        self.assertEqual(n, 1, "重复设置同一路径不得挂第二个 handler")

    def test_main_entry_wires_default_path(self):
        """生产入口必须调用 set_default_log_path —— 守卫防「实现完但没接线」复发。"""
        import ast
        src = Path("gcm/__main__.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        names = set()
        for c in calls:
            f = c.func
            if isinstance(f, ast.Attribute) and f.attr == "set_default_log_path":
                names.add(f.attr)
            elif isinstance(f, ast.Name) and f.id == "set_default_log_path":
                names.add(f.id)
        self.assertIn("set_default_log_path", names,
                      "__main__ 启动链路上必须有 set_default_log_path 调用点")


if __name__ == "__main__":
    unittest.main()
