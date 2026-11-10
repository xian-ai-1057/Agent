"""跨對話的長期記憶。

使用者說「請記住……」時，agent 用 remember 工具存下來；之後每段新的
對話都會把記憶放進 system prompt。記憶存成一個 JSON 檔，使用者可以用
`pyharness memory` 查看、刪除或清空，不想用時在 harness.yaml 關掉即可。

記憶屬於使用者而不是 agent，所以在呼叫 run 時傳入：

    store = memory.MemoryStore(f"data/memory/{user_id}.json")
    await harness.run(agent, text, session=session, memory=store)

每個使用者用自己的檔案，記憶就不會混在一起。
"""

import dataclasses
import datetime
import json
import pathlib
import uuid
from typing import Any

from harness import tools as tools_lib

MAX_ITEMS = 100
MAX_CHARS = 500


@dataclasses.dataclass
class MemoryItem:
    """一則記憶。

    Attributes:
        id: 識別碼，刪除時使用。
        content: 記憶內容。
        created_at: 建立時間（ISO 8601）。
    """

    id: str
    content: str
    created_at: str


class MemoryStore:
    """存在一個 JSON 檔裡的記憶。

    每次讀寫都直接操作檔案，所以 CLI 與正在執行的 agent 看到的內容
    一致。

    Attributes:
        path: JSON 檔案路徑。
    """

    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = pathlib.Path(path)

    def items(self) -> list[MemoryItem]:
        """列出所有記憶，由舊到新。

        Returns:
            記憶列表；檔案不存在時為空列表。
        """
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        return [MemoryItem(**item) for item in raw]

    def add(self, content: str) -> MemoryItem:
        """新增一則記憶。

        Args:
            content: 記憶內容。

        Returns:
            新增的記憶。

        Raises:
            ValueError: 內容是空的、太長，或記憶已達上限。
        """
        content = content.strip()
        if not content:
            raise ValueError("記憶內容不能是空的")
        if len(content) > MAX_CHARS:
            raise ValueError(f"一則記憶最多 {MAX_CHARS} 個字，請濃縮後再存")
        items = self.items()
        if len(items) >= MAX_ITEMS:
            raise ValueError(
                f"記憶已達上限 {MAX_ITEMS} 則，請先刪除不需要的記憶"
            )
        item = MemoryItem(
            id=uuid.uuid4().hex[:8],
            content=content,
            created_at=datetime.datetime.now().isoformat(timespec="seconds"),
        )
        self._save([*items, item])
        return item

    def remove(self, memory_id: str) -> MemoryItem:
        """刪除一則記憶。

        Args:
            memory_id: 要刪除的記憶識別碼。

        Returns:
            被刪除的記憶。

        Raises:
            KeyError: 找不到這則記憶。
        """
        items = self.items()
        for item in items:
            if item.id == memory_id:
                self._save([other for other in items if other is not item])
                return item
        raise KeyError(memory_id)

    def clear(self) -> int:
        """刪除所有記憶。

        Returns:
            刪除的數量。
        """
        count = len(self.items())
        if self.path.exists():
            self.path.unlink()
        return count

    def _save(self, items: list[MemoryItem]) -> None:
        """寫回檔案。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data: list[dict[str, Any]] = [dataclasses.asdict(i) for i in items]
        text = json.dumps(data, ensure_ascii=False, indent=2)
        self.path.write_text(text, encoding="utf-8")


def prompt_section(store: MemoryStore) -> str:
    """產生要接在 system prompt 後面的記憶說明。

    Args:
        store: 使用者的記憶。

    Returns:
        包含使用方式與目前所有記憶的文字。
    """
    lines = [
        "# 長期記憶",
        "",
        "你可以跨對話記住使用者的偏好與事實。使用者要求你記住某件事，"
        "或透露了之後顯然用得到的長期偏好時，用 remember 存下來；使用者"
        "要求忘記、或記憶已經過時時，用 forget 刪除。不要記住密碼、"
        "金鑰等機密。",
        "",
    ]
    items = store.items()
    if not items:
        lines.append("目前沒有任何記憶。")
    else:
        lines.append("目前記得的事（[識別碼] 內容）：")
        lines += [f"- [{item.id}] {item.content}" for item in items]
    return "\n".join(lines)


def make_tools(store: MemoryStore) -> list[tools_lib.Tool]:
    """建立操作記憶的工具。

    Args:
        store: 使用者的記憶。

    Returns:
        remember 與 forget 兩個工具。
    """

    def remember(content: str) -> str:
        """記住一件事，之後的對話也會記得。

        Args:
            content: 要記住的內容，寫成一句完整、不需要上下文就看得懂
                的話，例如「使用者偏好用表格呈現數字」。
        """
        try:
            item = store.add(content)
        except ValueError as error:
            raise tools_lib.ToolError(str(error)) from error
        return f"已記住（識別碼 {item.id}）：{item.content}"

    def forget(memory_id: str) -> str:
        """刪除一則記憶。

        Args:
            memory_id: 記憶的識別碼（system prompt 記憶列表中方括號裡
                的值）。
        """
        try:
            item = store.remove(memory_id)
        except KeyError as error:
            raise tools_lib.ToolError(
                f"找不到識別碼為 {memory_id} 的記憶"
            ) from error
        return f"已忘記：{item.content}"

    return [tools_lib.tool(remember), tools_lib.tool(forget)]
