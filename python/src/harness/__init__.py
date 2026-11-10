"""Harness：把既有 Python 專案的功能包成工具，交給聊天 agent 使用。

最小範例：
    import harness

    @harness.tool
    def add(a: int, b: int) -> int:
        '''把兩個整數相加。'''
        return a + b

    agent = harness.Agent(name="calc", instructions="你是計算機。", tools=[add])
    result = harness.run_sync(agent, "1 + 2 是多少？")
    print(result.output)
"""

from harness.agents import Agent
from harness.datatypes import Event
from harness.datatypes import ModelResponse
from harness.datatypes import RunContext
from harness.datatypes import RunResult
from harness.datatypes import ToolCall
from harness.datatypes import ToolResult
from harness.datatypes import Usage
from harness.hooks import Hooks
from harness.hooks import allow
from harness.hooks import deny
from harness.loop import run
from harness.loop import run_stream
from harness.loop import run_sync
from harness.permissions import Permissions
from harness.project import Project
from harness.project import load_project
from harness.sessions import Session
from harness.tools import Tool
from harness.tools import ToolError
from harness.tools import tool

__all__ = [
    "Agent",
    "Event",
    "Hooks",
    "ModelResponse",
    "Permissions",
    "Project",
    "RunContext",
    "RunResult",
    "Session",
    "Tool",
    "ToolCall",
    "ToolError",
    "ToolResult",
    "Usage",
    "allow",
    "deny",
    "load_project",
    "run",
    "run_stream",
    "run_sync",
    "tool",
]
