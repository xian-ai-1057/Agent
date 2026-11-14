"""存取群組：管理者決定每位成員可以使用哪些模型、agent、工具與知識庫。

做法參考 MaiAgent 的角色與 LiteLLM 的 access group：

    - 群組列出可以使用的資源：模型、agent、工具組（toolset）、知識庫，
      以及知識庫裡的標籤（細部過濾：有標籤的文件只有開放該標籤的人
      搜尋得到）。每一類可以是「全部」（存成 NULL），或一份名稱清單。
    - 每位成員自動屬於內建的「所有人」群組（不能刪除、不能移出），另外
      可以加入其他群組。能用的資源是所屬群組的聯集。
    - 新建的群組預設什麼都不能用；「所有人」一開始是全部，升級後原本
      的成員照常使用，管理者再把它收窄。
    - 管理者不受群組限制，什麼都能用。

群組存在帳號資料庫（.harness/harness.db）：

    access_groups   群組：識別碼、名稱、說明、五類資源（JSON 或 NULL）
    group_members   成員屬於哪些群組（不含「所有人」，它是隱含的）

這裡只管資料；伺服器怎麼依 Access 擋下沒開放的資源見 workspace.py。
"""

import dataclasses
import json
import pathlib
import sqlite3
import time
import uuid
from typing import Any

from harness.web import accounts as accounts_lib

EVERYONE_ID = "everyone"
EVERYONE_NAME = "所有人"
# 五類資源，對應資料表的欄位與 Access、Group 的屬性。knowledge_bases 是
# 知識庫的識別碼，knowledge 是知識庫裡的標籤。
KINDS = ("models", "agents", "toolsets", "knowledge_bases", "knowledge")
# API（見 src/web/protocol.ts 的 AccessKind）用 camelCase。
API_NAMES = {kind: kind for kind in KINDS} | {
    "knowledge_bases": "knowledgeBases"
}
_NAME_LIMIT = 40
_DESCRIPTION_LIMIT = 200
_LIST_LIMIT = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS access_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    models TEXT,
    agents TEXT,
    toolsets TEXT,
    knowledge TEXT,
    knowledge_bases TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS group_members_user ON group_members (user_id);
"""


class GroupError(Exception):
    """群組操作失敗，訊息會直接顯示給使用者。

    Attributes:
        status: HTTP 狀態碼。
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclasses.dataclass(frozen=True)
class Access:
    """一個人可以使用的資源。每一類是 None（全部）或名稱的集合。"""

    models: frozenset[str] | None = None
    agents: frozenset[str] | None = None
    toolsets: frozenset[str] | None = None
    knowledge_bases: frozenset[str] | None = None
    knowledge: frozenset[str] | None = None

    @property
    def unrestricted(self) -> bool:
        """每一類都是全部。"""
        return all(getattr(self, kind) is None for kind in KINDS)

    def allows(self, kind: str, name: str) -> bool:
        """這一類資源裡的 name 能不能用。"""
        allowed = getattr(self, kind)
        return allowed is None or name in allowed


# 管理者、本機模式與沒有群組功能時：什麼都能用。
FULL_ACCESS = Access()


@dataclasses.dataclass(frozen=True)
class Group:
    """一個存取群組。

    Attributes:
        id: 識別碼；內建的「所有人」是 everyone。
        name: 顯示名稱，不分大小寫不能重複。
        description: 說明（可以空白）。
        models: 可以使用的模型名稱；None 表示全部。
        agents: 可以開始對話的 agent；None 表示全部。
        toolsets: 可以使用的工具組；None 表示全部。
        knowledge_bases: 可以搜尋的知識庫（識別碼）；None 表示全部。
        knowledge: 知識庫裡可以搜尋的標籤；None 表示全部。沒有標籤的
            文件所有人都搜尋得到，不受這裡限制。
        members: 成員的識別碼（「所有人」是空的，代表每一位成員）。
    """

    id: str
    name: str
    description: str = ""
    models: tuple[str, ...] | None = None
    agents: tuple[str, ...] | None = None
    toolsets: tuple[str, ...] | None = None
    knowledge_bases: tuple[str, ...] | None = None
    knowledge: tuple[str, ...] | None = None
    members: tuple[str, ...] = ()
    created_at: float = 0.0

    @property
    def builtin(self) -> bool:
        """是不是內建的「所有人」。"""
        return self.id == EVERYONE_ID

    def info(self) -> dict[str, Any]:
        """GroupInfo（見 src/web/protocol.ts）。"""
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "builtin": self.builtin,
            "members": list(self.members),
        }
        for kind in KINDS:
            value = getattr(self, kind)
            data[API_NAMES[kind]] = None if value is None else list(value)
        return data


