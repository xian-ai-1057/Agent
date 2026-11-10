"""工具的執行權限。

判斷順序：deny 規則 → allow 規則 → 模式。
    ask:     需要同意的工具要先問使用者（預設）。
    bypass:  全部直接執行。
    dontAsk: 不問使用者，需要同意的工具一律拒絕（適合無人值守）。

「需要同意的工具」是指定義時標記 needs_approval 的工具，或名稱符合
ask 規則的工具。規則是工具名稱的萬用字元樣式，例如 "send_*"。

模式名稱與 harness.yaml 的寫法一致，TypeScript 版也使用同一份設定。
"""

import dataclasses
import fnmatch
from collections.abc import Awaitable
from collections.abc import Callable

from harness import datatypes
from harness import tools as tools_lib

MODES = ("ask", "bypass", "dontAsk")

Approver = Callable[[datatypes.ToolCall, tools_lib.Tool], Awaitable[bool]]


@dataclasses.dataclass
class Permissions:
    """權限設定。

    Attributes:
        mode: ask、bypass 或 dontAsk。
        allow: 不需詢問就能執行的工具名稱樣式。
        deny: 一律拒絕的工具名稱樣式。
        ask: 執行前要先詢問的工具名稱樣式。
        approver: 詢問使用者的函式，回傳 True 表示同意。
    """

    mode: str = "ask"
    allow: list[str] = dataclasses.field(default_factory=list)
    deny: list[str] = dataclasses.field(default_factory=list)
    ask: list[str] = dataclasses.field(default_factory=list)
    approver: Approver | None = None

    def __post_init__(self) -> None:
        """檢查模式名稱。"""
        if self.mode not in MODES:
            raise ValueError(f"權限模式必須是 {MODES} 之一：{self.mode}")


def check(permissions: Permissions, target: tools_lib.Tool) -> str:
    """判斷一個工具能不能執行（不詢問使用者）。

    Args:
        permissions: 權限設定。
        target: 要執行的工具。

    Returns:
        "allow"、"deny" 或 "ask"。
    """
    if _matches(target.name, permissions.deny):
        return "deny"
    if _matches(target.name, permissions.allow):
        return "allow"
    if permissions.mode == "bypass":
        return "allow"
    sensitive = target.needs_approval or _matches(target.name, permissions.ask)
    if not sensitive:
        return "allow"
    return "deny" if permissions.mode == "dontAsk" else "ask"


async def ask(
    permissions: Permissions,
    call: datatypes.ToolCall,
    target: tools_lib.Tool,
) -> bool:
    """詢問使用者是否同意這次工具呼叫。

    Args:
        permissions: 權限設定。
        call: 這次工具呼叫。
        target: 要執行的工具。

    Returns:
        使用者是否同意；沒有設定 approver 時視為不同意。
    """
    if permissions.approver is None:
        return False
    return await permissions.approver(call, target)


def _matches(name: str, patterns: list[str]) -> bool:
    """工具名稱是否符合任何一個樣式。"""
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
