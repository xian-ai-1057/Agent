"""把多人帳號之前的資料交給第一位管理者。

加入帳號之前（以及現在的本機模式），網頁版和 CLI 共用一份資料：

    .harness/sessions/        對話紀錄
    .harness/memory.json      長期記憶
    workspace/ 與專案裡的檔案  對話裡提到的檔案，路徑相對於專案根目錄

本機模式（不必登入）一直用這些位置，不搬任何東西，`pyharness chat`、
`pyharness memory` 與 TypeScript 版看到的是同一份。

帳號模式每個人的資料分開存放：

    .harness/users/<識別碼>/sessions/      對話紀錄
    .harness/users/<識別碼>/memory.json    長期記憶
    private/<識別碼>/                      「我的檔案」

帳號模式第一次啟動時（第一次設定密碼或建立帳號，包括從本機模式改成
帳號模式），伺服器呼叫 migrate，把舊的對話紀錄與記憶搬給第一位管理者，
之後不再搬（記在 .harness/users/migrated.json）。搬過去的每段對話都把
工作資料夾設成「舊資料（專案資料夾）」（folders.LEGACY_ID）：這個資料
夾就是專案根目錄，只有這位管理者看得到，所以舊對話裡的檔案（附件、
workspace/ 裡的圖表、報告）都照原本的路徑找得到。檔案本身不搬，也不會
分享給其他成員。

搬移只用改名（同一個檔案系統），不覆蓋任何檔案：目的地已經有同名的
檔案時留在原處，所以重複執行也是安全的。
"""

import datetime
import json
import os
import pathlib

from harness import project as project_lib
from harness.web import accounts as accounts_lib
from harness.web import folders as folders_lib

MARKER = accounts_lib.USERS_DIR / "migrated.json"
_LEGACY_SESSIONS = project_lib.SESSIONS_DIR
_LEGACY_MEMORY = project_lib.MEMORY_FILE
# 標記裡記下看得到「舊資料」資料夾的人。
LEGACY_KEY = "legacyFolder"


def user_dir(root: pathlib.Path, user_id: str) -> pathlib.Path:
    """一個人存放對話紀錄、記憶與權限的資料夾。"""
    return root / accounts_lib.USERS_DIR / user_id


def legacy_owner(root: pathlib.Path) -> str | None:
    """看得到「舊資料（專案資料夾）」的使用者；沒有搬過舊資料時為 None。"""
    try:
        data = json.loads((root / MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    owner = data.get(LEGACY_KEY) if isinstance(data, dict) else None
    return owner if isinstance(owner, str) and owner else None


def migrate(root: pathlib.Path, owner_id: str) -> list[str]:
    """把舊資料交給 owner_id（帳號模式才呼叫）；可以重複呼叫。

    Args:
        root: 專案根目錄。
        owner_id: 接收舊資料的使用者識別碼（第一位管理者）。

    Returns:
        給管理者看的說明，每一項是一個搬過去的位置；沒有搬任何東西時
        是空的。
    """
    root = root.resolve()
    moved: list[str] = []
    marker = root / MARKER
    target = user_dir(root, owner_id)
    if not marker.exists():
        sessions = root / _LEGACY_SESSIONS
        ids = _session_ids(sessions)
        _move(sessions, target / "sessions", moved, root)
        _move(root / _LEGACY_MEMORY, target / "memory.json", moved, root)
        # 搬過去的對話都在「舊資料」資料夾裡，路徑照舊相對於專案根目錄。
        for session_id in ids:
            if not (sessions / f"{session_id}.jsonl").exists():
                _tag_legacy(target / "sessions", session_id)
        record = {
            "owner": owner_id,
            "migratedAt": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        if moved:
            record[LEGACY_KEY] = owner_id
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(record, ensure_ascii=False), "utf-8")
    if owner_id != accounts_lib.LOCAL_ID:
        # 這個功能開發期間的版本，本機模式把資料放在 users/local/ 與
        # private/local/；一併交給第一位管理者。
        local = accounts_lib.LOCAL_ID
        _move(user_dir(root, local), target, moved, root)
        private = root / folders_lib.PRIVATE_DIR
        _move(private / local, private / owner_id, moved, root)
    return moved


def _session_ids(directory: pathlib.Path) -> list[str]:
    if not directory.is_dir() or directory.is_symlink():
        return []
    return [path.stem for path in directory.glob("*.jsonl")]


def _tag_legacy(directory: pathlib.Path, session_id: str) -> None:
    """把對話的工作資料夾設成「舊資料」（寫進 <id>.meta.json）。"""
    path = directory / f"{session_id}.meta.json"
    try:
        meta = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    options = meta.get("options")
    if not isinstance(options, dict):
        options = {}
    options["folder"] = folders_lib.LEGACY_ID
    meta["options"] = options
    path.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")


def _move(
    source: pathlib.Path,
    target: pathlib.Path,
    moved: list[str],
    root: pathlib.Path,
) -> None:
    """把 source 搬到 target；兩邊都是資料夾時逐項合併，不覆蓋任何檔案。"""
    if not source.exists() or source.is_symlink():
        return
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        moved.append(source.relative_to(root).as_posix())
        return
    if not (source.is_dir() and target.is_dir()):
        return
    for child in list(source.iterdir()):
        _move(child, target / child.name, moved, root)
    try:
        source.rmdir()
    except OSError:
        # 有同名而沒搬的檔案，留在原處。
        pass
