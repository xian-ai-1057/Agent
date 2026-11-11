"""多個 agent 協作：subagents 與 handoffs。

兩者都是以「工具」的形式提供給模型，差別在於對話歸誰：

    subagent  主 agent 把一件工作交出去。對方在全新的對話裡做完，只把
              最後的回覆當成工具結果傳回來，之後仍由主 agent 回答使用者。
              對方看不到目前的對話。
    handoff   把整段對話交出去。對方看得到完整的對話，直接回答使用者，
              之後的訊息也由它處理。

目前由誰負責對話不另外存檔，而是從對話紀錄裡最後一次成功的轉交推算，
所以每次都傳入同一個起始 agent 即可。
"""

import re
from collections.abc import Awaitable
from collections.abc import Callable

from harness import agents
from harness import datatypes
from harness import tools as tools_lib

SUBAGENT_PREFIX = "ask_"
HANDOFF_PREFIX = "transfer_to_"
MAX_SUBAGENT_DEPTH = 3
ERROR_PREFIX = "[錯誤] "

_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")

SubagentRunner = Callable[[agents.Agent, str], Awaitable[str]]


def subagent_tool_name(target: agents.Agent) -> str:
    """把工作交給某個 subagent 的工具名稱。

    Args:
        target: 被呼叫的 agent。

    Returns:
        形如 ask_researcher 的工具名稱。
    """
    return f"{SUBAGENT_PREFIX}{_safe_name(target.name)}"


def handoff_tool_name(target: agents.Agent) -> str:
    """把對話轉交給某個 agent 的工具名稱。

    Args:
        target: 接手對話的 agent。

    Returns:
        形如 transfer_to_billing 的工具名稱。
    """
    return f"{HANDOFF_PREFIX}{_safe_name(target.name)}"


def make_subagent_tool(
    target: agents.Agent, runner: SubagentRunner
) -> tools_lib.Tool:
    """建立「把工作交給 subagent」的工具。

    Args:
        target: 被呼叫的 agent。
        runner: 實際執行 subagent 的函式，由主迴圈提供。

    Returns:
        給主 agent 使用的工具。
    """

    async def delegate(task: str) -> str:
        """把工作交給另一個 agent。

        Args:
            task: 要它完成的工作。它看不到目前的對話，所以要寫清楚
                所有需要的背景、資料與期望的輸出。
        """
        return await runner(target, task)

    description = (
        f"把一件工作交給 {target.name}，並取得它完成後的回覆。"
        f"{_about(target)}它看不到目前的對話，只會收到你寫的 task。"
    )
    return tools_lib.tool(
        delegate, name=subagent_tool_name(target), description=description
    )


def make_handoff_tool(target: agents.Agent) -> tools_lib.Tool:
    """建立「把對話轉交給另一個 agent」的工具。

    工具本身只回報已轉交；實際的切換由主迴圈在工具執行成功後進行。

    Args:
        target: 接手對話的 agent。

    Returns:
        給目前的 agent 使用的工具。
    """

    def transfer() -> str:
        """把對話轉交給另一個 agent。"""
        return f"已轉交給 {target.name}，接下來由它回覆使用者。"

    description = (
        f"把這段對話轉交給 {target.name}，由它接手回覆使用者。"
        f"{_about(target)}它看得到完整的對話，轉交後你不需要再回覆。"
    )
    return tools_lib.tool(
        transfer, name=handoff_tool_name(target), description=description
    )


def reachable_agents(entry: agents.Agent) -> list[agents.Agent]:
    """列出從起始 agent 沿著 handoffs 可以到達的所有 agent。

    Args:
        entry: 起始 agent。

    Returns:
        包含起始 agent 在內的 agent 列表，可以處理互相轉交的循環。
    """
    found: list[agents.Agent] = []
    pending = [entry]
    while pending:
        current = pending.pop(0)
        if any(current is item for item in found):
            continue
        found.append(current)
        pending.extend(current.handoffs)
    return found


def active_agent(
    entry: agents.Agent, messages: list[datatypes.Message]
) -> agents.Agent:
    """依對話紀錄判斷目前由哪個 agent 負責對話。

    Args:
        entry: 起始 agent。
        messages: 目前為止的對話紀錄。

    Returns:
        最後一次成功轉交的對象；沒有轉交過，或該 agent 已經不存在時，
        回傳起始 agent。同名時以起始 agent 為準，所以傳入調整過設定的
        副本（例如網頁版每段對話自己選的模型）時，接手的仍是這個副本。
    """
    targets: dict[str, agents.Agent] = {}
    for item in reachable_agents(entry):
        targets.setdefault(handoff_tool_name(item), item)
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        name = message.get("name") or ""
        if not name.startswith(HANDOFF_PREFIX):
            continue
        if str(message.get("content") or "").startswith(ERROR_PREFIX):
            continue
        return targets.get(name, entry)
    return entry


def _safe_name(name: str) -> str:
    """把 agent 名稱轉成模型端點接受的工具名稱字元。"""
    return _INVALID_NAME_CHARS.sub("_", name)


def _about(target: agents.Agent) -> str:
    """給模型看的 agent 用途說明。"""
    if not target.description:
        return ""
    return f"它的用途：{target.description.rstrip('。')}。"
