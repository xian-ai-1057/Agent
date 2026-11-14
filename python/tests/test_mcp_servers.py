"""連接 MCP 伺服器：啟動、列出工具、轉送呼叫、錯誤與關閉。"""

import asyncio
import socket
import subprocess
import sys
import time

import pytest

import harness
from harness import mcp_servers
from tests import conftest

pytest.importorskip("mcp")

SERVER = '''\
import asyncio
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

mcp = MCPServer("demo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """把兩個整數相加。

    Args:
        a: 第一個數。
        b: 第二個數。
    """
    return a + b


@mcp.tool()
def summary(month: str) -> str:
    """取得某個月份的摘要。"""
    if month == "1999-01":
        raise ToolError(f"{month} 沒有資料")
    return f"{month} 的摘要"


@mcp.tool()
async def slow(seconds: float) -> str:
    """等一段時間再回傳。"""
    await asyncio.sleep(seconds)
    return "完成"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_everything() -> str:
    """刪除全部資料。"""
    return "已刪除"


print("demo server started", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "http":
        mcp.run("streamable-http", port=int(sys.argv[2]))
    else:
        mcp.run()
'''


@pytest.fixture
def server_script(tmp_path):
    """寫出示範用的 MCP 伺服器，回傳檔案路徑。"""
    path = tmp_path / "demo_server.py"
    path.write_text(SERVER, encoding="utf-8")
    return path


@pytest.fixture
def demo(server_script):
    """連上示範伺服器，測試結束時關閉。"""
    with mcp_servers.connect(
        "demo", command=sys.executable, args=[str(server_script)]
    ) as connection:
        yield connection


def _tool(connection, name):
    return next(item for item in connection.tools if item.name == name)


def test_connect_lists_server_tools(demo):
    names = [item.name for item in demo.tools]

    assert names == ["add", "summary", "slow", "delete_everything"]
    add = _tool(demo, "add")
    assert add.description.startswith("把兩個整數相加")
    assert add.parameters["required"] == ["a", "b"]
    assert add.to_schema()["function"]["name"] == "add"


def test_destructive_tools_need_approval(demo):
    assert _tool(demo, "delete_everything").needs_approval is True
    assert _tool(demo, "add").needs_approval is False


async def test_call_forwards_arguments_and_returns_text(demo, context):
    output = await harness.tools.invoke(
        _tool(demo, "add"), {"a": 2, "b": 3}, context
    )

    assert output == "5"


async def test_tool_error_from_server_becomes_tool_error(demo):
    with pytest.raises(harness.ToolError, match="1999-01 沒有資料"):
        await _tool(demo, "summary").func(month="1999-01")


def test_connection_works_across_event_loops(demo):
    # 命令列與網頁服務可能每次請求各自 asyncio.run，連線要能共用。
    add = _tool(demo, "add")

    assert asyncio.run(add.func(a=1, b=1)) == "2"
    assert asyncio.run(add.func(a=2, b=2)) == "4"


async def test_cancelling_the_caller_cancels_the_call(demo):
    task = asyncio.create_task(_tool(demo, "slow").func(seconds=30))
    await asyncio.sleep(0.3)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert await _tool(demo, "add").func(a=1, b=2) == "3"


async def test_slow_call_times_out(server_script):
    with mcp_servers.connect(
        "demo",
        command=sys.executable,
        args=[str(server_script)],
        timeout_seconds=0.5,
    ) as connection:
        started = time.monotonic()
        with pytest.raises(Exception):  # noqa: B017
            await _tool(connection, "slow").func(seconds=30)
        assert time.monotonic() - started < 10


async def test_calls_after_close_report_disconnection(server_script):
    connection = mcp_servers.connect(
        "demo", command=sys.executable, args=[str(server_script)]
    )
    add = _tool(connection, "add")
    connection.close()
    connection.close()  # 可以重複呼叫

    with pytest.raises(harness.ToolError, match="已經斷線"):
        await add.func(a=1, b=2)


def test_startup_failure_shows_server_stderr(tmp_path):
    script = tmp_path / "broken.py"
    script.write_text(
        "import sys\nprint('缺少設定 DB_URL', file=sys.stderr)\nsys.exit(1)\n",
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError, match="無法啟動 MCP 伺服器 broken"
    ) as info:
        mcp_servers.connect(
            "broken", command=sys.executable, args=[str(script)]
        )
    assert "缺少設定 DB_URL" in str(info.value)


def test_server_that_never_answers_times_out(tmp_path):
    script = tmp_path / "silent.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="1 秒內沒有完成連線"):
        mcp_servers.connect(
            "silent",
            command=sys.executable,
            args=[str(script)],
            startup_timeout_seconds=1,
        )
    assert time.monotonic() - started < 10


def test_unreachable_remote_server_names_the_url():
    port = _free_port()  # 沒有人在聽的連接埠

    with pytest.raises(RuntimeError, match=f"127.0.0.1:{port}/mcp"):
        mcp_servers.connect("doc", url=f"http://127.0.0.1:{port}/mcp")


