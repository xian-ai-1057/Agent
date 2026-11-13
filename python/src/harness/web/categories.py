"""每個人自己的「專案」：把對話分類整理（和 ChatGPT、Claude 的專案一樣）。

這裡的專案只是對話的分類，和 harness 的專案資料夾（harness.yaml 所在的
地方）無關；程式裡叫 category，避免和 project 混在一起。

分類存在這個人的資料夾裡的 categories.json（本機模式是 .harness/，要
登入時是 .harness/users/<識別碼>/）；每段對話屬於哪個分類記在對話的
.meta.json（見 harness.web.workspace）。
"""

import dataclasses
import json
import os
import pathlib
import time
import uuid
from typing import Any

FILE_NAME = "categories.json"
NAME_LIMIT = 50
MAX_CATEGORIES = 200


class CategoryError(Exception):
    """分類的操作不合法。

    Attributes:
        status: 給瀏覽器的 HTTP 狀態碼。
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclasses.dataclass(frozen=True)
class Category:
    """一個分類。

    Attributes:
        id: 識別碼。
        name: 顯示的名稱。
        created_at: 建立時間（毫秒）。
    """

    id: str
    name: str
    created_at: float

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "createdAt": self.created_at}


class CategoryStore:
    """一個人的分類清單，存在 directory/categories.json。"""

    def __init__(self, directory: pathlib.Path) -> None:
        self.path = directory / FILE_NAME

    def all(self) -> list[Category]:
        """所有分類，依建立時間排列。"""
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("categories") if isinstance(data, dict) else None
        found = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            category_id, name = item.get("id"), item.get("name")
            created = item.get("createdAt")
            if isinstance(category_id, str) and isinstance(name, str):
                when = created if isinstance(created, int | float) else 0
                found.append(Category(category_id, name, when))
        return sorted(found, key=lambda item: item.created_at)

    def get(self, category_id: str) -> Category | None:
        return next((c for c in self.all() if c.id == category_id), None)

    def create(self, name: str) -> Category:
        """新增分類。

        Raises:
            CategoryError: 名稱不合法、重複，或分類太多。
        """
        name = _clean(name)
        items = self.all()
        if len(items) >= MAX_CATEGORIES:
            raise CategoryError(400, f"最多只能有 {MAX_CATEGORIES} 個專案")
        _check_unique(items, name)
        category = Category(uuid.uuid4().hex[:12], name, time.time() * 1000)
        self._save([*items, category])
        return category

    def rename(self, category_id: str, name: str) -> Category:
        """改名。

        Raises:
            CategoryError: 找不到分類，或名稱不合法、重複。
        """
        name = _clean(name)
        items = self.all()
        current = next((c for c in items if c.id == category_id), None)
        if current is None:
            raise CategoryError(404, "找不到這個專案")
        _check_unique([c for c in items if c.id != category_id], name)
        renamed = dataclasses.replace(current, name=name)
        self._save([renamed if c.id == category_id else c for c in items])
        return renamed

    def delete(self, category_id: str) -> None:
        """刪除分類（不刪對話，見 Workspace.delete_category）。

        Raises:
            CategoryError: 找不到分類。
        """
        items = self.all()
        if not any(c.id == category_id for c in items):
            raise CategoryError(404, "找不到這個專案")
        self._save([c for c in items if c.id != category_id])

    def _save(self, items: list[Category]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{FILE_NAME}.tmp")
        data = {"categories": [item.to_json() for item in items]}
        temporary.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        os.replace(temporary, self.path)


def _clean(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise CategoryError(400, "專案名稱不可以是空的")
    name = " ".join(name.split())
    if len(name) > NAME_LIMIT:
        raise CategoryError(400, f"專案名稱最多 {NAME_LIMIT} 個字")
    return name


def _check_unique(items: list[Category], name: str) -> None:
    if any(item.name.casefold() == name.casefold() for item in items):
        raise CategoryError(409, f"已經有叫「{name}」的專案")
