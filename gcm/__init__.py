"""Git-clone-Max — GitHub 仓库批量并行下载与增量更新工具。

版本契约（红线，见 CLAUDE.md §5）：
- `__version__` 是**全项目唯一事实源**。pyproject / installer.iss / spec /
  plugin.json / README / docs 的版本都必须与它一致，有守卫测试强制。

changelog 从 v9.10.3 起拆到 `gcm/_changelog_data.py`（G81 / N-4）：
本文件曾同时承载「版本号 + 76 条 changelog」，163 行里 150 行是 changelog 文本、
最长单条 1,347 字符、被 88/320 次提交触碰 —— 任何改动都容易在同一处冲突。
拆分后本文件只保留版本契约；changelog 由 `changelog.d/` 碎片驱动、
由 `scripts/changelog.py build` 聚合。**对外 API 不变**：
`from gcm import __changelog__` 拿到的仍是 `tuple[str, ...]`（最新在前）。
"""
from __future__ import annotations

from ._changelog_data import ENTRIES as __changelog__

__version__ = "9.12.0"
__app_name__ = "Git-clone-Max"

__all__ = ["__version__", "__app_name__", "__changelog__"]
