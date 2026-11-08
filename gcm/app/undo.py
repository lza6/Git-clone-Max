"""撤销的分派与执行（Rev12 U-05，尼尔森原则③）。

**职责边界**（为什么独立于 `util/oplog.py`）：
`oplog` 只管**记录**，不知道任何业务；本模块只管**逆做**，不知道文件格式。
这样 `oplog` 可以原样给 CLI / MCP 复用（只读查看历史），而逆做永远只在 UI 进程发生 ——
逆做要调 `owner.db` 的写方法，只有主进程持有完整上下文。

**唯一的写库纪律**：撤销**必须**复用 `db` 已有的公开方法（`upsert_repo` / `set_tags` …），
**不许**为撤销另写一套 INSERT/UPDATE —— 那是两份真相，且会绕过 `write_txn` 的回滚保护。
"""
from __future__ import annotations

from typing import Any

from ..i18n import tr
from ..util.oplog import OpLog, OpRecord


class UndoError(RuntimeError):
    """撤销失败（已被撤销 / 不支持 / 记录缺失）。消息面向用户，必须可读。"""


#: action → 人类可读的操作名（撤销按钮文案用）
ACTION_LABELS = {
    "repo_delete": "删除仓库记录",
    "repo_tags": "修改标签",
    "repo_favorite": "切换收藏",
    "repo_excluded": "切换排除",
    "list_delete": "删除清单",
    "retry_queue_clear": "清空重试队列",
    "url_history_clear": "清空地址历史",
    "input_clear": "清空输入区",
}


def can_undo(rec: OpRecord) -> bool:
    """该条记录此刻能否撤销。UI 画不画按钮以这个为准，不要自己判断。"""
    if rec.undoable != 1 or rec.undone_at:
        return False
    return rec.action in ACTION_LABELS


def undo_record(oplog: OpLog, owner: Any, rec: OpRecord) -> str:
    """执行撤销，返回给用户看的成功文案。

    失败一律抛 `UndoError`（调用方负责提示），**绝不静默返回 False** ——
    「点了撤销没反应」比报错更伤可用性（尼尔森原则①）。
    """
    if rec.undone_at:
        raise UndoError(tr("这条操作已经撤销过了"))
    if rec.undoable != 1:
        raise UndoError(tr("该操作不支持撤销（只记录了操作，没有保存可恢复的旧值）"))
    handler = _HANDLERS.get(rec.action)
    if handler is None:
        raise UndoError(f"暂不支持撤销「{rec.action}」")

    summary = handler(owner, rec)
    # 先做逆操作再标记：标记失败只会导致「可再次撤销」，比反过来安全
    oplog.mark_undone(rec.id)
    # 撤销本身也记一条（undoable=0，防止递归撤销）
    oplog.record("undo", rec.target, summary=f"撤销 {rec.action}（{rec.id}）",
                 undoable=False)
    return summary


# ---------------------------------------------------------------- 各 action 的逆做


def _undo_repo_delete(owner: Any, rec: OpRecord) -> str:
    """撤销「删除仓库记录」：把 before 快照原样 upsert 回去。

    `repos` 表对 `upsert_repo` 而言不含 tags/favorite/excluded（那三列由迁移
    单独补上，且 `upsert_repo` 不写它们），所以**必须先 upsert 再单独恢复这三项**，
    否则恢复出来的仓库会丢标签与收藏 —— 那正是「撤销后状态与操作前不一致」。

    批量删除时 `before["batch"]` 是一个列表，逐条恢复；单条失败不打断其余条目
    （部分恢复比一条都不恢复好，但必须把失败条数如实报给用户）。
    """
    batch = rec.before.get("batch")
    if isinstance(batch, list):
        snapshots = [r for r in batch if isinstance(r, dict)]
    else:
        snapshots = [dict(rec.before or {})]
    if not snapshots:
        raise UndoError(tr("这条记录没有可恢复的仓库"))

    db = getattr(owner, "db", None)
    if db is None:
        raise UndoError(tr("数据库不可用，无法恢复"))

    done, failed = 0, 0
    for row in snapshots:
        if not row.get("owner") or not row.get("repo"):
            failed += 1
            continue
        try:
            _restore_one_repo(db, row)
            done += 1
        except Exception:  # noqa: BLE001 —— 单条失败要计数上报，不能吞掉整批
            failed += 1
    _refresh(owner)
    if not done:
        raise UndoError(tr("没有任何仓库能恢复（快照数据不完整）"))
    if failed:
        return f"已恢复 {done} 个仓库，{failed} 个失败（数据不完整）"
    return f"已恢复 {done} 个仓库" if done > 1 else \
        f"已恢复仓库「{snapshots[0].get('display') or snapshots[0].get('repo')}」"


