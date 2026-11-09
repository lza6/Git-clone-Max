"""结果文案（G75）：状态与动作的**唯一**用户可见文案来源。

为什么单独成模块：
1. 原来的两份映射分散在 `progress_table._on_worker_result` 内联字典里，
   与 `status_pill._COLOR_KEY_BY_STATUS` 形成两条"状态→颜色/文案"的真相；
2. 这两张表是**presentation 数据**，与表格刷新逻辑无关，塞在 110 行的回调里既难测也难改；
3. 文案必须走 `tr()`（`gcm/i18n/`），集中在这里才不会漏翻。

口径：颜色不在这里定义 —— 用 `status_pill.color_for(status, palette)`（按色板取语义色）。
"""
from __future__ import annotations

from ..i18n import tr

# 状态值（models.SyncStatus.value）→ 文案键
_STATUS_TEXT: dict[str, str] = {
    "pending": tr("等待中"),
    "running": tr("更新中…"),
    "success": tr("成功"),
    "failed": tr("失败"),
    # 状态列用独立键「已跳过」：'跳过' 已是首启向导 Skip 按钮的 key，
    # 同一个 dict 里一个 key 只能有一个译文，混用会让按钮和状态抢同一句英文（且 ruff F601）。
    "skipped": tr("已跳过"),
    "cancelled": tr("已取消"),
    "conflict": tr("冲突"),
}

# 动作值（models.SyncAction.value）→ 文案键（`updated` 需要带提交数）
_ACTION_TEXT: dict[str, str] = {
    "cloned": tr("新建克隆"),
    "fetched": tr("已最新"),
    "empty": tr("空仓库"),
    "skipped": tr("跳过"),
    "conflict": tr("冲突保留"),
    "cancelled": tr("取消"),
    "failed": tr("失败"),
}


def _value_of(item) -> str:
    """取枚举的字符串值；传入 str 时原样返回（调用方可能已解包）。"""
    return str(getattr(item, "value", item))


def status_label(status) -> str:
    """状态 → 界面文案（未知状态回退原始值）。"""
    raw = _value_of(status)
    return tr(_STATUS_TEXT.get(raw, raw))


def action_label(action, commits: int = 0) -> str:
    """动作 → 界面文案；`updated` 带提交数（`增量 +3`）。"""
    raw = _value_of(action)
    if raw == "updated":
        return tr("增量 +{n}").format(n=int(commits or 0))
    return tr(_ACTION_TEXT.get(raw, raw))


def format_duration(duration_ms) -> str:
    """毫秒 → 可读耗时（Rev11 V-16 唯一口径，tests/test_rev11_w2.py 钉死）。

    落在本模块的理由：耗时与状态/动作同属"一行的结果呈现文案"，
    且 progress_table.py 已贴 800 行红线（Rev11 执行时破线，抽出即回）。

    - None/0/负数 → `—`（不显示假 0，与 status_strip 同一纪律）；
    - < 60s → `12.3s`（保留一位小数）；
    - ≥ 60s → `m:ss`（与 status_strip.format_elapsed 同形态，便于跨表比较）。

    LBYL 判型而非 try/except：异常预算棘轮（test_g80_hygiene）只降不升。
    """
    v = duration_ms
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        s = str(v).strip() if v is not None else ""
        if s.replace(".", "", 1).lstrip("+-").isdigit():
            v = int(s)
        else:
            return "—"
    if v <= 0:
        return "—"
    total_s = v / 1000.0
    if total_s < 60:
        return f"{total_s:.1f}s"
    minutes, seconds = divmod(int(total_s), 60)
    return f"{minutes}:{seconds:02d}"


__all__ = ["status_label", "action_label", "format_duration"]
