"""Session：一段對話的紀錄，可以存成 JSONL 檔並在之後接續。

紀錄只會往後附加、不會改寫，每一行是一則 OpenAI 格式的訊息。system
prompt 不存進紀錄，每次執行時由 agent 重新組合。
"""

import datetime
import json
import pathlib
import uuid

from harness import datatypes


class Session:
    """一段對話。path 為 None 時只存在記憶體中。

    Attributes:
        id: session 識別碼。
        path: JSONL 檔案路徑。
        messages: 目前為止的所有訊息。
    """

    def __init__(
        self,
        session_id: str | None = None,
        path: pathlib.Path | None = None,
        messages: list[datatypes.Message] | None = None,
    ) -> None:
        self.id = session_id or new_id()
        self.path = path
        self.messages = messages if messages is not None else []

    def append(self, message: datatypes.Message) -> None:
        """加入一則訊息，有檔案路徑時同步寫入。

        Args:
            message: OpenAI 格式的訊息。
        """
        self.messages.append(message)
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(message, ensure_ascii=False) + "\n")


def new_id() -> str:
    """產生可依時間排序的 session 識別碼。

    Returns:
        形如 20260101-120000-ab12cd 的字串。
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def create(directory: pathlib.Path) -> Session:
    """建立一個會寫入檔案的新 session。

    Args:
        directory: 存放 session 檔案的資料夾。

    Returns:
        新的 Session。
    """
    session_id = new_id()
    return Session(session_id, pathlib.Path(directory) / f"{session_id}.jsonl")


def load(directory: pathlib.Path, session_id: str) -> Session:
    """讀取既有的 session。

    Args:
        directory: 存放 session 檔案的資料夾。
        session_id: 要讀取的 session 識別碼。

    Returns:
        載入訊息後的 Session。

    Raises:
        FileNotFoundError: 找不到這個 session。
    """
    path = pathlib.Path(directory) / f"{session_id}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"找不到 session：{session_id}")
    with path.open(encoding="utf-8") as file:
        messages = [json.loads(line) for line in file if line.strip()]
    return Session(session_id, path, messages)


def list_ids(directory: pathlib.Path) -> list[str]:
    """列出資料夾裡所有的 session 識別碼，由舊到新。

    Args:
        directory: 存放 session 檔案的資料夾。

    Returns:
        session 識別碼列表；資料夾不存在時為空列表。
    """
    directory = pathlib.Path(directory)
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.jsonl"))


def latest(directory: pathlib.Path) -> Session | None:
    """讀取最新的 session。

    Args:
        directory: 存放 session 檔案的資料夾。

    Returns:
        最新的 Session；沒有任何 session 時為 None。
    """
    ids = list_ids(directory)
    if not ids:
        return None
    return load(directory, ids[-1])