def _restore_one_repo(db: Any, row: dict) -> None:
    """把一条仓库快照写回（复用既有公开方法，不另写 INSERT）。"""
    from ..models import RepoSpec

    spec = RepoSpec(
        owner=str(row["owner"]),
        repo=str(row["repo"]),
        url_https=str(row.get("url") or ""),
        display=str(row.get("display") or ""),
        folder_name=str(row.get("folder_name") or ""),
    )
    rid = db.upsert_repo(
        spec,
        str(row.get("local_path") or ""),
        str(row.get("host") or "github.com"),
        row.get("default_branch") or None,
        row.get("head_sha") or None,
    )
    # upsert 不覆盖这三列时也要显式写回，保证「撤销后 == 操作前」
    tags = row.get("tags")
    if isinstance(tags, list):
        clean = [str(t) for t in tags if str(t).strip()]
        if clean:
            db.set_tags(int(rid), clean)
    elif isinstance(tags, str) and tags:
        db.set_tags(int(rid), [t for t in tags.split("|") if t])
    if row.get("favorite"):
        db.set_favorite(int(rid), True)
    if row.get("excluded"):
        db.set_excluded(int(rid), True)


def _undo_repo_tags(owner: Any, rec: OpRecord) -> str:
    row = dict(rec.before or {})
    rid = row.get("repo_id")
    if rid is None:
        raise UndoError(tr("这条记录缺少仓库 ID，无法恢复标签"))
    db = getattr(owner, "db", None)
    if db is None:
        raise UndoError(tr("数据库不可用，无法恢复"))
    db.set_tags(int(rid), list(row.get("tags") or []))
    _refresh(owner)
    return tr("已恢复原标签")


def _undo_toggle(owner: Any, rec: OpRecord, *, field: str) -> str:
    row = dict(rec.before or {})
    rid = row.get("repo_id")
    if rid is None:
        raise UndoError(tr("这条记录缺少仓库 ID，无法恢复"))
    db = getattr(owner, "db", None)
    if db is None:
        raise UndoError(tr("数据库不可用，无法恢复"))
    setter = getattr(db, f"set_{field}", None)
    if setter is None:
        raise UndoError(f"数据库没有 set_{field}，无法恢复")
    setter(int(rid), bool(row.get(field)))
    _refresh(owner)
    return tr("已恢复原状态")


def _undo_repo_favorite(owner: Any, rec: OpRecord) -> str:
    return _undo_toggle(owner, rec, field="favorite")


def _undo_repo_excluded(owner: Any, rec: OpRecord) -> str:
    return _undo_toggle(owner, rec, field="excluded")


def _undo_list_delete(owner: Any, rec: OpRecord) -> str:
    """撤销「删除清单」：写回 lists 目录（`save()` 就是 `delete()` 的逆）。"""
    row = dict(rec.before or {})
    name = row.get("name") or ""
    lines = row.get("lines")
    if not name or not isinstance(lines, list):
        raise UndoError(tr("这条记录缺少清单内容，无法恢复"))
    store = getattr(owner, "lists", None)
    if store is None or not hasattr(store, "save"):
        raise UndoError(tr("清单存储不可用，无法恢复"))
    store.save(name, [str(x) for x in lines])
    return f"已恢复清单「{name}」"


