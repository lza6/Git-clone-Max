"""B5：本地仓库编目（catalog）—— 画像的 agent 可寻址出口。

动机：画像（`app/knowledge.py`）已写进 `repos.profile_json`，但 MCP 资源、
检索与报表都消费不到它 —— agent 想看「这几百个仓都是什么」只能逐个读库。
本模块只做**确定性出口**（零 LLM、零网络），两个形态同源：

- `catalog.json`：每仓一行的机器清单（host/owner/repo/画像/同步事实）；
- `catalog.md`：按 host 分组的一页纸表格（人类/agent 都可读）。

写入走 `util/atomic_io`（唯一 tmp + 替换），出站文本过 `redact`。
画像为空的仓显式标 `"profile": null` + `profile_status: "not_probed"`
（沿用 knowledge.py 的语义：探测不到 ≠ 没有）。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from .. import __version__
from ..i18n import tr
from ..util.atomic_io import write_text_atomic
from ..util.redact import redact
from .knowledge import RepoProfile

CATALOG_SCHEMA = "gcm.catalog.v1"

__all__ = ["CATALOG_SCHEMA", "build_catalog", "render_markdown", "write_catalog"]


def _profile_entry(raw: Any) -> dict[str, Any] | None:
    """`profile_json` → 清单里的 profile 子对象；空/坏值 → None（未探测）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    p = RepoProfile.from_json(text)
    return {
        "lang": p.lang,
        "kind": p.kind,
        "summary": redact(p.summary),
        "has_ci": p.has_ci,
        "has_tests": p.has_tests,
        "has_docs": p.has_docs,
        "langs": p.langs,
    }


def build_catalog(db: Any) -> dict[str, Any]:
    """从仓库表构建编目（只读；db 只需提供 list_repos）。"""
    repos: list[dict[str, Any]] = []
    by_lang: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for row in db.list_repos():
        profile = _profile_entry(row.get("profile_json"))
        if profile is not None:
            if profile["lang"]:
                by_lang[profile["lang"]] = by_lang.get(profile["lang"], 0) + 1
            kind = str(profile["kind"] or "unknown")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        repos.append({
            "host": str(row.get("host") or "github.com"),
            "owner": str(row.get("owner") or ""),
            "repo": str(row.get("repo") or ""),
            "folder_name": str(row.get("folder_name") or ""),
            "url": redact(str(row.get("url") or "")),
            "local_path": str(row.get("local_path") or ""),
            "head_sha": str(row.get("head_sha") or ""),
            "last_sync_at": str(row.get("last_sync_at") or ""),
            "favorite": bool(row.get("favorite")),
            "tags": str(row.get("tags") or ""),
            "profile_status": "probed" if profile is not None else "not_probed",
            "profile": profile,
        })
    return {
        "schema": CATALOG_SCHEMA,
        "app_version": __version__,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "count": len(repos),
        "by_lang": dict(sorted(by_lang.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))),
        "repos": repos,
    }


def _cell(text: Any, limit: int = 80) -> str:
    s = " ".join(str(text or "").split()).replace("|", "\\|")
    return s[:limit]


def render_markdown(catalog: dict[str, Any]) -> str:
    """渲染一页纸表格（按 host 分组；与 catalog.json 同源）。"""
    lines = [
        f"# {tr('本地仓库编目')}",
        "",
        tr("共 {} 个仓库 · 生成于 {}").format(
            catalog.get("count", 0), catalog.get("generated_at", "")),
        "",
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in catalog.get("repos") or []:
        groups.setdefault(str(row.get("host") or ""), []).append(row)
    for host in sorted(groups):
        lines.append(f"## {host}")
        lines.append("")
        lines.append("| {} | {} | {} | CI | {} | {} | {}".format(
            tr("仓库"), tr("语言"), tr("形态"), tr("测试"),
            tr("最近同步"), tr("简介")))
        lines.append("|---|---|---|---|---|---|---|")
        for row in groups[host]:
            profile = row.get("profile")
            if profile:
                lang = profile.get("lang") or tr("未探测")
                kind = profile.get("kind") or tr("未探测")
                ci = tr("有") if profile.get("has_ci") else tr("无")
                tests = tr("有") if profile.get("has_tests") else tr("无")
                summary = _cell(profile.get("summary"))
            else:
                lang = kind = tr("未探测")
                ci = tests = "—"
                summary = ""
            lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                _cell(row.get("folder_name"), 60), _cell(lang, 20),
                _cell(kind, 20), ci, tests,
                _cell(row.get("last_sync_at"), 20), summary))
        lines.append("")
    return "\n".join(lines)


def write_catalog(db: Any, out_dir: str | Path) -> dict[str, Any]:
    """导出 catalog.json + catalog.md 到 `out_dir`；返回 {json, markdown, count}。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cat = build_catalog(db)
    json_path = out / "catalog.json"
    md_path = out / "catalog.md"
    write_text_atomic(json_path, redact(json.dumps(cat, ensure_ascii=False, indent=2)))
    write_text_atomic(md_path, redact(render_markdown(cat)))
    return {"json": str(json_path), "markdown": str(md_path), "count": cat["count"]}
