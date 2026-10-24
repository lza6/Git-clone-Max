"""仓库级 SSH 私钥的读写（G80-B8）。

为什么单独成模块：`repo_db.py` 在 G80-B8 之前已经 799 行（距 800 红线 1 行），
再往里加两三个方法就会变成第 4 个超限模块（守卫 `test_g79_repo_hygiene` 会红）。
把「SSH 私钥路径」这一组访问器抽成 mixin，`Database` 继承它：
对外 API 不变（`db.get_repo_ssh_key(...)` / `db.set_ssh_key(...)` 照旧），
repo_db.py 却回到了 800 线内。

边界：**只存路径，不存私钥内容**（校验在 `gcm/util/sshkey.py`）。
"""
from __future__ import annotations

from typing import Any


class SshKeyMixin:
    """`repos.ssh_key` 的读写（路径，不是私钥内容）。宿主类须提供 `_lock` 与 `_conn`。"""

    _lock: Any
    _conn: Any

    def get_repo_ssh_key(self, owner: str, repo: str, host: str = "github.com") -> str:
        """读仓库级 SSH 私钥**路径**（空串 = 用全局 `settings.ssh_key_path`）。

        此前该列只有写入方法、没有任何读取点（死列）；本方法是唯一读取入口，
        由引擎在每个任务启动时查一次并覆盖全局配置（per-repo > global）。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT ssh_key FROM repos WHERE owner=? AND repo=? AND host=?",
                (str(owner or ""), str(repo or ""), str(host or "github.com")),
            ).fetchone()
        if row is None:
            return ""
        try:
            return str(row["ssh_key"] or "")
        except (IndexError, KeyError, TypeError):
            return ""

    def set_ssh_key(self, repo_id: int, key_path: str) -> None:
        """为仓库指定 SSH 私钥路径（clone/fetch 时经 `GIT_SSH_COMMAND` 注入）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE repos SET ssh_key=?, updated_at=datetime('now','localtime') WHERE id=?",
                (str(key_path or ""), repo_id))
            self._conn.commit()
