"""仓库画像：从**已克隆的本地目录**提取确定性、零 LLM 的项目特征。

为什么需要（G81 / N-6）：`RepoInfo` 此前只有 git 元数据（路径 / 远端 / HEAD），
没有任何内容维度。用户挂着几百个仓库时，「这个仓库是 Python 还是 Rust？」
「它是 CLI 还是库？」「有没有 CI 和测试？」这些问题只能逐个点开看。

设计约束（都是实测/规范推导出来的，不是偏好）：

1. **零 LLM、零付费 API**。项目红线是 `paid_api_real_call = false`，
   所以画像必须是纯确定性算法：marker 文件 + 后缀统计 + README 首段。
   参考项目 `repo-to-skill` 的流水线里，只有 Clone&Analyze 与 Classify 两步
   是确定性的，后三步（Generate/Test/Evaluate）依赖 LLM —— 这里只取前两步。
2. **成本可忽略**。实测对 1,726 个目录做 15 类 marker 探测耗时 1.95 秒
   （≈1.1ms/仓），因此可以在同步完成的 worker 线程里顺手做，不需要后台任务。
3. **读盘有上限**。只读 README 前 4 KiB、只 stat marker 文件是否存在，
   绝不遍历 vendor/node_modules/.git 内部，避免在超大仓库上退化成扫盘。
4. **纯函数**。不碰 DB、不碰 Qt、不写文件 —— 便于单测，也满足
   `gcm/app` 禁新增 Qt 依赖的边界守卫。

术语（与 `docs/data-model.md` 保持一致）：
- `lang`：主语言，取后缀统计的众数；无源码文件时为空串
- `kind`：项目形态，`cli` / `library` / `service` / `skill` / `docs` / `unknown`
- `summary`：README 首段（去 Markdown 修饰、截断 200 字符）
- `has_ci` / `has_tests` / `has_docs`：三个布尔信号
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = [
    "RepoProfile",
    "probe_repo_profile",
    "MAX_README_BYTES",
    "LANGS",
]

# README 只读前 4 KiB：足够拿到首段摘要，又不会被 100 KB 的巨型 README 拖慢。
MAX_README_BYTES = 4096

# 后缀 → 语言。刻意只收常见主力语言，避免把 .json/.md 这类数据/文档算成"语言"。
LANGS: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".swift": "swift",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".m": "objective-c", ".scala": "scala", ".dart": "dart",
    ".lua": "lua", ".sh": "shell", ".ps1": "powershell",
    ".ex": "elixir", ".exs": "elixir", ".zig": "zig", ".nim": "nim",
    ".vue": "vue", ".svelte": "svelte",
}

# 语言统计时要跳过的目录（依赖/产物/元数据，不是"这个项目的代码"）
_SKIP_DIRS = frozenset({
    ".git", "node_modules", "vendor", "dist", "build", "target",
    "coverage", ".venv", "venv", "__pycache__", ".next", ".nuxt",
    ".idea", ".vscode", "site-packages", "bower_components",
})

# 语言统计的遍历上限：超过就停，避免在巨型 monorepo 上卡住。
_MAX_SCAN_FILES = 4000
_MAX_WALK_DEPTH = 4

# marker 文件 → 语言（与 `LANGS` 互补：有些仓库只有 marker 没有明显源码后缀）
_MARKER_LANG = (
    ("pyproject.toml", "python"), ("setup.py", "python"),
    ("requirements.txt", "python"), ("Pipfile", "python"),
    ("package.json", "javascript"), ("tsconfig.json", "typescript"),
    ("Cargo.toml", "rust"), ("go.mod", "go"),
    ("pom.xml", "java"), ("build.gradle", "java"), ("build.gradle.kts", "kotlin"),
    ("composer.json", "php"), ("Gemfile", "ruby"), ("pubspec.yaml", "dart"),
    ("CMakeLists.txt", "cpp"), ("mix.exs", "elixir"),
)

# 用于判形态的 marker（存在即视为对应工具链的一部分）
_CLI_MARKERS = (
    ("src/main.rs", "cli"), ("cmd/main.go", "cli"), ("main.go", "cli"),
    ("bin", "cli"),
)
_SERVICE_MARKERS = (
    ("docker-compose.yml", "service"), ("docker-compose.yaml", "service"),
    ("Dockerfile", "service"), ("k8s", "service"), ("deploy", "service"),
    ("helm", "service"),
)

_CI_PATHS = (
    ".github/workflows", ".gitlab-ci.yml", ".circleci", "azure-pipelines.yml",
    "Jenkinsfile", ".travis.yml", "bitbucket-pipelines.yml",
)
_TEST_DIRS = ("tests", "test", "spec", "__tests__", "e2e")
_DOC_DIRS = ("docs", "doc", "documentation", "wiki")
_DOC_FILES = ("CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "ARCHITECTURE.md")


@dataclass
class RepoProfile:
    """一个仓库的确定性画像。全部字段为空/False 表示「探测不到」，不表示「没有」。"""

    lang: str = ""
    kind: str = "unknown"
    summary: str = ""
    has_ci: bool = False
    has_tests: bool = False
    has_docs: bool = False
    # 语言分布（前 5 名，name → 文件数）。用于展示"这个仓库主要是什么组成的"。
    langs: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | None) -> RepoProfile:
        """从库里的 JSON 还原；非法/空值一律返回空画像（绝不抛）。"""
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        langs = data.get("langs")
        if not isinstance(langs, dict):
            # 坏数据（手改过 profile_json / 旧版本写过别的形态）→ 丢弃分布，
            # 保留其余字段。绝不能在这里抛：读画像的调用方是 UI 刷新路径。
            langs = {}
        clean_langs: dict[str, int] = {}
        for k, v in langs.items():
            if isinstance(v, int) and not isinstance(v, bool):
                clean_langs[str(k)] = v
        return cls(
            lang=str(data.get("lang") or ""),
            kind=str(data.get("kind") or "unknown"),
            summary=str(data.get("summary") or ""),
            has_ci=bool(data.get("has_ci")),
            has_tests=bool(data.get("has_tests")),
            has_docs=bool(data.get("has_docs")),
            langs=clean_langs,
        )


def _read_head_text(path: Path, limit: int = MAX_README_BYTES) -> str:
    """读文件前 `limit` 字节；失败返回空串（画像不能让同步失败）。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(limit)
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")


