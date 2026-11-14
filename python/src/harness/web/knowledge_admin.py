"""網頁版的知識庫管理：維護者、harness.yaml 裡的知識庫設定與文件。

- 知識庫的名稱、說明、資料夾與檢索設定寫在 harness.yaml（預設知識庫在
  knowledge 區塊，其他在 knowledgeBases 區塊，見 project 模組），CLI 與
  網頁版共用。這裡負責把管理平台的修改寫回去。
- 維護者（可以上傳、更新、刪除文件與試搜尋的成員）存在帳號資料庫
  .harness/harness.db 的 knowledge_maintainers，和存取群組一樣只在帳號
  模式使用。管理者不必設定就能管理每個知識庫。
- 文件直接存在知識庫的資料夾裡：先寫到同一個資料夾裡的隱藏暫存檔，
  寫完再改名，搜尋時不會讀到寫到一半的檔案。
"""

import contextlib
import os
import pathlib
import re
import sqlite3
import uuid
from collections.abc import AsyncIterator
from typing import Any

from harness import knowledge as knowledge_lib
from harness import project as project_lib
from harness.web import accounts as accounts_lib
from harness.web import config as config_lib
from harness.web import uploads as uploads_lib

# 網頁上可以修改的設定（和 harness.yaml 的鍵相同）。
SETTING_KEYS = (
    "name",
    "description",
    "embeddingModel",
    "chunkSize",
    "reranker",
    "rerankCandidates",
    "minSimilarity",
    "minRerankScore",
)
_TEXT_KEYS = ("name", "description", "embeddingModel", "reranker")
_INT_KEYS = ("chunkSize", "rerankCandidates")
_FLOAT_KEYS = ("minSimilarity", "minRerankScore")
_NAME_LIMIT = 40
_DESCRIPTION_LIMIT = 300
_DIR_LIMIT = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_maintainers (
    base_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY (base_id, user_id)
);
CREATE INDEX IF NOT EXISTS knowledge_maintainers_user
    ON knowledge_maintainers (user_id);