class GroupStore:
    """存取群組的資料庫，和帳號共用 .harness/harness.db。

    每次操作都開一個新的連線，CLI 與正在執行的伺服器可以同時使用。
    """

    def __init__(self, path: str | pathlib.Path, clock=time.time) -> None:
        self.path = pathlib.Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(access_groups)")
            }
            if "knowledge_bases" not in columns:
                # 支援多個知識庫之前建立的資料庫：原本的群組可以搜尋全部
                # 知識庫（當時只有一個），和升級前一樣。
                db.execute(
                    "ALTER TABLE access_groups ADD COLUMN knowledge_bases TEXT"
                )
            # 內建的「所有人」：一開始什麼都能用，和加入群組之前一樣。
            db.execute(
                "INSERT OR IGNORE INTO access_groups (id, name, created_at) "
                "VALUES (?, ?, 0)",
                (EVERYONE_ID, EVERYONE_NAME),
            )
            db.commit()
        finally:
            db.close()

    @classmethod
    def for_project(cls, root: str | pathlib.Path) -> "GroupStore":
        """專案的群組資料庫（和帳號同一個檔案）。"""
        return cls(pathlib.Path(root) / accounts_lib.DB_FILE)

    def list_groups(self) -> list[Group]:
        """所有群組：「所有人」排第一，其他依建立時間。"""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM access_groups "
                "ORDER BY id != ?, created_at, name",
                (EVERYONE_ID,),
            ).fetchall()
            members = _members(db)
        return [_group(row, members.get(row["id"], ())) for row in rows]

    def get_group(self, group_id: str) -> Group | None:
        """依識別碼找群組，找不到時為 None。"""
        return next(
            (item for item in self.list_groups() if item.id == group_id), None
        )

    def create_group(
        self,
        name: str,
        description: str = "",
        resources: dict[str, Any] | None = None,
        members: list[str] | None = None,
    ) -> Group:
        """建立群組。沒有給的資源類別預設是空的（什麼都不能用）。

        Raises:
            GroupError: 名稱空白、太長或重複，或資源與成員的格式不對。
        """
        resources = resources or {}
        values = {kind: _encode(resources.get(kind, [])) for kind in KINDS}
        group_id = uuid.uuid4().hex[:12]
        with self._connect() as db:
            try:
                db.execute(
                    "INSERT INTO access_groups (id, name, description, "
                    f"{', '.join(KINDS)}, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        group_id,
                        _checked_name(name),
                        _checked_description(description),
                        *(values[kind] for kind in KINDS),
                        self._clock(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise GroupError("已經有同名的群組") from error
            _replace_members(db, group_id, members or [])
        group = self.get_group(group_id)
        assert group is not None
        return group

    def update_group(self, group_id: str, changes: dict[str, Any]) -> Group:
        """修改名稱、說明、資源或成員；只改有給的欄位。

        「所有人」可以改說明與資源，不能改名稱與成員。

        Raises:
            GroupError: 群組不存在、欄位格式不對或名稱重複。
        """
        current = self._require(group_id)
        sets: list[str] = []
        params: list[Any] = []
        if "name" in changes:
            name = _checked_name(changes["name"])
            if current.builtin and name != current.name:
                raise GroupError("「所有人」不能改名稱")
            sets.append("name = ?")
            params.append(name)
        if "description" in changes:
            sets.append("description = ?")
            params.append(_checked_description(changes["description"]))
        for kind in KINDS:
            if kind in changes:
                sets.append(f"{kind} = ?")
                params.append(_encode(changes[kind]))
        members = changes.get("members")
        if members is not None and current.builtin:
            raise GroupError("每位成員都屬於「所有人」，不能調整")
        with self._connect() as db:
            if sets:
                try:
                    db.execute(
                        f"UPDATE access_groups SET {', '.join(sets)} "
                        "WHERE id = ?",
                        (*params, group_id),
                    )
                except sqlite3.IntegrityError as error:
                    raise GroupError("已經有同名的群組") from error
            if members is not None:
                _replace_members(db, group_id, members)
        return self._require(group_id)

    def delete_group(self, group_id: str) -> None:
        """刪除群組（成員本身不受影響）。「所有人」不能刪。"""
        group = self._require(group_id)
        if group.builtin:
            raise GroupError("「所有人」不能刪除")
        with self._connect() as db:
            db.execute(
                "DELETE FROM group_members WHERE group_id = ?", (group_id,)
            )
            db.execute("DELETE FROM access_groups WHERE id = ?", (group_id,))

    def groups_of(self, user_id: str) -> list[Group]:
        """這個人所屬的群組，包含「所有人」。"""
        return [
            item
            for item in self.list_groups()
            if item.builtin or user_id in item.members
        ]

    def access_for(self, user: accounts_lib.User) -> Access:
        """這個人可以使用的資源：所屬群組的聯集；管理者不受限制。"""
        if user.is_admin:
            return FULL_ACCESS
        return union(self.groups_of(user.id))

    def _require(self, group_id: str) -> Group:
        group = self.get_group(group_id)
        if group is None:
            raise GroupError("找不到這個群組", 404)
        return group

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return accounts_lib._Transaction(db)


def union(groups: list[Group]) -> Access:
    """好幾個群組合起來能用的資源：任何一個是全部就是全部。"""
    values: dict[str, frozenset[str] | None] = {}
    for kind in KINDS:
        names: set[str] = set()
        everything = False
        for group in groups:
            value = getattr(group, kind)
            if value is None:
                everything = True
                break
            names.update(value)
        values[kind] = None if everything else frozenset(names)
    return Access(**values)


def _members(db: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for row in db.execute(
        "SELECT group_id, user_id FROM group_members ORDER BY user_id"
    ):
        found.setdefault(row["group_id"], []).append(row["user_id"])
    return {key: tuple(value) for key, value in found.items()}


def _replace_members(
    db: sqlite3.Connection, group_id: str, members: Any
) -> None:
    ids = _checked_list(members, "members")
    db.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
    db.executemany(
        "INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?, ?)",
        [(group_id, user_id) for user_id in ids],
    )


def _group(row: sqlite3.Row, members: tuple[str, ...]) -> Group:
    return Group(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        models=_decode(row["models"]),
        agents=_decode(row["agents"]),
        toolsets=_decode(row["toolsets"]),
        knowledge_bases=_decode(row["knowledge_bases"]),
        knowledge=_decode(row["knowledge"]),
        members=() if row["id"] == EVERYONE_ID else members,
        created_at=row["created_at"],
    )


def _encode(value: Any) -> str | None:
    """資源清單存成 JSON；None 表示全部。"""
    if value is None:
        return None
    return json.dumps(_checked_list(value, "資源"), ensure_ascii=False)


def _decode(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    return tuple(json.loads(value))


def _checked_list(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise GroupError(f"{what}必須是字串清單")
    if len(value) > _LIST_LIMIT:
        raise GroupError(f"{what}最多 {_LIST_LIMIT} 項")
    # 去掉空白與重複，保留順序。
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _checked_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise GroupError("請輸入群組名稱")
    name = name.strip()
    if len(name) > _NAME_LIMIT:
        raise GroupError(f"群組名稱最多 {_NAME_LIMIT} 個字")
    return name


def _checked_description(description: Any) -> str:
    if description is None:
        return ""
    if not isinstance(description, str):
        raise GroupError("說明必須是文字")
    description = description.strip()
    if len(description) > _DESCRIPTION_LIMIT:
        raise GroupError(f"說明最多 {_DESCRIPTION_LIMIT} 個字")
    return description