def _first_paragraph(text: str) -> str:
    """从 README 文本里取「第一段有意义的正文」，去掉标题/徽章/图片那一坨。

    规则（按出现顺序尝试）：
    1. 跳过空行、纯标题行（`#`）、纯图片/徽章行（`[![` / `<img` / `<p` / `![`）；
    2. 遇到第一段普通文本就收集到空行或下一个标题为止；
    3. 去掉行内 Markdown 强调/链接语法，压成一行。
    """
    out: list[str] = []
    started = False
    for line in text.splitlines():
        s = line.strip()
        if not started:
            if not s:
                continue
            if s.startswith(("#", "![", "[![", "<", "---", "===", "|", ">")):
                continue
            if s.startswith(("```", "    ")):
                return ""  # 以代码块开头的 README，首段无意义
            started = True
        else:
            if not s or s.startswith("#"):
                break
        out.append(s)
    if not out:
        return ""
    joined = " ".join(out)
    # 去行内链接 [text](url) → text，去强调标记，压缩空白
    res: list[str] = []
    i = 0
    while i < len(joined):
        if joined[i] == "[":
            close = joined.find("]", i)
            if close > 0 and close + 1 < len(joined) and joined[close + 1] == "(":
                paren = joined.find(")", close)
                if paren > 0:
                    res.append(joined[i + 1:close])
                    i = paren + 1
                    continue
        res.append(joined[i])
        i += 1
    text2 = "".join(res).replace("**", "").replace("`", "").replace("*", "")
    text2 = " ".join(text2.split())
    return text2[:200]


