"""把多人帳號之前的資料交給第一位管理者（或本機使用者）。

加入帳號之前，網頁版只有一份資料：

    .harness/sessions/        對話紀錄
    .harness/memory.json      長期記憶
    workspace/uploads/        上傳的檔案（有設定沙盒時在 sandbox.workspace）

現在每個人的資料分開存放：

    .harness/users/<識別碼>/sessions/      對話紀錄
    .harness/users/<識別碼>/memory.json    長期記憶
    private/<識別碼>/                      「我的檔案」

伺服器啟動時呼叫 migrate：第一次啟動時把舊資料搬給「擁有者」（本機
模式是本機使用者，帳號模式是最早建立的管理者），之後不再搬（記在
.harness/users/migrated.json）。上傳的檔案搬到擁有者「我的檔案」裡
同樣的相對位置（例如 private/<識別碼>/workspace/uploads/），舊對話裡的
附件路徑因此仍然找得到。workspace/ 裡其他檔案（沙盒產生的圖表等）留在
原處，也就是預設的「共用」資料夾。

從本機模式改成帳號模式（設定密碼或建立帳號）後，本機使用者的資料
（.harness/users/local/ 與 private/local/）也會交給第一位管理者。

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
from harness.web import uploads as uploads_lib

MARKER = accounts_lib.USERS_DIR / "migrated.json"
_LEGACY_SESSIONS = project_lib.SESSIONS_DIR
_LEGACY_MEMORY = project_lib.MEMORY_FILE


def user_dir(root: pathlib.Path, user_id: str) -> pathlib.Path:
    """一個人存放對話紀錄、記憶與權限的資料夾。"""
    return root / accounts_lib.USERS_DIR / user_id


def migrate(
    root: pathlib.Path, owner_id: str, legacy_workspace: pathlib.Path
) -> list[str]:
    """把舊資料交給 owner_id；可以重複呼叫。

    Args:
        root: 專案根目錄。
        owner_id: 接收舊資料的使用者識別碼。
        legacy_workspace: 舊版上傳檔案所在的資料夾（底下的 uploads/）。

    Returns:
        給管理者看的說明，每一項是一個搬過去的位置；沒有搬任何東西時
        是空的。
    """
    root = root.resolve()
    moved: list[str] = []
    marker = root / MARKER
    target = user_dir(root, owner_id)
    if not marker.exists():
        _move(root / _LEGACY_SESSIONS, target / "sessions", moved, root)
        _move(root / _LEGACY_MEMORY, target / "memory.json", moved, root)
        legacy = legacy_workspace.resolve()
        if legacy.is_relative_to(root) and legacy != root:
            relative = legacy.relative_to(root)
            uploads = legacy / uploads_lib.UPLOADS_DIR
            private = root / folders_lib.PRIVATE_DIR / owner_id
            destination = private / relative / uploads_lib.UPLOADS_DIR
            _move(uploads, destination, moved, root)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "owner": owner_id,
                    "migratedAt": datetime.datetime.now(
                        datetime.UTC
                    ).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    if owner_id != accounts_lib.LOCAL_ID:
        local = accounts_lib.LOCAL_ID
        _move(user_dir(root, local), target, moved, root)
        private = root / folders_lib.PRIVATE_DIR
        _move(private / local, private / owner_id, moved, root)
    return moved


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
