"""批次计划（G84-12）：**执行前**告诉用户"这一批会发生什么"。

## 为什么需要
`engine.launch()` 一旦开始就是真实写盘：新建目录、`git fetch`、`merge --ff-only`、
必要时 rebase。用户点下按钮之前**看不到**"将要克隆几个 / 更新几个 / 哪几个地址根本
无效 / 哪个目标目录被别的东西占了"。CLI/MCP 侧更糟：外部 agent 直接调用 `clone_batch`
就落盘了，没有任何"先看看会做什么"的步骤。

## 设计约束（**必须诚实**）
- **纯函数、零网络**：只依据「输入文本 + 目标根目录 + 本地文件系统」判断，
  不碰 git、不联网。所以它**不能**说"已是最新"——那需要 `ls-remote`。
  凡是本地推不出的，一律标 `unknown`，不猜（参考 ripwire 的 `run_unknown` 纪律：
  "猜一个比没有更糟"）。
- **与执行器同源**：目录命名规则复用 `engine` 的那一条（`local_path or root/folder_name`），
  端口错位就会给出与真实行为不一致的预览——那比没有预览更有害。
- **不做副作用**：不创建目录、不写库、不改配置。

判定口径（每一档都要能对上执行器的真实行为）：

| 情况 | 判定 | 依据 |
|------|------|------|
| 同一目标目录在本批出现多次 | `duplicate` | engine 按 `local_path or folder_name` 去重，后者跳过 |
| 目标目录不存在 | `clone` | 全新克隆 |
| 目标目录存在且含 `.git` | `update` | 走 fetch/merge 链路 |
| 目录存在、无 `.git`、`local_path` 为空 | `replace` | 执行器会 `rmtree` 后重克隆
  （`gcm/git/service.py` 半成品清理分支） |
| 目录存在、无 `.git`、`local_path` 非空 | `blocked` | 路径来自用户/外部数据 →
  执行器**绝不删除**，非空且非 git 时报失败 |
| 地址解析失败 | `invalid` | `url_lib.parse_urls` 的 invalid 列表 |
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .url_lib import parse_urls

#: 计划里的动作取值（稳定字符串，进 CLI JSON 与 MCP 返回，改动 = 契约变更）
#: `replace` 与 `blocked` 的区别是**本轮审查抓出来的真问题**：
#: 执行器在 `spec.local_path` 为空（普通 URL 输入）时会 `shutil.rmtree` 目标目录再重克隆，
#: 只有 `local_path` 非空（导入的元数据）才"绝不删除"。两者混说成"不会覆盖"= 预览在
#: 最危险的那一档给出了反的信息。
ACTIONS = ("clone", "update", "replace", "blocked", "duplicate", "invalid")


@dataclass
class PlanRow:
    url: str
    folder: str
    target: str
    action: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "folder": self.folder, "target": self.target,
                "action": self.action, "reason": self.reason}


@dataclass
class BatchPlan:
    root: str
    rows: list[PlanRow] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {a: 0 for a in ACTIONS}
        for r in self.rows:
            out[r.action] = out.get(r.action, 0) + 1
        return out

    @property
    def writable(self) -> int:
        """真正会动盘的数量（clone + update + **replace**）。

        `replace` 会删掉现有目录再克隆 —— 它当然算"动盘"（审查发现：漏了它，
        会让 agent 以为"只有 2 个仓库会变"，实际是 3 个，其中 1 个毁掉重建）。
        """
        return (self.counts.get("clone", 0) + self.counts.get("update", 0)
                + self.counts.get("replace", 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "dry_run": True,
            "counts": self.counts,
            "writable": self.writable,
            "rows": [r.to_dict() for r in self.rows],
            # 明确写出"本地预检的边界"，避免 agent/用户把 preview 当承诺
            "limits": ("本预览只做本地判定：不说『已是最新』（需远端 ls-remote）；"
                       "`replace` 会**删除**目标目录后重克隆；"
                       "不预判网络/权限错误；clone 与 update 都是**将要尝试**的动作"),
        }

    def summary_lines(self, limit: int = 12) -> list[str]:
        """给人看的摘要（对话框/终端共用）。"""
        c = self.counts
        lines = [
            f"输入 {len(self.rows)} 条：将克隆 {c.get('clone', 0)} · "
            f"将更新 {c.get('update', 0)} · 将删除重建 {c.get('replace', 0)} · "
            f"目录被占 {c.get('blocked', 0)} · 重复 {c.get('duplicate', 0)} · "
            f"无效 {c.get('invalid', 0)}",
            f"目标目录：{self.root}",
        ]
        shown = [r for r in self.rows if r.action != "clone"][:limit]
        if shown:
            lines.append("需要注意的条目：")
            for r in shown:
                lines.append(f"  [{r.action}] {r.url} → {r.folder}（{r.reason}）")
        return lines


def classify_target(spec: Any, target: Path) -> tuple[str, str]:
    """单条目标的动作判定（纯函数，便于对**两种路径来源**分别断言）。

    为什么单独拆出来：`blocked` 与 `replace` 的区别只在 `spec.local_path`，
    而 `parse_urls` 产出的 spec 永远没有 `local_path`（那是导入元数据才有的）——
    不拆出来，`blocked` 这一档就永远无法被测试覆盖，只能靠人读代码相信它。
    """
    if not target.exists():
        return "clone", "目标目录不存在"
    if (target / ".git").exists():
        return "update", "已存在 git 仓库 → 增量更新"
    if getattr(spec, "local_path", ""):
        return ("blocked",
                "目标目录来自导入的元数据（local_path），执行器不会删除它；"
                "非空且非 git 时报失败")
    return ("replace",
            "目标目录已存在但不是 git 仓库；执行器会先删除它再重新克隆")


def plan_batch(text: str, root: str | Path) -> BatchPlan:
    """算出一批输入的执行计划（纯本地、无副作用、可重复调用）。"""
    root_path = Path(root)
    plan = BatchPlan(root=str(root_path))
    specs, invalid = parse_urls(text)

    seen: dict[str, str] = {}
    for raw in invalid:
        plan.rows.append(PlanRow(url=str(raw), folder="", target="",
                                 action="invalid", reason="地址无法解析"))
    for spec in specs:
        folder = spec.folder_name
        target = Path(spec.local_path) if spec.local_path else root_path / folder
        # 大小写/路径分隔符规范化：Windows 上 `C:/x/Foo` 与 `c:/x/foo` 是同一目录，
        # 不规范化会让预览说"要克隆两份"，而执行器只做一份。
        key = os.path.normcase(str(target))
        if key in seen:
            plan.rows.append(PlanRow(url=spec.url_https, folder=folder, target=key,
                                     action="duplicate",
                                     reason=f"与本批另一条指向同一目录（{seen[key]}）"))
            continue
        seen[key] = spec.url_https
        action, reason = classify_target(spec, target)
        plan.rows.append(PlanRow(url=spec.url_https, folder=folder, target=key,
                                 action=action, reason=reason))
    return plan
