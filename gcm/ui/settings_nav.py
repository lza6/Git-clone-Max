"""设置页分组跳转（C2-4）：把「去设置」的落点算出来。

**为什么独立成模块**：同 `batch_actions.py` —— 主窗体量已登记为技术债，
新增 UI 逻辑落独立模块。这里只做「section 键 → 分组序号」的映射与滚动。
"""
from __future__ import annotations

from typing import Any

#: 失败面板「去设置」的 section 键 → 设置分组标题关键词（按顺序取第一个命中）。
SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "network": ("并行", "网络", "Token", "凭据", "代理", "镜像"),
    "startup": ("启动",),
    "insight": ("数据洞察", "统计"),
    "appearance": ("外观",),
}


def find_group_index(groups: list[Any], section: str) -> int:
    """返回该 section 对应的设置分组序号；找不到返回 -1。

    纯函数（不碰控件），可单测。
    """
    keywords = SECTION_KEYWORDS.get(section, SECTION_KEYWORDS["network"])
    for i, grp in enumerate(groups):
        try:
            title = grp.title()
        except Exception:       # noqa: BLE001 —— 非分组控件直接跳过
            continue
        if any(k in title for k in keywords):
            return i
    return -1


def goto_section(owner: Any, section: str = "network") -> int:
    """切到设置页并滚到对应分组，返回实际落在的分组序号（-1 = 没找到）。

    找不到关键词时退回第 2 组（「并行与网络」，历史顺序固定）；再找不到就停在设置页顶部 ——
    绝不弹窗说「找不到」，那等于又给了用户一个无效动作。
    """
    try:
        owner.tabs.setCurrentIndex(2)
    except Exception:           # noqa: BLE001 —— 没有页签控件时只能放弃跳转
        return -1
    groups = (list(getattr(owner, "settings_groups", []))
              + list(getattr(owner, "settings_groups_extra", [])))
    target = find_group_index(groups, section)
    if target < 0 and len(groups) > 1:
        target = 1
    if target >= 0:
        nav = getattr(owner, "settings_nav", None)
        if nav is not None:
            try:
                nav.setCurrentRow(target)
            except Exception as e:   # noqa: BLE001 —— 导航控件异常不该阻止滚动，但要留痕
                from ..app.applog import warn as _warn
                _warn(f"设置导航选中失败（继续滚动）：{e}")
        try:
            owner._scroll_to_settings_group(target)
        except Exception as e:  # noqa: BLE001
            from ..app.applog import warn as _warn
            _warn(f"设置分组滚动失败：{e}")
    return target
