"""管理页动作按钮的视觉权重（Rev11-C P-01）。

**为什么单独成模块**：`manage_panel.py` 已贴着 800 行硬上限
（`test_g79_repo_hygiene::TestNoNewOversizedModules` 用集合相等断言）。
这与 v4 的 `queue_db.py`、v5 的 `repo_profile.py` 是同一范式：
**新增能力落新模块，不往热文件里塞**。

四档语义（QSS 侧规则见 `qss_build.py` 的 `#primary` / `#secondary` / `#ghost` /
`#danger`）：

| 权重 | 场景 | 例 |
|------|------|-----|
| `primary` | 本屏唯一主操作（反相块） | 一键更新全部 |
| `secondary` | 常用、但有替代路径 | 刷新列表 / 导入本地 / 导出所选 |
| `ghost` | 低频，不占视觉预算 | 打标签 / 导入导出元数据 |
| `danger` | 中断或破坏性 | 取消全部 / 删除选中记录 |
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: 属性名 → 权重（按语义分组，改权重只改这里）
ACTION_WEIGHTS: Mapping[str, str] = {
    "btn_update_all": "primary",
    "btn_cancel_manage": "danger",
    "btn_delete": "danger",
    "btn_refresh": "secondary",
    "btn_import_local": "secondary",
    "btn_export_selected": "secondary",
    "btn_batch_tag": "ghost",
    "btn_export_meta": "ghost",
    "btn_import_meta": "ghost",
}


def apply_weights(owner: Any,
                  weights: Mapping[str, str] | None = None) -> int:
    """给 owner 上对应属性名的按钮赋 objectName；返回实际赋值的个数。

    属性不存在时跳过（不同版本的管理页按钮集合可能不同，不该因此崩）。
    """
    applied = 0
    for attr, weight in (weights or ACTION_WEIGHTS).items():
        btn = getattr(owner, attr, None)
        if btn is None:
            continue
        btn.setObjectName(weight)
        applied += 1
    return applied


__all__ = ["ACTION_WEIGHTS", "apply_weights"]
