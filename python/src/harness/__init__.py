"""Agent Harness 的 Python 實作：完全開源、不綁定模型供應商的 agent harness。

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
from harness.compaction import Compaction
from harness.compaction import CompactionResult
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
from harness.knowledge import Knowledge
from harness.loop import compact
from harness.loop import run
from harness.loop import run_stream
from harness.loop import run_sync
from harness.mcp_servers import McpConnection
from harness.mcp_servers import connect as connect_mcp_server
from harness.memory import MemoryStore
from harness.permissions import Permissions
from harness.project import Project
from harness.project import load_project
from harness.sessions import Session
from harness.skills import Skill
from harness.tools import Tool
from harness.tools import ToolError
from harness.tools import tool
from harness.vectorstore import SqliteVectorStore
from harness.vectorstore import VectorStore

__all__ = [
    "Agent",
    "Compaction",
    "CompactionResult",
    "Event",
    "Hooks",
    "Knowledge",
    "McpConnection",
    "MemoryStore",
    "ModelResponse",
    "Permissions",
    "Project",
    "RunContext",
    "RunResult",
    "Session",
    "Skill",
    "SqliteVectorStore",
    "Tool",
    "ToolCall",
    "ToolError",
    "ToolResult",
    "Usage",
    "VectorStore",
    "allow",
    "compact",
    "connect_mcp_server",
    "deny",
    "load_project",
    "run",
    "run_stream",
    "run_sync",
    "tool",
]
