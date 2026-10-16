"""G67-15 文档新鲜度守卫：`docs/` 与 `README.md` 的版本号、路径引用不得漂移。

**为什么需要这个文件**（真实教训，不是假想）：
`docs/` 下多份工程文档长期落后于代码 —— `architecture.md` / `product-brief.md` /
`code-gems.md` / `perf-observability.md` 标题停在 `v9.0.0`，`SOP-交付与维护.md` 停在
`v8.2.0`，`慢测定位.md` 把**已落地**的提速建议仍写成「待办」，
`RELEASE_BODY.md` / `README.md` 还写着本地 HTTP API 支持 `::1`（G67-5 已移除）。
这类漂移**不会让任何测试变红**，只能靠守卫；而 G67-7 已经证明「守卫只覆盖一侧」
（只守卫代码生产者、漏掉契约文档）会让版本悄悄漂移且全绿。

**守卫口径（两条，都可被反例证伪）**：
1. 版本号：文档中出现的 `vX.Y.Z`（以及标题行 / 「当前版本」行里的裸 `X.Y.Z`）必须等于
   `gcm.__version__`。历史语境（ADR 的引入版本、验证台账、审计快照）走**显式白名单**，
   每条白名单都带理由注释；白名单里出现过期条目由 `test_exemptions_are_justified` 拦住。
2. 路径引用：文档里提到的仓库内路径必须真实存在 —— 引用了已删除模块（如本轮的
   `gcm/app/rate_limit.py`）或写错文件名即红灯。

反向自检见 `test_guard_catches_version_drift_on_real_doc` /
`test_guard_catches_deleted_path_on_real_doc`：把真实文档在内存里改坏，断言扫描函数会报警。
"""
from __future__ import annotations

import fnmatch
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- 扫描范围
# G67-24：把仓库根与 skills/ 也纳入路径扫描 —— 只扫 docs/ 会漏掉
# README 的「目录结构」表与 skills 技能包里的死链（本轮删 计划书/ 时全靠人肉 grep）。
SCAN_GLOBS = ("docs/**/*.md", "README.md", "CLAUDE.md", "skills/**/*.md")


def iter_scan_files() -> list[pathlib.Path]:
    """待扫描文档（docs/ 下全部 .md + 仓库根 README.md）。"""
    out: list[pathlib.Path] = []
    for pattern in SCAN_GLOBS:
        out.extend(sorted(ROOT.glob(pattern)))
    return [p for p in out if p.is_file()]


# --------------------------------------------------------------------------- 版本号规则
# 与 tests/test_g66_patch.py 同一套「含 semver 形态」判定：前后不得紧邻数字或点，
# 因此 `127.0.0.1` / `8.0.2.1` / `2026-09-26` 都不会被误当成版本号。
_SEMVER = re.compile(r"(?<![\d.])(v?)(\d+\.\d+\.\d+)(?![\d.])")

# 这些行是「当前版本声明」：行内出现裸 semver（无 v 前缀）也必须等于当前版本。
_CURRENT_CLAIM_WORDS = ("当前版本", "适用版本", "最新版本", "本版版本")

# 整文件豁免：内容按定义就是历史快照（每次新增同类文件请在此登记理由）。
_HISTORICAL_GLOBS: tuple[tuple[str, str], ...] = (
    ("docs/adr/*.md", "ADR 的「状态」行记录的是该决策**引入时的**版本，天然是历史值"),
    ("docs/验证记录-*.md", "按日期+版本分段的验证台账，记录历次实测（含当时覆盖率），不得篡改"),
    ("docs/audit-v*.md", "文件名即「审计时的版本」，是那次审计的快照报告"),
    ("docs/reports/*", "某轮审计/汇报的归档产物（HTML/md 快照）"),
)

