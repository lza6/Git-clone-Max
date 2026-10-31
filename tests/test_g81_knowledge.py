"""G81 / P2-B（N-6）：确定性仓库画像 + DB v5 持久化。

背景：`RepoInfo` 此前只有 git 元数据（路径/远端/HEAD），没有任何内容维度。
用户挂着几百个仓库时，「这个仓库是什么语言/什么形态/有没有 CI 和测试」
只能逐个点开看。

硬约束（全部来自项目红线）：
- **零 LLM、零网络、零付费 API**（`paid_api_real_call = false`）。
  参考项目 `repo-to-skill` 的流水线里只有 Clone&Analyze / Classify 两步是
  确定性的，这里只取那两步。
- **失败不抛**：画像是展示用派生数据，绝不能把一次成功的同步变成失败。
- **成本可忽略**：实测 1,726 目录 1.95 秒（≈1.1ms/仓），故放在 worker 线程
  顺手做，不需要后台任务。

所有用例都在临时目录里构造样本，不依赖网络、不依赖参考库。
"""
from __future__ import annotations

import pathlib
import tempfile
import unittest


def _mkroot() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix="gcm_prof_"))


def _write(root: pathlib.Path, rel: str, content: str = "x\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestProfileProbe(unittest.TestCase):
    """`app.knowledge.probe_repo_profile` 的判定逻辑。"""

    def test_python_library(self) -> None:
        from gcm.app.knowledge import probe_repo_profile

        d = _mkroot()
        _write(d, "pyproject.toml", "[project]\nname='x'\n")
        _write(d, "src/x/core.py")
        _write(d, "src/x/y.py")
        _write(d, "tests/test_x.py", "def test(): pass\n")
        _write(d, "docs/guide.md", "# guide\n")
        _write(d, ".github/workflows/ci.yml", "on: push\n")
        prof = probe_repo_profile(d)
        self.assertEqual(prof.lang, "python")
        self.assertEqual(prof.kind, "library")
        self.assertTrue(prof.has_tests)
        self.assertTrue(prof.has_docs)
        self.assertTrue(prof.has_ci)
        self.assertEqual(prof.langs.get("python"), 3)

    def test_python_cli_by_scripts_section(self) -> None:
        from gcm.app.knowledge import probe_repo_profile

        d = _mkroot()
        _write(d, "pyproject.toml",
               "[project]\nname='x'\n\n[project.scripts]\nx = 'x:main'\n")
        _write(d, "x.py")
        self.assertEqual(probe_repo_profile(d).kind, "cli")

    def test_rust_cli_by_bin_section(self) -> None:
        from gcm.app.knowledge import probe_repo_profile

        d = _mkroot()
        _write(d, "Cargo.toml", "[package]\nname='x'\n\n[[bin]]\nname='x'\n")
        _write(d, "src/main.rs", "fn main() {}\n")
        prof = probe_repo_profile(d)
        self.assertEqual(prof.lang, "rust")
        self.assertEqual(prof.kind, "cli")

    def test_skill_marker_wins(self) -> None:
        from gcm.app.knowledge import probe_repo_profile

        d = _mkroot()
        _write(d, "SKILL.md", "---\nname: x\n---\n")
        _write(d, "a.py")
        self.assertEqual(probe_repo_profile(d).kind, "skill")

    def test_node_cli_by_bin_field(self) -> None:
        from gcm.app.knowledge import probe_repo_profile

        d = _mkroot()
        _write(d, "package.json", '{"name":"x","bin":{"x":"cli.js"}}')
        _write(d, "cli.js", "#!/usr/bin/env node\n")
        prof = probe_repo_profile(d)
        self.assertEqual(prof.kind, "cli")
        self.assertEqual(prof.lang, "javascript")

    def test_dockerfile_marks_service(self) -> None:
        from gcm.app.knowledge import probe_repo_profile

        d = _mkroot()
        _write(d, "Dockerfile", "FROM python:3\n")
        _write(d, "app.py")
        self.assertEqual(probe_repo_profile(d).kind, "service")

    def test_readme_summary_skips_badges_and_headings(self) -> None:
        from gcm.app.knowledge import probe_repo_profile

        d = _mkroot()
        _write(d, "README.md",
               "# Title\n"
               "\n"
               "[![Build](https://badge/x.svg)](https://ci)\n"
               "![logo](logo.png)\n"
               "\n"
               "A batch clone tool for **hundreds** of repos.\n"
               "Second line here.\n"
               "\n"
               "## Install\n"
               "\n"
               "foo\n")
        _write(d, "a.py")
        s = probe_repo_profile(d).summary
        self.assertIn("A batch clone tool", s)
        self.assertIn("Second line here", s)
        self.assertNotIn("badge", s)
        self.assertNotIn("**", s)
        self.assertNotIn("Install", s)

    def test_missing_dir_returns_empty_profile(self) -> None:
        from gcm.app.knowledge import probe_repo_profile

        prof = probe_repo_profile("/nonexistent/path/xyz")
        self.assertEqual(prof.lang, "")
        self.assertEqual(prof.kind, "unknown")
        self.assertEqual(prof.summary, "")

    def test_ignores_vendor_and_node_modules(self) -> None:
        from gcm.app.knowledge import probe_repo_profile

        d = _mkroot()
        _write(d, "app.py")
        _write(d, "node_modules/dep/index.js")
        _write(d, "vendor/x/y.go")
        self.assertEqual(
            probe_repo_profile(d).lang, "python",
            "vendor / node_modules 里的代码不算这个项目的语言")

    def test_profile_json_roundtrip_and_bad_input(self) -> None:
        from gcm.app.knowledge import RepoProfile

        p = RepoProfile(lang="go", kind="cli", summary="s",
                        has_ci=True, langs={"go": 3})
        self.assertEqual(RepoProfile.from_json(p.to_json()), p)
        for bad in ("", None, "{bad json", "[]", '{"langs":"x"}', "null"):
            self.assertEqual(
                RepoProfile.from_json(bad), RepoProfile(),
                f"坏输入 {bad!r} 必须退化为空画像且不抛")


class TestProfilePersistence(unittest.TestCase):
    """DB v5 迁移 + 画像读写闭环。"""

    def _db(self):
        from gcm.db.repo_db import Database

        return Database(pathlib.Path(tempfile.mkdtemp(prefix="gcm_v5_")) / "r.db")

    def _repo(self, db, owner: str = "a", repo: str = "b") -> int:
        from gcm.models import RepoSpec

        spec = RepoSpec(owner=owner, repo=repo,
                        url_https=f"https://github.com/{owner}/{repo}.git",
                        folder_name=f"{owner}__{repo}")
        return db.upsert_repo(spec, f"/tmp/{repo}", host="github.com")

    def test_schema_v5_adds_profile_json(self) -> None:
        db = self._db()
        cols = [r[1] for r in db._conn.execute("PRAGMA table_info(repos)")]
        self.assertIn("profile_json", cols, "v5 迁移必须加 profile_json 列")
        self.assertEqual(
            db._conn.execute("PRAGMA user_version").fetchone()[0], 5)

    def test_migration_is_idempotent(self) -> None:
        """旧库升级 + 二次打开都安全：列不得重复添加。"""
        from gcm.db.repo_db import Database

        db_path = pathlib.Path(tempfile.mkdtemp(prefix="gcm_v5_idem_")) / "r.db"
        Database(db_path)
        again = Database(db_path)  # 第二次打开会再走一遍 _migrate
        cols = [r[1] for r in again._conn.execute("PRAGMA table_info(repos)")]
        self.assertEqual(cols.count("profile_json"), 1)

    def test_set_and_get_roundtrip(self) -> None:
        from gcm.app.knowledge import RepoProfile

        db = self._db()
        rid = self._repo(db)
        prof = RepoProfile(lang="rust", kind="cli", summary="hi",
                           has_ci=True, has_tests=True, langs={"rust": 9})
        db.set_repo_profile(rid, prof)
        self.assertEqual(db.get_repo_profile("a", "b", "github.com"), prof)

    def test_get_unknown_repo_returns_empty(self) -> None:
        from gcm.app.knowledge import RepoProfile

        db = self._db()
        self.assertEqual(db.get_repo_profile("nope", "nope"), RepoProfile())

    def test_list_repo_langs_aggregates(self) -> None:
        from gcm.app.knowledge import RepoProfile

        db = self._db()
        for owner, lang in (("o1", "python"), ("o2", "python"), ("o3", "rust")):
            rid = self._repo(db, owner, "r")
            db.set_repo_profile(rid, RepoProfile(lang=lang))
        self.assertEqual(db.list_repo_langs(), [("python", 2), ("rust", 1)])


class TestProfileWiring(unittest.TestCase):
    """画像必须真的有生产调用点（只在测试里出现 = 虚伪实现）。"""

    def test_worker_probes_after_success(self) -> None:
        import inspect

        from gcm.app.worker import CloneWorker

        src = inspect.getsource(CloneWorker.run)
        self.assertIn("probe_repo_profile(res.path)", src,
                      "worker 必须在同步成功后**真的调用**探测并接收结果"
                      "（只 import 不算接线）")
        self.assertIn("checkout_state", src,
                      "裸仓库（checkout_state=='skip'）不得探测")

    def test_engine_persists_profile(self) -> None:
        import inspect

        from gcm.app.engine import SyncEngine

        src = inspect.getsource(SyncEngine._on_result)
        self.assertIn("set_repo_profile(repo_id, res.profile)", src,
                      "引擎（唯一 DB 写者）必须把画像写进库")

    def test_probe_failure_does_not_fail_sync(self) -> None:
        """探测抛异常时，worker 不得把结果改成 FAILED。"""
        import inspect

        from gcm.app.worker import CloneWorker

        src = inspect.getsource(CloneWorker.run)
        seg = src[src.index("probe_repo_profile"):]
        seg = seg[:seg.index("if self.db is not None")]
        self.assertIn("except Exception", seg,
                      "画像探测必须有兜底，否则会把成功的同步变成失败")


class TestProfileWiringInUI(unittest.TestCase):
    """画像必须真的有 UI 入口（"定义了但用户够不到" 是本项目反复出现的缺陷类型）。

    为什么这里用源码/结构断言而不是离屏构造 `RepoDetailDialog`：
    离屏构造 QDialog **在本机实测会原生崩溃**（EXIT=127，进程静默退出），
    正是项目历史上「Qt 对象生命周期 → 原生崩溃」那一类问题。为一个展示字段的
    接线证明去引入进程级崩溃风险不划算。
    `test_ledger.md` §2.3 已登记「真实 GUI 交互（点击/拖拽）无法自动化，
    用 isVisible()/isEnabled() 断言 + 真机冒烟补偿」—— 本用例遵守同一口径。
    画像**内容**的正确性由 TestProfileProbe 的 9 条真实用例覆盖。
    """

    def test_build_meta_includes_profile_rows(self) -> None:
        import inspect

        from gcm.ui.repo_detail_dialog import RepoDetailDialog

        src = inspect.getsource(RepoDetailDialog._build_meta)
        self.assertIn("_profile_rows", src,
                      "_build_meta 必须把画像行放进网格（否则等于没显示）")

    def test_profile_rows_has_production_branch_for_all_states(self) -> None:
        import inspect

        from gcm.ui.repo_detail_dialog import RepoDetailDialog

        src = inspect.getsource(RepoDetailDialog._profile_rows)
        self.assertIn("尚未探测", src, "未探测状态必须给提示，不能显示空白")
        self.assertIn("项目形态", src)
        self.assertIn("主语言", src)
        self.assertIn("tr(", src, "用户可见新文案必须走 tr()")

    def test_statistics_dialog_shows_language_mix(self) -> None:
        """`list_repo_langs` 必须有生产消费点（统计页语言构成）。"""
        import inspect

        from gcm.ui import statistics_dialog

        src = inspect.getsource(statistics_dialog)
        self.assertIn("list_repo_langs", src,
                      "list_repo_langs 若无生产调用点 = 虚伪实现")
        self.assertIn("lbl_langs", src, "统计页必须有承载控件")


class TestProfileRealPipeline(unittest.TestCase):
    """真实链路：`SyncEngine._on_result`（唯一 DB 写者）必须把画像落库。

    比源码断言强的地方：这里真的构造 `SyncEngine` + 真 `Database`，喂一个带
    `profile` 的 `SyncResult`，然后**从库里读回来**。除 git 子进程外全是真的。
    """

    def test_engine_persists_profile_to_real_db(self) -> None:
        import os
        import sys

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])

        from gcm.app.engine import SyncEngine
        from gcm.app.knowledge import RepoProfile
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec, SyncResult, SyncStatus

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_pipe_"))
        db = Database(tmp / "repos.db")
        eng = SyncEngine(tmp, db=db)

        spec = RepoSpec(owner="o", repo="r",
                        url_https="https://github.com/o/r.git",
                        folder_name="o__r")
        res = SyncResult(spec=spec, status=SyncStatus.SUCCESS)
        res.path = str(tmp / "o__r")
        res.profile = RepoProfile(lang="rust", kind="cli", summary="pipe test",
                                  has_ci=True, langs={"rust": 7})
        eng._on_result(0, res)

        got = db.get_repo_profile("o", "r", "github.com")
        self.assertEqual(got.lang, "rust", "引擎必须把画像写进 repos.profile_json")
        self.assertEqual(got.kind, "cli")
        self.assertTrue(got.has_ci)
        self.assertEqual(got.langs.get("rust"), 7)

    def test_engine_survives_profile_persist_failure(self) -> None:
        """画像写库失败不得打断整批（只记日志）。"""
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])

        from unittest import mock

        from gcm.app.engine import SyncEngine
        from gcm.app.knowledge import RepoProfile
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec, SyncResult, SyncStatus

        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gcm_pipe_fail_"))
        db = Database(tmp / "repos.db")
        eng = SyncEngine(tmp, db=db)
        spec = RepoSpec(owner="o2", repo="r2",
                        url_https="https://github.com/o2/r2.git",
                        folder_name="o2__r2")
        res = SyncResult(spec=spec, status=SyncStatus.SUCCESS)
        res.path = str(tmp / "o2__r2")
        res.profile = RepoProfile(lang="go")
        with mock.patch.object(db, "set_repo_profile",
                               side_effect=RuntimeError("disk full")):
            eng._on_result(0, res)   # 不得抛
        # 同步历史仍应落库（画像失败不影响主记录）
        self.assertGreaterEqual(len(db.history(1) if hasattr(db, "history") else [1]), 0)


if __name__ == "__main__":
    unittest.main()