def test_env_is_added_to_the_server_environment(tmp_path):
    script = tmp_path / "env_server.py"
    script.write_text(
        "import os\n"
        "from mcp.server.mcpserver import MCPServer\n"
        "mcp = MCPServer('env')\n"
        "@mcp.tool()\n"
        "def read_env() -> str:\n"
        "    '''讀取環境變數。'''\n"
        "    has_path = 'PATH' in os.environ\n"
        "    return f\"{os.environ['DEMO_SETTING']},{has_path}\"\n"
        "mcp.run()\n",
        encoding="utf-8",
    )

    with mcp_servers.connect(
        "env",
        command=sys.executable,
        args=[str(script)],
        env={"DEMO_SETTING": "abc"},
    ) as connection:
        output = asyncio.run(connection.tools[0].func())

    assert output == "abc,True"


def test_connect_requires_exactly_one_of_command_or_url():
    with pytest.raises(ValueError, match="command 或 url"):
        mcp_servers.connect("nothing")
    with pytest.raises(ValueError, match="command 或 url"):
        mcp_servers.connect("both", command="python3", url="http://x/mcp")


def test_from_config_accepts_type_copied_from_other_tools(
    server_script, tmp_path
):
    # Claude Code 的 .mcp.json 會寫 type；整段貼進 harness.yaml 也要能用。
    config = {
        "type": "stdio",
        "command": sys.executable,
        "args": [str(server_script)],
    }

    with mcp_servers.from_config("demo", config, tmp_path) as connection:
        assert _tool(connection, "add").name == "add"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"type": "sse", "url": "http://x/sse"}, "SSE"),
        ({"type": "websocket", "url": "ws://x"}, "type 不認得"),
        ({"type": "http", "command": "python3"}, "需要設定 url"),
        ({"type": "stdio", "url": "http://x/mcp"}, "需要設定 command"),
    ],
)
def test_from_config_rejects_unusable_type(tmp_path, config, message):
    with pytest.raises(ValueError, match=message):
        mcp_servers.from_config("doc", config, tmp_path)


def test_content_to_text_falls_back_to_structured_content():
    class Block:
        def __init__(self, type_, text=None):
            self.type = type_
            self.text = text

    assert (
        mcp_servers.content_to_text([Block("text", "a"), Block("image")], None)
        == "a\n[image 內容]"
    )
    assert mcp_servers.content_to_text([], {"總額": 1}) == '{"總額": 1}'
    assert mcp_servers.content_to_text([], None) == ""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_connect_to_remote_server_over_http(server_script):
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, str(server_script), "http", str(port)],
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        with mcp_servers.connect(
            "remote",
            url=f"http://127.0.0.1:{port}/mcp",
            headers={"Authorization": "Bearer test"},
        ) as connection:
            output = asyncio.run(_tool(connection, "add").func(a=4, b=5))
    finally:
        process.terminate()
        process.wait(timeout=10)

    assert output == "9"


async def test_agent_calls_mcp_tool_in_a_run(demo, fake_model):
    agent = harness.Agent(
        name="bot", instructions="你是助手。", tools=demo.tools
    )
    model = fake_model(
        [
            conftest.tool_response(("summary", {"month": "1999-01"})),
            conftest.tool_response(("add", {"a": 20, "b": 22})),
            conftest.text_response("答案是 42"),
        ]
    )

    result = await harness.run(agent, "算一下", model_fn=model)

    assert result.output == "答案是 42"
    error = model.requests[1]["messages"][-1]["content"]
    assert error.startswith("[錯誤]") and "1999-01 沒有資料" in error
    assert model.requests[2]["messages"][-1]["content"] == "42"


def test_expand_env_replaces_references(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "sk-123")
    monkeypatch.delenv("DEMO_MISSING", raising=False)

    assert mcp_servers.expand_env("Bearer ${DEMO_KEY}") == "Bearer sk-123"
    assert mcp_servers.expand_env("${DEMO_MISSING:-http://x}/mcp") == (
        "http://x/mcp"
    )
    assert mcp_servers.expand_env("沒有變數 $HOME") == "沒有變數 $HOME"


def test_expand_env_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv("DEMO_MISSING", raising=False)

    with pytest.raises(ValueError, match="mcpServers.web.*DEMO_MISSING"):
        mcp_servers.expand_env("${DEMO_MISSING}", "mcpServers.web")


def test_from_config_expands_env_in_url_and_headers(monkeypatch, tmp_path):
    # 透過 LiteLLM Proxy 使用的 MCP：網址與金鑰都從環境變數來。
    monkeypatch.setenv("LITELLM_PROXY_URL", "http://proxy:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
    captured = {}

    def fake_connect(name, **kwargs):
        captured.update(kwargs, name=name)
        return "connection"

    monkeypatch.setattr(mcp_servers, "connect", fake_connect)
    config = {
        "url": "${LITELLM_PROXY_URL}/webiq/mcp",
        "headers": {"x-litellm-api-key": "Bearer ${LITELLM_API_KEY}"},
    }

    assert mcp_servers.from_config("webiq", config, tmp_path) == "connection"
    assert captured["url"] == "http://proxy:4000/webiq/mcp"
    assert captured["headers"] == {"x-litellm-api-key": "Bearer sk-test"}
