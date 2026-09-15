"""G35-4 任务清单持久化：多份仓库清单存取 data/lists/*.json。

每行一个仓库地址（HTTPS/SSH/短格式均可），清单本质是字符串行集合。
原子写 + 损坏/缺失兜底，非法文件名字符清洗，文件为纯 JSON 便于分享。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

# 非法文件名字符（Windows）：<>:"/\|?* 与控制字符 \x00-\x1f 统一替换为 _
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class TaskLists:
    """任务清单存取：save / list_names / load / items / delete。"""

    def __init__(self, dir_path: str | Path) -> None:
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    @staticmethod
    def _clean(name: str) -> str:
        """清洗非法文件名字符，返回可直接作为文件名的字符串。"""
        return _ILLEGAL_CHARS.sub("_", name or "")

    def _file_path(self, name: str) -> Path:
        return self.dir / f"{self._clean(name)}.json"

    def _read(self, name: str) -> dict | None:
        """读回结构化 dict；不存在 / 非 dict / 损坏 → None。"""
        path = self._file_path(name)
        try:
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                return None
            return data
        except Exception:
            return None

    # ------------------------------------------------------------------
    def save(self, name: str, items: list[str]) -> Path:
        """保存清单（原子写 + 同名校覆盖保 created_at / 刷 updated_at），返回路径。"""
        name = (name or "").strip()
        clean = self._clean(name)
        # 空名，或清洗后不剩任何有效字符（如全非法字符 "???/<>"）→ 拒绝
        if not name or not clean or not clean.replace("_", ""):
            raise ValueError("任务清单名不能为空或全为非法字符")
        now = datetime.now().isoformat(timespec="microseconds")
        old = self._read(clean)
        created_at = old.get("created_at", now) if old else now
        data = {
            "name": clean,
            "items": [str(x) for x in items],
            "created_at": created_at,
            "updated_at": now,
        }
        path = self._file_path(clean)
        tmp = self.dir / f"{clean}.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def list_names(self) -> list[str]:
        """返回存在的清单名（按文件名排序，不含 .json 后缀）。"""
        return sorted(p.stem for p in self.dir.glob("*.json"))

    def load(self, name: str) -> dict | None:
        """读回清单 dict；文件不存在/损坏 → None。"""
        return self._read(name)

    def items(self, name: str) -> list[str]:
        """便捷读清单行集合；不存在 → []。"""
        data = self._read(name)
        if not data:
            return []
        return [str(x) for x in data["items"]]

    def delete(self, name: str) -> bool:
        """删除清单；不存在返回 False。"""
        path = self._file_path(name)
        try:
            if path.exists():
                path.unlink()
                return True
        except Exception:
            return False
        return False