# 行内白名单：键 = 仓库相对路径（POSIX 风格），值 = {允许的历史版本: 理由}。
# 只放宽「确实在讲别的版本」的地方；新出现的版本字面量必须先在这里登记理由。
_ALLOWED_HISTORICAL_VERSIONS: dict[str, dict[str, str]] = {
    "README.md": {
        "1.2.0": "`owner/repo@v1.2.0` 是 git 引用语法示例（指定 tag），不是产品版本",
        "4.0.0": "`git tag v4.0.0` 是发布命令示例，逐字来自历史操作",
        "7.6.0": "「v7.6.0（G45-6）起 main_window 已拆分」是沿革说明",
    },
    "docs/data-model.md": {
        "8.0.2": "说明「被删的旧 db_structure.md 停在 v8.0.2」这一事实，是考古语境",
    },
    "CLAUDE.md": {
        # §6「已知边界」登记发布链断档：这是一段版本**区间**事实，
        # 版本号本身就是信息，改写会丢失精度。
        "9.1.2": "登记发布链断档：最新 git tag 停在此版本（考古事实）",
        "9.1.3": "登记发布链断档：未打 tag 的起始版本",
        "9.3.2": "登记发布链断档：未打 tag 的结束版本（G69-18 基线）",
    },
}


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).as_posix()


def is_historical_file(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat, _ in _HISTORICAL_GLOBS)


def current_version() -> str:
    from gcm import __version__
    return __version__


def scan_version_drift(path: pathlib.Path | str, text: str,
                       version: str | None = None) -> list[str]:
    """返回该文档中「版本号不等于当前版本」的问题行（已按白名单/历史豁免过滤）。

    传入 `path` 为 `pathlib.Path`（相对仓库根解析相对路径用于白名单匹配）或已算好的
    POSIX 相对路径字符串。
    """
    version = version or current_version()
    rel = path if isinstance(path, str) else _rel(path)
    if is_historical_file(rel):
        return []
    allowed = _ALLOWED_HISTORICAL_VERSIONS.get(rel, {})
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in _SEMVER.finditer(line):
            has_v, digits = match.group(1) == "v", match.group(2)
            # 裸 semver 只在「当前版本声明」行才要求同步，否则会误伤
            # 「Python 3.14.3」「PyQt6 6.11.0」这类环境版本号。
            if not has_v and not _is_current_claim_line(line):
                continue
            if digits == version:
                continue
            if digits in allowed:  # 行内白名单：历史引用（带不带 v 前缀都算同一条）
                continue
            problems.append(
                f"{rel}:{lineno} 版本 {match.group(0)!r} != gcm.__version__ ({version})"
                + (f"（行内白名单已登记 {sorted(allowed)}，如需再放宽请补理由）"
                   if allowed else "")
                + f"  →  {line.strip()[:90]}")
    return problems


def _is_current_claim_line(line: str) -> bool:
    """标题（H1）或含「当前版本/适用版本」字样的行 = 当前版本声明。"""
    if line.startswith("# "):
        return True
    return any(word in line for word in _CURRENT_CLAIM_WORDS)


# --------------------------------------------------------------------------- 路径引用规则
# 只认「仓库内常见目录前缀 + 已知扩展名」的 token；`dist\...exe`、`data/...` 这类
# 运行期/构建期产物路径不在范围内（干净 clone 下本就不存在）。
_PATH_PREFIXES = ("gcm", "docs", "scripts", "skills", "tests", "packaging", "计划书")
_PATH_EXTENSIONS = (".py", ".md", ".json", ".yml", ".yaml", ".txt", ".iss", ".spec",
                    ".bat", ".ps1", ".html", ".toml", ".cfg", ".ini")
_PATH_TOKEN = re.compile(
    r"(?<![\w./\\-])(" + "|".join(_PATH_PREFIXES) + r")/"
    r"[^\s`\"'*|<>()\[\]{}，。；：、（）【】「」]+")
_TRAILING_JUNK = re.compile(r"[^A-Za-z0-9_/\\.\-]+$")


def scan_missing_paths(path: pathlib.Path | str, text: str,
                       exists=None) -> list[str]:
    """返回文档中「引用了但不存在」的仓库内路径。`exists` 可注入以便反向自检。"""
    rel = path if isinstance(path, str) else _rel(path)
    check = exists or (lambda p: (ROOT / p).exists())
    problems: list[str] = []
    seen: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in _PATH_TOKEN.finditer(line):
            token = _TRAILING_JUNK.sub("", match.group(0))
            if not token.endswith(_PATH_EXTENSIONS):
                continue
            if ".." in token or token in seen:
                continue
            seen.add(token)
            if check(token):
                continue
            problems.append(f"{rel}:{lineno} 引用了不存在的路径 {token!r}")
    return problems


