"""G49-1 轻量 i18n：零构建 tr(key) 文案查询（不引 Qt Linguist）。

设计（与规划一致）：
- 默认 zh：tr(key) 在 zh 模式直接返回 key（key 即中文文案，天然一致）；
- 切 en：查 gcm/i18n/en.py 字典；缺失 key 回退中文（key 本身）；
- 设置页语言下拉改 settings.language，重启后 __main__ 调 set_language 生效。
"""
from __future__ import annotations

_CURRENT = "zh"


def set_language(lang: str) -> None:
    """设置当前语言（"zh"/"en"；其它值回退 zh）。"""
    global _CURRENT
    _CURRENT = lang if lang in ("zh", "en") else "zh"


def current_language() -> str:
    return _CURRENT


def tr(key: str) -> str:
    """取当前语言下的文案；en 缺失或 zh 模式直接返回 key（中文兜底）。"""
    if _CURRENT == "en":
        from .en import EN
        return EN.get(key, key)
    from .zh import ZH
    return ZH.get(key, key)