def _undo_retry_queue_clear(owner: Any, rec: OpRecord) -> str:
    """撤销「清空重试队列」：把 before 里的条目重新入队。

    复用 `requeue_dropped` 是错的 —— 那只管 dropped 区。清空队列丢的是整个
    `entries`，正确逆做是**把它整体写回**，与 `retry_queue.clear()` 对称。
    """
    row = dict(rec.before or {})
    entries = row.get("entries")
    if not isinstance(entries, list) or not entries:
        raise UndoError(tr("这条记录没有可恢复的条目"))
    queue = _find_retry_queue(owner)
    if queue is None:
        raise UndoError(tr("重试队列不可用，无法恢复"))
    restore = getattr(queue, "restore_entries", None)
    if restore is None:
        raise UndoError(tr("重试队列没有 restore_entries 方法，无法恢复"))
    n = restore([e for e in entries if isinstance(e, dict)])
    if not n:
        raise UndoError(tr("没有任何条目能恢复（数据格式不匹配）"))
    return f"已恢复 {n} 条重试任务"


def _find_retry_queue(owner: Any) -> Any:
    """按已知挂载位置找重试队列（引擎持有它，面板只做展示）。"""
    for attr in ("retry_queue", "_retry_queue"):
        q = getattr(owner, attr, None)
        if q is not None:
            return q
    eng = getattr(owner, "engine", None)
    for attr in ("retry_queue", "_retry_queue"):
        q = getattr(eng, attr, None) if eng is not None else None
        if q is not None:
            return q
    return None


def _undo_url_history_clear(owner: Any, rec: OpRecord) -> str:
    """撤销「清空地址历史」。

    `UrlHistory` **没有** `restore` 方法，只有 `add()` 且它是头插。
    所以按 before 列表**倒序** add，才能还原原来的先后顺序。
    """
    row = dict(rec.before or {})
    items = row.get("items")
    store = getattr(owner, "url_history", None)
    if store is None or not isinstance(items, list):
        raise UndoError(tr("地址历史不可用，无法恢复"))
    for url in reversed([u for u in items if isinstance(u, str) and u]):
        store.add(url)
    return f"已恢复 {len(items)} 条地址历史"


def _undo_input_clear(owner: Any, rec: OpRecord) -> str:
    """撤销「清空输入区」：把原文写回（追加而不是覆盖，避免冲掉用户新输入）。"""
    row = dict(rec.before or {})
    text = row.get("text")
    if not isinstance(text, str) or not text.strip():
        raise UndoError(tr("这条记录没有可恢复的输入内容"))
    widget = getattr(owner, "repo_input", None)
    if widget is None:
        raise UndoError(tr("输入区不可用，无法恢复"))
    current = widget.toPlainText()
    restored = text if not current.strip() else current.rstrip("\n") + "\n" + text
    widget.setPlainText(restored)
    return f"已恢复输入区（{len(text.splitlines())} 行）"


def _refresh(owner: Any) -> None:
    """尽力刷新管理页（失败不影响撤销结果本身）。"""
    try:
        loader = getattr(owner, "_load_db_into_grid", None) or getattr(
            owner, "_refresh_manage", None)
        if loader is not None:
            loader()
    except Exception as e:  # noqa: BLE001 —— 刷新失败不该把已成功的撤销说成失败
        from ..app.applog import warn as _warn
        _warn(f"撤销后刷新管理页失败（数据已恢复，仅界面未刷新）：{e}")


_HANDLERS = {
    "repo_delete": _undo_repo_delete,
    "repo_tags": _undo_repo_tags,
    "repo_favorite": _undo_repo_favorite,
    "repo_excluded": _undo_repo_excluded,
    "list_delete": _undo_list_delete,
    "retry_queue_clear": _undo_retry_queue_clear,
    "input_clear": _undo_input_clear,
    "url_history_clear": _undo_url_history_clear,
}


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action or tr("未知操作"))


__all__ = ["ACTION_LABELS", "UndoError", "action_label", "can_undo", "undo_record"]