# --------------------------------------------------------------------------- 守卫
class TestDocsVersionFreshness(unittest.TestCase):
    """版本号必须与 gcm.__version__ 同源（历史语境走显式白名单）。"""

    def test_scope_is_not_empty(self):
        """防「范围写错导致守卫空转」：必须真的扫到文档。"""
        files = iter_scan_files()
        self.assertEqual(len(files), len(set(files)), "扫描列表不应有重复")
        self.assertGreaterEqual(len(files), 10,
                                f"docs/ + README 至少应有 10 份文档，实际 {len(files)}")
        self.assertTrue(any(_rel(p) == "README.md" for p in files))

    def test_docs_versions_match_package(self):
        problems: list[str] = []
        for path in iter_scan_files():
            problems += scan_version_drift(path, path.read_text(encoding="utf-8"))
        self.assertEqual(problems, [], "文档版本号与 gcm.__version__ 不一致：\n  "
                                       + "\n  ".join(problems))

    def test_current_version_claim_exists(self):
        """文档里必须真的存在「当前版本声明」，否则本守卫等于没在检查。"""
        version = current_version()
        hits: list[str] = []
        for path in iter_scan_files():
            rel = _rel(path)
            if is_historical_file(rel):
                continue
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                if any(w in line for w in _CURRENT_CLAIM_WORDS) and version in line:
                    hits.append(f"{rel}:{lineno}")
        self.assertTrue(hits, f"未找到任何声明当前版本 {version} 的行；"
                              "README 的「当前版本」行或 docs/agent-contract.md 的"
                              "「适用版本」行被删了？")

    def test_exemptions_are_justified(self):
        """豁免 glob 必须命中真实文件（过期豁免 = 悄悄放宽守卫）。"""
        for pattern, reason in _HISTORICAL_GLOBS:
            with self.subTest(pattern=pattern):
                self.assertTrue(reason.strip(), "豁免必须带理由")
                self.assertTrue(list(ROOT.glob(pattern)),
                                f"豁免 {pattern!r} 未命中任何文件，请删除该条")
        for rel in _ALLOWED_HISTORICAL_VERSIONS:
            with self.subTest(rel=rel):
                self.assertTrue((ROOT / rel).is_file(),
                                f"行内白名单 {rel!r} 指向的文件不存在")


class TestDocsPathReferences(unittest.TestCase):
    """文档引用的仓库内路径必须真实存在（防引用已删除模块 / 写错文件名）。"""

    def test_no_missing_path_references(self):
        problems: list[str] = []
        checked = 0
        for path in iter_scan_files():
            text = path.read_text(encoding="utf-8")
            checked += len(_PATH_TOKEN.findall(text))
            problems += scan_missing_paths(path, text)
        self.assertGreater(checked, 0, "路径扫描为空 —— 正则或范围写错了")
        self.assertEqual(problems, [], "文档引用了不存在的路径：\n  "
                                       + "\n  ".join(problems))

    def test_deleted_rate_limit_module_not_referenced(self):
        """**具体回归**（G67-15 同轮删除 `gcm/app/rate_limit.py`）：不得再有引用。

        这条比通用扫描更直白：删模块的人往往忘记改文档，而文档会继续向读者（和 agent）
        承诺一个不存在的文件。

        口径说明：只匹配**模块路径** `rate_limit.py` / `app/rate_limit`，不匹配
        `settings.rate_limit_kbps`（那是「下载限速」设置项，仍然存在且与已删模块无关）；
        按定义就是历史快照的文档（`docs/验证记录-*.md` 等）跳过 —— 它们记录的是
        「当时改了什么」，那时文件确实存在。
        """
        self.assertFalse((ROOT / "gcm" / "app" / "rate_limit.py").exists(),
                         "本用例的前提是该模块已删除；若它被恢复，请先复核文档口径")
        offenders: list[str] = []
        for path in iter_scan_files():
            rel = _rel(path)
            if is_historical_file(rel):
                continue
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                if "rate_limit.py" in line or "app/rate_limit" in line:
                    offenders.append(f"{rel}:{lineno} {line.strip()[:90]}")
        self.assertEqual(offenders, [], "文档仍在引用已删除的 rate_limit 模块：\n  "
                                        + "\n  ".join(offenders))