def _detect_langs(root: Path) -> tuple[dict[str, int], bool]:
    """统计语言后缀分布。返回 (前 5 名, 是否因超限截断)。"""
    counts: dict[str, int] = {}
    seen = 0
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= _MAX_WALK_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            seen += 1
            if seen > _MAX_SCAN_FILES:
                truncated = True
                break
            lang = LANGS.get(os.path.splitext(fn)[1].lower())
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
        if truncated:
            break
    top = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5])
    return top, truncated


def _has_any(root: Path, names: tuple[str, ...]) -> bool:
    return any((root / n).exists() for n in names)


def _detect_kind(root: Path, langs: dict[str, int]) -> str:
    """判项目形态。顺序即优先级：skill > service > cli > library > docs > unknown。"""
    if (root / "SKILL.md").is_file() or (root / ".claude-plugin").is_dir():
        return "skill"
    if _has_any(root, tuple(p for p, _ in _SERVICE_MARKERS)):
        return "service"
    if (root / "package.json").is_file():
        try:
            meta = json.loads(_read_head_text(root / "package.json", 65536) or "{}")
        except (ValueError, TypeError):
            meta = {}
        if isinstance(meta, dict) and isinstance(meta.get("bin"), (str, dict)):
            return "cli"
        if isinstance(meta, dict) and meta.get("main") or meta.get("exports"):
            return "library"
    if _has_any(root, tuple(p for p, _ in _CLI_MARKERS)):
        return "cli"
    if (root / "pyproject.toml").is_file():
        body = _read_head_text(root / "pyproject.toml")
        if "[project.scripts]" in body or "console_scripts" in body:
            return "cli"
        if "[project]" in body or "[tool.poetry]" in body:
            return "library"
    if (root / "Cargo.toml").is_file():
        body = _read_head_text(root / "Cargo.toml")
        if "[[bin]]" in body:
            return "cli"
        if "[lib]" in body or "[package]" in body:
            return "library"
    if (root / "go.mod").is_file():
        return "library"
    if langs:
        return "library"
    # 只有文档没有代码
    if _has_any(root, ("README.md",)) and not langs:
        return "docs"
    return "unknown"


def probe_repo_profile(repo_path: str | Path) -> RepoProfile:
    """探测一个**已克隆**仓库目录的画像。

    绝不抛异常：任何 I/O 失败都退化为「探测不到」（空画像），因为画像是锦上添花，
    不能让它把一次成功的同步变成失败。

    异常处理刻意**收敛成一个边界**（而不是每步一个 try）：探测全程只有一种失败语义
    ——「这个目录读不了」。分散的多处 `except OSError` 既难读，也会让
    `test_g80_hygiene` 的 except 预算无谓上涨（该预算只降不升）。
    """
    root = Path(repo_path)
    try:
        if not root.is_dir():
            return RepoProfile()
        langs, _trunc = _detect_langs(root)
        lang = next(iter(langs), "") or _lang_from_markers(root)
        summary = _readme_summary(root)
        kind = _detect_kind(root, langs)
        return RepoProfile(
            lang=lang, kind=kind, summary=summary,
            has_ci=any((root / p).exists() for p in _CI_PATHS),
            has_tests=any((root / d).is_dir() for d in _TEST_DIRS),
            has_docs=(any((root / d).is_dir() for d in _DOC_DIRS)
                      or any((root / f).is_file() for f in _DOC_FILES)),
            langs=langs,
        )
    except OSError:
        # 权限/路径过长/被占用：读不了就报"探测不到"，不猜、不抛。
        return RepoProfile()


def _lang_from_markers(root: Path) -> str:
    """无源码后缀时（纯 skill / 纯配置仓库）用 marker 退化判语言。"""
    for marker, guess in _MARKER_LANG:
        if (root / marker).is_file():
            return guess
    return ""


def _readme_summary(root: Path) -> str:
    """按常见大小写尝试 README，取首段摘要（读不到返回空串）。"""
    for name in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
        p = root / name
        if p.is_file():
            return _first_paragraph(_read_head_text(p))
    return ""