"""


class KnowledgeAdminError(Exception):
    """管理知識庫失敗，訊息會直接顯示給使用者。

    Attributes:
        status: HTTP 狀態碼。
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class MaintainerStore:
    """知識庫的維護者，和帳號共用 .harness/harness.db。"""

    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.executescript(_SCHEMA)
            db.commit()
        finally:
            db.close()

    @classmethod
    def for_project(cls, root: str | pathlib.Path) -> "MaintainerStore":
        """專案的維護者資料庫（和帳號同一個檔案）。"""
        return cls(pathlib.Path(root) / accounts_lib.DB_FILE)

    def all(self) -> dict[str, list[str]]:
        """每個知識庫的維護者識別碼，以知識庫識別碼為鍵。"""
        found: dict[str, list[str]] = {}
        with self._connect() as db:
            rows = db.execute(
                "SELECT base_id, user_id FROM knowledge_maintainers "
                "ORDER BY base_id, user_id"
            ).fetchall()
        for base_id, user_id in rows:
            found.setdefault(base_id, []).append(user_id)
        return found

    def bases_of(self, user_id: str) -> set[str]:
        """這個人維護的知識庫。"""
        with self._connect() as db:
            rows = db.execute(
                "SELECT base_id FROM knowledge_maintainers WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {row[0] for row in rows}

    def replace(self, base_id: str, user_ids: Any) -> None:
        """把知識庫的維護者換成 user_ids。

        Raises:
            KnowledgeAdminError: user_ids 不是字串清單。
        """
        if not isinstance(user_ids, list) or not all(
            isinstance(item, str) for item in user_ids
        ):
            raise KnowledgeAdminError("maintainers 必須是字串清單")
        ids = list(dict.fromkeys(item for item in user_ids if item.strip()))
        with self._connect() as db:
            db.execute(
                "DELETE FROM knowledge_maintainers WHERE base_id = ?",
                (base_id,),
            )
            db.executemany(
                "INSERT INTO knowledge_maintainers VALUES (?, ?)",
                [(base_id, user_id) for user_id in ids],
            )

    def remove(self, base_id: str) -> None:
        """刪除知識庫時一併清掉它的維護者。"""
        self.replace(base_id, [])

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        return accounts_lib._Transaction(db)


def checked_settings(changes: Any) -> dict[str, Any]:
    """檢查管理平台送來的設定，回傳要寫進 harness.yaml 的值。

    值是 None 或空字串表示刪掉這個設定（沿用預設或 knowledge 區塊的值）。

    Raises:
        KnowledgeAdminError: 欄位或格式不對。
    """
    if not isinstance(changes, dict):
        raise KnowledgeAdminError("設定必須是物件")
    unknown = sorted(set(changes) - set(SETTING_KEYS))
    if unknown:
        raise KnowledgeAdminError(f"不認得的設定：{'、'.join(unknown)}")
    values: dict[str, Any] = {}
    for key, value in changes.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            values[key] = None
        elif key in _TEXT_KEYS:
            if not isinstance(value, str):
                raise KnowledgeAdminError(f"{key} 必須是文字")
            values[key] = value.strip()
        elif key in _INT_KEYS:
            if isinstance(value, bool) or not isinstance(value, int):
                raise KnowledgeAdminError(f"{key} 必須是整數")
            values[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise KnowledgeAdminError(f"{key} 必須是數字")
            values[key] = value
    name = values.get("name")
    if name is not None and len(name) > _NAME_LIMIT:
        raise KnowledgeAdminError(f"名稱最多 {_NAME_LIMIT} 個字")
    description = values.get("description")
    if description is not None and len(description) > _DESCRIPTION_LIMIT:
        raise KnowledgeAdminError(f"說明最多 {_DESCRIPTION_LIMIT} 個字")
    if "name" in changes and name is None:
        raise KnowledgeAdminError("請輸入知識庫名稱")
    return values


def checked_id(base_id: Any) -> str:
    """新知識庫的識別碼。

    Raises:
        KnowledgeAdminError: 格式不對或是保留的名稱。
    """
    if not isinstance(base_id, str) or not base_id.strip():
        raise KnowledgeAdminError("請輸入識別碼")
    base_id = base_id.strip()
    if not project_lib.KNOWLEDGE_ID_PATTERN.match(base_id):
        raise KnowledgeAdminError(
            "識別碼只能用小寫英文、數字、- 與 _（40 字以內），會當作資料夾名稱"
        )
    if base_id == project_lib.DEFAULT_KNOWLEDGE_ID:
        raise KnowledgeAdminError(f"{base_id} 保留給預設知識庫")
    return base_id


def write_config(
    root: pathlib.Path,
    default: dict[str, Any] | None,
    bases: dict[str, dict[str, Any]],
) -> None:
    """把知識庫設定寫回 harness.yaml 的 knowledge 與 knowledgeBases。

    只換掉這兩個區塊（區塊裡的註解會消失），其他內容保持原樣。寫入前
    先用 project.load_knowledge_bases 檢查設定能不能載入。

    Args:
        root: 專案根目錄。
        default: knowledge 區塊的新內容；None 表示不改。
        bases: knowledgeBases 區塊的新內容；空的時刪掉這個區塊。

    Raises:
        KnowledgeAdminError: 設定載入失敗，或檔案寫法特殊不能自動修改。
    """
    config = config_lib.read_config(root)
    if default is not None:
        config["knowledge"] = default
    if bases:
        config[project_lib.KNOWLEDGE_BASES_KEY] = bases
    else:
        config.pop(project_lib.KNOWLEDGE_BASES_KEY, None)
    try:
        project_lib.load_knowledge_bases(root, config)
    except ValueError as error:
        raise KnowledgeAdminError(str(error)) from error
    try:
        if default is not None:
            config_lib.save_block(root, "knowledge", default or None)
        config_lib.save_block(
            root, project_lib.KNOWLEDGE_BASES_KEY, bases or None
        )
    except ValueError as error:
        raise KnowledgeAdminError(str(error)) from error


def config_blocks(
    root: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """harness.yaml 目前的 knowledge 與 knowledgeBases（都是 dict）。"""
    config = config_lib.read_config(root)
    default = config.get("knowledge")
    bases = config.get(project_lib.KNOWLEDGE_BASES_KEY) or {}
    return (
        dict(default) if isinstance(default, dict) else {},
        {
            str(key): dict(value) if isinstance(value, dict) else {}
            for key, value in bases.items()
        }
        if isinstance(bases, dict)
        else {},
    )


def merged(current: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """把 checked_settings 的結果合併進原本的設定（None 表示刪掉）。"""
    result = dict(current)
    for key, value in values.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def list_documents(base: knowledge_lib.KnowledgeBase) -> list[dict[str, Any]]:
    """知識庫裡的文件：路徑、大小、修改時間與支不支援這種格式。"""
    knowledge = base.knowledge
    assert knowledge.path is not None
    found = []
    for relative in knowledge.files():
        try:
            info = (knowledge.path / relative).stat()
        except OSError:
            continue
        suffix = pathlib.PurePosixPath(relative).suffix.lower()
        found.append(
            {
                "path": relative,
                "size": info.st_size,
                "modifiedAt": info.st_mtime,
                "supported": suffix in knowledge.parsers,
            }
        )
    return found


async def save_document(
    base: knowledge_lib.KnowledgeBase,
    filename: str,
    chunks: AsyncIterator[bytes],
    *,
    max_bytes: int,
    directory: str = "",
    replace: str | None = None,
) -> dict[str, Any]:
    """把上傳的檔案存進知識庫的資料夾。

    Args:
        base: 知識庫。
        filename: 瀏覽器送來的檔名（會整理成安全的檔名）。
        chunks: 檔案內容。
        max_bytes: 單一檔案的大小上限。
        directory: 放在知識庫裡的哪個子資料夾（例如 hr），空字串表示
            最上層。
        replace: 要換掉的文件（相對路徑）；None 表示新增，同名的文件
            已經存在時拒絕。

    Returns:
        {"path": 相對路徑, "size": 位元組數}。

    Raises:
        KnowledgeAdminError: 路徑不對、檔案太大或是空的、同名的文件已經
            存在。
    """
    root = _root(base)
    if replace is not None:
        target = _existing(base, replace)
    else:
        name = uploads_lib.safe_name(filename)
        target = root.joinpath(*_folder_parts(directory), name)
        _check_inside(base, target)
        if target.exists() or target.is_symlink():
            raise KnowledgeAdminError(
                f"已經有 {target.relative_to(root).as_posix()}，"
                "要換掉請用文件旁邊的「更新」",
                409,
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.parent / f".upload-{uuid.uuid4().hex}.part"
    size = 0
    try:
        with open(partial, "xb") as handle:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise KnowledgeAdminError(
                        f"檔案超過上限 {max_bytes // (1024 * 1024)} MB", 413
                    )
                handle.write(chunk)
        if size == 0:
            raise KnowledgeAdminError("是空的檔案")
        os.replace(partial, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            partial.unlink()
    return {"path": target.relative_to(root).as_posix(), "size": size}


def delete_document(base: knowledge_lib.KnowledgeBase, path: str) -> None:
    """刪除知識庫裡的一份文件。

    Raises:
        KnowledgeAdminError: 找不到這份文件。
    """
    _existing(base, path).unlink()


def _root(base: knowledge_lib.KnowledgeBase) -> pathlib.Path:
    path = base.knowledge.path
    if path is None:
        raise KnowledgeAdminError("這個知識庫沒有資料夾")
    return path.resolve()


def _existing(base: knowledge_lib.KnowledgeBase, path: Any) -> pathlib.Path:
    """知識庫裡的一份文件（必須在 files() 列出的清單裡）。"""
    if not isinstance(path, str) or path not in base.knowledge.files():
        raise KnowledgeAdminError(f"知識庫裡沒有這份文件：{path}", 404)
    target = _root(base) / path
    if target.is_symlink() or not target.is_file():
        raise KnowledgeAdminError(f"知識庫裡沒有這份文件：{path}", 404)
    return target


def _folder_parts(directory: Any) -> list[str]:
    """子資料夾的每一層（整理成安全的名稱）。"""
    if directory is None or directory == "":
        return []
    if not isinstance(directory, str):
        raise KnowledgeAdminError("資料夾必須是文字")
    parts = [part for part in re.split(r"[\\/]+", directory) if part]
    if len(parts) > _DIR_LIMIT:
        raise KnowledgeAdminError(f"資料夾最多 {_DIR_LIMIT} 層")
    cleaned = []
    for part in parts:
        if part in (".", "..") or part.startswith("."):
            raise KnowledgeAdminError("資料夾名稱不能以點開頭")
        cleaned.append(uploads_lib.safe_name(part))
    return cleaned


def _check_inside(base: knowledge_lib.KnowledgeBase, target: pathlib.Path):
    """目標在知識庫資料夾裡，而且不在其他知識庫的子資料夾裡。"""
    root = _root(base)
    relative = target.relative_to(root)
    if relative.as_posix() == knowledge_lib.LABELS_FILE:
        raise KnowledgeAdminError(
            f"{knowledge_lib.LABELS_FILE} 是標籤設定，不能當成文件上傳"
        )
    for current in [root, *(root / p for p in relative.parents[-2::-1])]:
        if current.is_symlink():
            raise KnowledgeAdminError("資料夾是捷徑（symlink），不能存放檔案")
    for other in base.knowledge.exclude:
        other = other if other.is_absolute() else root / other
        if target.resolve().is_relative_to(other.resolve()):
            raise KnowledgeAdminError(
                "這個資料夾屬於另一個知識庫，請到那個知識庫上傳"
            )


def describe_settings(spec: dict[str, Any]) -> dict[str, Any]:
    """harness.yaml 裡寫的設定（只留網頁上可以改的鍵）。"""
    return {key: spec[key] for key in SETTING_KEYS if key in spec}