class TestGuardIsNotHollow(unittest.TestCase):
    """反向自检：守卫必须能真的抓到漂移，否则「全绿」毫无意义。"""

    def test_guard_catches_version_drift_on_real_doc(self):
        """把真实 README 的版本号在内存里改坏 → 扫描必须报警。"""
        rel, version = "README.md", current_version()
        text = (ROOT / rel).read_text(encoding="utf-8")
        self.assertIn(f"当前版本：v{version}", text)
        drifted = text.replace(f"当前版本：v{version}", "当前版本：v9.0.0")
        self.assertNotEqual(drifted, text, "漂移样例构造失败")
        problems = scan_version_drift(rel, drifted)
        self.assertTrue(problems, "守卫没能抓到 README 的版本漂移（空壳守卫）")
        self.assertTrue(any("README.md" in p and "9.0.0" in p for p in problems),
                        f"报错信息应指出 README 的 9.0.0，实际：{problems}")

    def test_guard_catches_drift_in_historical_version_spelling(self):
        """不带 `v` 前缀、写在文档标题里的过期版本同样要被抓到。"""
        problems = scan_version_drift(
            "docs/architecture.md",
            "# Git-clone-Max 架构资产（9.0.0，2026-09-26）\n\n正文无版本。\n")
        self.assertTrue(problems, "标题里的裸 semver 未被检查")
        self.assertTrue(any("9.0.0" in p for p in problems))

    def test_guard_catches_deleted_path_on_real_doc(self):
        """往真实 README 文本里插入对已删除模块的引用 → 路径扫描必须报警。"""
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        poisoned = text + "\n本工具另见 gcm/app/rate_limit.py 的限流实现。\n"
        problems = scan_missing_paths("README.md", poisoned)
        self.assertTrue(problems, "守卫没能抓到对已删除模块的引用（空壳守卫）")
        self.assertTrue(any("rate_limit.py" in p for p in problems), problems)

    def test_guard_accepts_existing_paths_and_whitelisted_versions(self):
        """不应误伤：存在的路径、白名单里的历史版本、环境版本号。"""
        self.assertEqual(
            scan_missing_paths("README.md", "见 `gcm/util/redact.py` 与 tests/test_core.py。"),
            [])
        self.assertEqual(
            scan_version_drift("README.md",
                               "```bash\ngit tag v4.0.0\n```\n"
                               "`owner/repo@v1.2.0` 指定分支/标签\n"
                               "v7.6.0（G45-6）起 main_window 已拆分\n"),
            [], "README 行内白名单里的历史版本被误报")
        self.assertEqual(
            scan_version_drift("docs/SOP-交付与维护.md",
                               "| Python | ≥3.10（本机 3.14.3） |\n"
                               "| PyQt6 | ≥6.6（本机 6.11.0） |\n"),
            [], "环境版本号（非产品版本）被误报")
        self.assertEqual(
            scan_version_drift("docs/adr/ADR-001-engine-single-writer.md",
                               "> 状态：Accepted（2026-09-18 起，v4.0.0 引入）\n"),
            [], "ADR 的引入版本属历史语境，被误报")
        # 反向：**非**白名单文档里出现的历史版本仍应被抓到（防止白名单被滥用）
        self.assertTrue(
            scan_version_drift("docs/code-gems.md", "# 范例（v9.0.0）\n"),
            "非历史文档的历史版本竟然没被检查")


    def test_not_a_time_bomb(self):
        """本文件必须自证**不是**时间炸弹：不得出现「相对语义 + 绝对日期字面量」。

        本文件在文档字符串里引用了 `docs/验证记录-*.md` 这类带日期的文件名；若哪天
        有人把「距今 N 天」这类相对语义写进同一行，`TestNoTimeBombAssertions`
        （`tests/test_g66_patch.py`，全 tests/ 覆盖）就会红灯 —— 这里提前给出定位提示。
        """
        text = pathlib.Path(__file__).read_text(encoding="utf-8")
        date_literal = re.compile(r"\d{4}-\d{2}-\d{2}")
        relative_hints = ("距今", "超过", "未过期", "stale")
        code_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        offenders = [
            f"line {i}: {ln.strip()[:80]}"
            for i, ln in enumerate(code_lines, 1)
            if date_literal.search(ln) and any(h in ln for h in relative_hints)
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
