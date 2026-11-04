"""G84-7：接线守卫 —— 「写好了但没人调」这一类伪闭环的静态拦截。

**要防的是什么**（本轮实测到的两例都属这一类）：
- `app/event_sink.py`：121 行、测试齐全，但**生产零调用点** → 宣称的「事件落盘」
  在用户机器上从未发生；
- `manage_panel.invalidate_size_cache()`：docstring 承诺「点刷新会清空缓存」，
  但刷新按钮没接 → 管理页「大小」列永远拿旧值/空白。

两者都能通过 `ruff`、`mypy`、`vulture ≥80`（它们**不是**死代码，只是没人调用），
所以需要这条守卫：**模块必须被别人 import，可替换点必须有生产调用方**。

新增合法例外时必须在 `ALLOWED_ORPHANS` 里写明理由（`test_allowlist_has_reasons`
会拦空理由，避免以后靠加白名单把守卫糊过去）。
"""
from __future__ import annotations

import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "gcm"

#: 允许"没人 import"的模块：入口点与契约数据文件。每条必须写清理由。
ALLOWED_ORPHANS: dict[str, str] = {
    "gcm.__main__": "进程入口（python -m gcm），由解释器调用而非被 import",
    "gcm.mcp_server": "MCP stdio 入口（python -m gcm.mcp_server），独立进程",
}


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _iter_modules() -> list[tuple[str, pathlib.Path]]:
    out: list[tuple[str, pathlib.Path]] = []
    for p in sorted(PKG.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        out.append((_module_name(p), p))
    return out


def _imported_names(path: pathlib.Path, mod: str) -> set[str]:
    """解析一个文件里出现的所有「gcm 内部模块」引用（含函数内惰性 import）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    is_pkg = path.name == "__init__.py"
    package = mod if is_pkg else mod.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "gcm" or alias.name.startswith("gcm."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # 相对 import：按层级回溯包名
                base_parts = package.split(".")
                up = node.level - 1
                if up:
                    base_parts = base_parts[:-up] if up <= len(base_parts) else []
                base = ".".join(base_parts)
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            if not (target == "gcm" or target.startswith("gcm.")):
                continue
            found.add(target)
            # `from . import a, b` / `from .pkg import sub` 的符号也可能是子模块
            for alias in node.names:
                cand = f"{target}.{alias.name}"
                found.add(cand)
    return found


def find_orphans(modules: dict[str, pathlib.Path]) -> set[str]:
    """返回「没有被任何其它 gcm 模块引用」的模块名集合（纯函数，便于自检）。"""
    imported: set[str] = set()
    for mod, path in modules.items():
        for name in _imported_names(path, mod):
            if name != mod:
                imported.add(name)
    # 包内任意子模块被引用，等于该包被引用（`from .db import ...` 的场景）
    orphans: set[str] = set()
    for mod in modules:
        if mod in imported:
            continue
        if any(name.startswith(mod + ".") for name in imported):
            continue
        orphans.add(mod)
    return orphans


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


class TestNoOrphanModules(unittest.TestCase):
    def test_every_module_is_imported_by_someone(self):
        modules = dict(_iter_modules())
        self.assertGreater(len(modules), 100, "模块扫描结果异常（路径或 rglob 出错）")
        orphans = find_orphans(modules) - set(ALLOWED_ORPHANS)
        self.assertEqual(
            sorted(orphans), [],
            "这些模块没有被任何生产代码 import（写了没人用 = 伪闭环）：\n  "
            + "\n  ".join(sorted(orphans))
            + "\n要么接线，要么删掉；确属入口点请在 ALLOWED_ORPHANS 登记理由。")

    def test_allowlist_has_no_dead_entries(self):
        modules = set(dict(_iter_modules()))
        dead = sorted(set(ALLOWED_ORPHANS) - modules)
        self.assertEqual(dead, [], f"白名单登记了不存在的模块：{dead}")

    def test_allowlist_has_reasons(self):
        weak = [k for k, v in ALLOWED_ORPHANS.items() if len(v.strip()) < 10]
        self.assertEqual(weak, [], f"这些例外没写清理由：{weak}")

    def test_detector_catches_synthetic_orphan(self):
        """反向自检：喂一个没人 import 的假模块，检测器必须报它（否则守卫是假的）。"""
        fake = dict(_iter_modules())
        fake["gcm.app.__nonexistent_probe__"] = PKG / "app" / "event_sink.py"
        self.assertIn("gcm.app.__nonexistent_probe__", find_orphans(fake))


class TestWiringCallSites(unittest.TestCase):
    """「可替换点必须有生产调用方」的具体断言。"""

    def _grep_gcm(self, pattern: str, exclude: set[str]) -> list[str]:
        hits: list[str] = []
        for p in sorted(PKG.rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if rel in exclude:
                continue
            for i, line in enumerate(_read(p).splitlines(), 1):
                if re.search(pattern, line):
                    hits.append(f"{rel}:{i}")
        return hits

    def test_event_sink_is_wired(self):
        hits = self._grep_gcm(r"\benable_event_sink\b", {"gcm/app/event_sink.py"})
        self.assertTrue(hits, "事件落盘没有任何生产调用点（真伪闭环）")
        self.assertTrue(any("__main__.py" in h for h in hits),
                        f"事件落盘应在入口挂载，实测只在：{hits}")

    def test_size_cache_invalidation_is_wired(self):
        """`invalidate_size_cache` 必须在**刷新按钮链路**上被真正调用。

        它是 `manage_panel` 的内部方法，所以"生产调用点"只能在同一文件内找 ——
        关键是**调用**（`self.invalidate_size_cache()`）而不只是定义。
        """
        text = _read(PKG / "ui" / "manage_panel.py")
        self.assertIn("self.invalidate_size_cache()", text,
                      "invalidate_size_cache 只有定义、没有调用（docstring 的承诺是假的）")
        self.assertIn("_on_refresh_clicked", text)
        self.assertIn("btn_refresh.clicked.connect(self._on_refresh_clicked)", text,
                      "刷新按钮没连到清缓存的处理函数")
        self.assertIn("clear_dir_sizes", text, "库内缓存未一起清（用户点刷新还是旧值）")


class TestEventSinkE2E(unittest.TestCase):
    """端到端：挂载后发布一条事件，磁盘上必须真的出现 jsonl（不是只有接线）。"""

    def setUp(self):
        from gcm.app import event_bus as eb
        from gcm.app import event_sink as es
        self.eb, self.es = eb, es
        es.reset_event_sink_for_tests()
        eb.reset_event_bus()
        self.tmp = pathlib.Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))

    def tearDown(self):
        self.es.reset_event_sink_for_tests()
        self.eb.reset_event_bus()

    def test_publish_lands_on_disk(self):
        sink = self.es.enable_event_sink(self.tmp)
        self.assertIsNotNone(sink, "enable_event_sink 应返回 sink 实例")
        self.eb.get_event_bus().publish("task_result", {"key": "owner/repo",
                                                        "status": "success"})
        sink.close()
        files = sorted((self.tmp / "events").glob("events_*.jsonl"))
        self.assertTrue(files, f"事件没有落盘（{self.tmp} 下无 events_*.jsonl）")
        blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
        self.assertIn("task_result", blob)
        self.assertIn("owner/repo", blob)

    def test_enable_is_idempotent(self):
        first = self.es.enable_event_sink(self.tmp)
        second = self.es.enable_event_sink(self.tmp)
        self.assertIs(first, second, "重复挂载会产生第二个 sink（重复写盘）")

    def test_redaction_happens_before_disk(self):
        """入总线前已打码 —— sink 落盘内容里不能出现 token 明文。"""
        sink = self.es.enable_event_sink(self.tmp)
        self.eb.get_event_bus().publish(
            "task_error", {"url": "https://user:ghp_SECRETTOKEN@github.com/a/b"})
        sink.close()
        blob = "".join(f.read_text(encoding="utf-8")
                       for f in (self.tmp / "events").glob("events_*.jsonl"))
        self.assertNotIn("ghp_SECRETTOKEN", blob)


if __name__ == "__main__":
    unittest.main()
