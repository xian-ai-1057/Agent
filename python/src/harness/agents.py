"""Agent 的設定。"""

import dataclasses
from typing import Any

from harness import tools as tools_lib


# eq=False：agent 之間可以互相轉交而形成循環，所以用物件本身比較。
@dataclasses.dataclass(eq=False)
class Agent:
    """一個 agent：一段指示加上一組可用的工具。

    Attributes:
        name: agent 名稱。
        instructions: system prompt。
        tools: 這個 agent 可以使用的工具。
        model: litellm 的模型名稱，None 表示使用預設模型。
        model_params: 額外傳給 litellm 的參數（max_tokens…）。
        max_turns: 一次 run 最多呼叫模型幾次，避免無限迴圈。
        description: 用途說明。這個 agent 被當成 subagent 或 handoff 的
            對象時，其他 agent 的模型靠這段說明決定要不要找它。
        subagents: 可以把工作交給哪些 agent（對方看不到目前的對話）。
        handoffs: 可以把整段對話轉交給哪些 agent。
    """

    name: str
    instructions: str = ""
    tools: list[tools_lib.Tool] = dataclasses.field(default_factory=list)
    model: str | None = None
    model_params: dict[str, Any] = dataclasses.field(default_factory=dict)
    max_turns: int = 20
    description: str = ""
    subagents: list["Agent"] = dataclasses.field(default_factory=list)
    handoffs: list["Agent"] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        """檢查工具名稱沒有重複。"""
        names = [item.name for item in self.tools]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(
                f"agent {self.name} 有重複的工具名稱：{duplicates}"
            )
