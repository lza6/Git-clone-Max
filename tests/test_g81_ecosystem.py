"""G81 · 技能包发现层与开源治理守卫。

背景（2026-10-31 参考对标审计，全量 1,726 目录实测）：
- 参考库抽样 200 个 SKILL.md，200/200（100%）以 YAML frontmatter（`---`）
  开头且含 `name:` 字段 —— 这是 agent 生态的硬惯例（Claude Code / agent-plugins.org）。
  本项目 6/6 缺失 → agent 无法自动发现/触发技能包（增长方向 A 断链）。
- 参考库普及率（分母 1,726）：`.claude-plugin/plugin.json` 117、
  `llms.txt` 35、CONTRIBUTING 750、CHANGELOG 521、SECURITY 508、
  dependabot 309。本项目此前 9 项全缺。

本守卫冻结两层事实：
1. **frontmatter 层**：每个 skills/*/SKILL.md 必须有 frontmatter 且
   `name` == 目录名、`description` 非空且含触发语（"Use when" / "Use for" /
   "Use before"）—— 触发语是 frontmatter 有别于普通简介的关键。
2. **发现层与治理层**：文件存在 + plugin.json 的 version 必须等于
   `gcm.__version__`（复用版本单源红线，防止第二版本真相）。
   dependabot.yml 必须可解析（缩进错误会让 Dependabot 静默失效）。

变异实验（守卫有效性证据，G81 落地时执行并留档 evidence/）：
- 删任一 SKILL.md 的 frontmatter → 本文件红
- 把 plugin.json version 改成 0.0.1 → 本文件红
- dependabot.yml 缩进破坏 → 本文件红
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

_TRIGGER_PAT = re.compile(r"\bUse (when|for|before|this|after)\b", re.IGNORECASE)


def _spec(url_https: str = "https://github.com/a/b.git"):
    from gcm.models import RepoSpec

    return RepoSpec(owner="a", repo="b", url_https=url_https, folder_name="a__b")


def _skill_dirs() -> list[pathlib.Path]:
    return sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())


def _parse_frontmatter(text: str) -> tuple[dict[str, str] | None, str]:
    """极简 frontmatter 解析：只取顶层 `key: value`（value 可跨行续行）。

    不引第三方 YAML 依赖 —— 守卫只断言结构存在，不做完整 YAML 语义。
    返回 (字段字典 或 None, 错误消息)。
    """
    if not text.startswith("---"):
        return None, "文件不以 --- 开头（缺 frontmatter）"
    end = text.find("\n---", 3)
    if end < 0:
        return None, "frontmatter 未闭合（缺第二个 ---）"
    block = text[3:end]
    fields: dict[str, str] = {}
    key = ""
    for line in block.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            key = m.group(1)
            fields[key] = m.group(2).strip().strip(">").strip()
        elif key and line.startswith((" ", "\t")):
            fields[key] += " " + line.strip()
        else:
            return None, f"frontmatter 含无法解析的行：{line!r}"
    return fields, ""


class TestSkillFrontmatter(unittest.TestCase):
    """P1-A：每个技能包的 SKILL.md 必须可被 agent 自动发现（frontmatter）。"""

    def test_every_skill_has_frontmatter(self) -> None:
        for d in _skill_dirs():
            sk = d / "SKILL.md"
            self.assertTrue(
                sk.exists(), f"技能包 {d.name} 缺 SKILL.md")
            fields, err = _parse_frontmatter(sk.read_text(encoding="utf-8"))
            self.assertIsNotNone(
                fields, f"{sk.relative_to(ROOT)}：{err}")

    def test_frontmatter_name_matches_dir(self) -> None:
        for d in _skill_dirs():
            fields, _ = _parse_frontmatter(
                (d / "SKILL.md").read_text(encoding="utf-8"))
            assert fields is not None  # 上一条已断言
            self.assertEqual(
                fields.get("name"), d.name,
                f"{d.name}/SKILL.md 的 name 字段必须等于目录名"
                f"（got {fields.get('name')!r}），否则 agent 索引时对不上号")

    def test_frontmatter_description_has_trigger(self) -> None:
        for d in _skill_dirs():
            fields, _ = _parse_frontmatter(
                (d / "SKILL.md").read_text(encoding="utf-8"))
            assert fields is not None
            desc = fields.get("description", "")
            self.assertGreaterEqual(
                len(desc), 30,
                f"{d.name}/SKILL.md 的 description 太短（{len(desc)} 字符），"
                "agent 靠它判断何时触发，必须写清场景")
            self.assertRegex(
                desc, _TRIGGER_PAT,
                f"{d.name}/SKILL.md 的 description 缺触发语"
                "（'Use when/for/before …'）—— 参考库惯例是触发式描述")

    def test_frontmatter_triggers_present_and_bilingual(self) -> None:
        """`triggers` 决定中英文用户能不能把口语表述路由到这个包。

        参考库抽样 200 个 SKILL.md 的字段普及率：`name` / `description` 100%、
        `triggers` **50%**（`version` 只有 1%、`install`/`requires` 更低，因此
        那几项刻意不加 —— 不引入参考库里的小众约定，version 还会撞版本单源红线）。

        中文触发词不是锦上添花：本项目是中文优先产品，用户对 agent 说
        「批量下载」「断点续跑」「导出报表」时，若 frontmatter 只有英文
        description，路由就可能命中不到对应技能包。
        """
        cjk = re.compile(r"[\u4e00-\u9fff]")
        block_re = re.compile(r"^triggers:\s*$\n((?:\s+-\s*.*\n)+)", re.M)
        item_re = re.compile(r'-\s*"?([^"\n]+)"?')
        for d in _skill_dirs():
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            m = block_re.search(text)
            self.assertIsNotNone(
                m, f"{d.name}/SKILL.md 缺 triggers 块（50% 的同类项目都有）")
            assert m is not None
            items = [w.strip() for w in item_re.findall(m.group(1))]
            self.assertGreaterEqual(
                len(items), 4,
                f"{d.name} 的 triggers 只有 {len(items)} 个，至少 4 个")
            zh = [w for w in items if cjk.search(w)]
            self.assertGreaterEqual(
                len(zh), 2,
                f"{d.name} 的 triggers 必须含 ≥2 个中文词（当前 {zh}），"
                "否则中文用户的口语表述路由不到这个包")

    def test_skill_count_unchanged(self) -> None:
        """技能包数量冻结在 6 —— 新增/删除技能包时更新本常量并补对应验收。"""
        self.assertEqual(len(_skill_dirs()), 6)


class TestDiscoveryLayer(unittest.TestCase):
    """P1-B：发现层文件存在且不破版本单源。"""

    def test_plugin_json_exists_and_version_single_source(self) -> None:
        from gcm import __version__

        pj = ROOT / ".claude-plugin" / "plugin.json"
        self.assertTrue(pj.exists(), "缺 .claude-plugin/plugin.json（发现层入口）")
        text = pj.read_text(encoding="utf-8")
        m = re.search(r'"version"\s*:\s*"([^"]+)"', text)
        self.assertIsNotNone(m, "plugin.json 缺 version 字段")
        self.assertEqual(
            m.group(1), __version__,
            "plugin.json 的 version 必须等于 gcm.__version__"
            "（版本单源红线的成员之一，写死=第二真相）")

    def test_llms_txt_exists_and_references_contract(self) -> None:
        t = (ROOT / "llms.txt").read_text(encoding="utf-8")
        self.assertIn("docs/agent-contract.md", t,
                      "llms.txt 必须指向 agent 契约（面向 LLM 的入口职责）")


class TestGovernanceFiles(unittest.TestCase):
    """P1-C：开源治理文件存在性（参考库普及率 508–750 / 1,726）。"""

    _FILES = (
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        ".github/dependabot.yml",
    )

    def test_governance_files_exist(self) -> None:
        for rel in self._FILES:
            self.assertTrue(
                (ROOT / rel).exists(), f"缺治理文件 {rel}")

    def test_changelog_md_is_pointer_not_copy(self) -> None:
        """CHANGELOG.md 必须是**指针**而非复制 —— 复制正文会与
        gcm.__changelog__ 形成第二真相（版本单源红线）。"""
        t = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("__changelog__", t,
                      "CHANGELOG.md 必须指向 gcm.__changelog__")
        self.assertLess(
            len(t), 4000,
            "CHANGELOG.md 变大了 —— 检查是否开始复制正文（禁止）")

    def test_dependabot_yaml_parses(self) -> None:
        """dependabot.yml 缩进坏 = Dependabot 静默失效，必须可解析。"""
        text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        try:
            import yaml  # PyYAML 非运行时依赖；缺失则降级为结构断言
        except ImportError:
            self.assertIn("version: 2", text)
            self.assertIn("package-ecosystem:", text)
            return
        data = yaml.safe_load(text)
        self.assertIsInstance(data, dict)
        self.assertEqual(data.get("version"), 2)
        self.assertIsInstance(data.get("updates"), list)
        self.assertGreaterEqual(len(data["updates"]), 2)


class TestMirrorDegradeExplainability(unittest.TestCase):
    """P2-A（N-5）：`SyncResult.mirror_used` / `degrade_path` 可解释性字段。

    背景：用户问"为什么这次慢 / 为什么历史不完整"时，答案只存在于日志里。
    这两个字段把答案搬到结果行（事件 / CLI --json / UI 同源）。
    加性字段（只加不减），老消费者按多余键忽略。
    """

    def _svc(self, tmp: pathlib.Path, **kw):
        from gcm.git.service import GitService

        svc = GitService(tmp, **kw)
        svc._current_host = "github.com"
        svc._emit = lambda *a, **k: None
        svc._global_git_auth_configured = lambda: False
        return svc

    def _spec(self):
        return _spec()

    def test_fields_exist_and_default_empty(self) -> None:
        from gcm.models import SyncResult, SyncStatus

        r = SyncResult(spec=self._spec(), status=SyncStatus.RUNNING)
        self.assertEqual(r.mirror_used, "")
        self.assertEqual(r.degrade_path, "")

    def test_clone_via_mirror_records_mirror_used(self) -> None:
        import tempfile
        from unittest import mock

        from gcm.models import SyncResult, SyncStatus

        d = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g81_mu_"))
        svc = self._svc(d, mirror_prefix={"github.com": ["https://m"]},
                        mirror_use_presets=False)
        spec = self._spec()
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)
        with mock.patch("gcm.git.service.probe_git_url",
                        return_value=(True, 10, "")), \
                mock.patch("gcm.git.service.run_git_ui",
                           return_value=(0, None)), \
                mock.patch.object(svc, "_head_sha", return_value="abc"), \
                mock.patch.object(svc, "_safe_rmtree_partial",
                                  return_value=None), \
                mock.patch.object(svc, "_set_origin_url", return_value=True), \
                mock.patch.object(svc, "_remember_mirror", return_value=None):
            svc._clone(spec, res)
        self.assertEqual(res.mirror_used, "https://m",
                         "镜像克隆成功后 origin 会被改回源站（G69-21），"
                         "因此必须在取数当时记录 mirror_used")
        self.assertEqual(res.degrade_path, "")

    def test_clone_fallback_to_source_clears_mirror_used(self) -> None:
        import tempfile
        from unittest import mock

        from gcm.models import SyncResult, SyncStatus

        d = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g81_fb_"))
        svc = self._svc(d, mirror_prefix={"github.com": ["https://m"]},
                        mirror_use_presets=False, mirror_fallback=True)
        spec = self._spec()
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)

        def fake_run(cmd, cwd, on_line, **kw):
            return (0 if cmd[-2] == spec.url_https else 1), None

        with mock.patch("gcm.git.service.probe_git_url",
                        return_value=(True, 20, "")), \
                mock.patch("gcm.git.service.run_git_ui", side_effect=fake_run), \
                mock.patch.object(svc, "_head_sha", return_value="abc"), \
                mock.patch.object(svc, "_safe_rmtree_partial",
                                  return_value=None), \
                mock.patch.object(svc, "_set_origin_url", return_value=True), \
                mock.patch.object(svc, "_remember_mirror", return_value=None):
            svc._clone(spec, res)
        self.assertEqual(res.mirror_used, "",
                         "镜像失败回退源站成功时，最终通道是源站 —— "
                         "mirror_used 必须为空（不谎报镜像）")

    def test_treeless_degrade_records_path(self) -> None:
        import tempfile
        from unittest import mock

        from gcm.models import SyncResult, SyncStatus

        d = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g81_dl_"))
        svc = self._svc(d)  # 无镜像：纯弱网降级场景
        spec = self._spec()
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)

        def fake_run(cmd, cwd, on_line, **kw):
            # 常规克隆失败；treeless 成功
            return (0 if "--filter=blob:none" in cmd else 1), None

        with mock.patch("gcm.git.service.probe_git_url",
                        return_value=(True, 10, "")), \
                mock.patch("gcm.git.service.run_git_ui", side_effect=fake_run), \
                mock.patch.object(svc, "_head_sha", return_value="abc"), \
                mock.patch.object(svc, "_safe_rmtree_partial",
                                  return_value=None), \
                mock.patch("gcm.git.service._is_networkish_error",
                           return_value=True):
            svc._clone(spec, res)
        self.assertEqual(res.degrade_path, "treeless",
                         "弱网 treeless 降级成功必须在结果行留痕")
        self.assertEqual(res.mirror_used, "")

    def test_engine_event_carries_new_fields(self) -> None:
        """事件负载必须携带 mirror_used / degrade_path（SSE/CLI 同源）。"""
        import inspect

        from gcm.app.engine import SyncEngine

        src = inspect.getsource(SyncEngine._on_result)
        self.assertIn("mirror_used", src,
                      "engine 的事件负载必须透传 mirror_used")
        self.assertIn("degrade_path", src,
                      "engine 的事件负载必须透传 degrade_path")

    def test_cli_result_dict_carries_new_fields(self) -> None:
        from gcm.cli import _result_dict
        from gcm.models import SyncResult, SyncStatus

        spec = _spec(url_https="https://user:pass@github.com/a/b.git")
        r = SyncResult(spec=spec, status=SyncStatus.SUCCESS)
        r.mirror_used = "https://user:secret@m.example"
        r.degrade_path = "depth1"
        d = _result_dict(r)
        self.assertEqual(d["mirror_used"], "https://***@m.example",
                         "mirror_used 里的镜像凭据必须被 redact 打码")
        self.assertNotIn("secret", d["mirror_used"])
        self.assertEqual(d["degrade_path"], "depth1")

    def test_additive_only_contract(self) -> None:
        """契约只加不减：既有 7 个键一个都不能少（老消费者不破）。"""
        from gcm.cli import _result_dict
        from gcm.models import SyncResult, SyncStatus

        spec = self._spec()
        r = SyncResult(spec=spec, status=SyncStatus.SUCCESS)
        d = _result_dict(r)
        for k in ("url", "status", "action", "path", "message", "code"):
            self.assertIn(k, d, f"契约键 {k} 不得移除（只加不减）")


if __name__ == "__main__":
    unittest.main()
