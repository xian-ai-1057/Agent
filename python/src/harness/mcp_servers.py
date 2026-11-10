"""連接 MCP 伺服器，把它提供的工具轉成框架的 Tool。

用途是讓 agent 使用現成的 MCP 伺服器（其他語言寫的服務、第三方提供的
工具），設定方式與 TypeScript 版相同（harness.yaml 的 mcpServers）。

每條連線在自己的背景執行緒裡跑一個事件迴圈，連線會一直開著，直到呼叫
close()。這樣連線就不屬於任何一個呼叫端的事件迴圈：同步的 load_project、
每次請求各自 asyncio.run 的網頁服務、命令列的聊天都能共用同一條連線。

需要 MCP 的 Python SDK：pip install "mcp>=2"。
"""

import asyncio
import concurrent.futures
import contextlib
import json
import os
import pathlib
import sys
import threading
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any

from harness import tools as tools_lib

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_STARTUP_TIMEOUT_SECONDS = 300
_STDERR_TAIL_CHARS = 2000
_CLOSE_TIMEOUT_SECONDS = 10
_CLIENT_NAME = "agent-harness"
# Claude Code、Cursor 等工具的 MCP 設定會寫 type，值與需要的欄位對應。
_TRANSPORT_TYPES = {
    "stdio": "command",
    "http": "url",
    "streamable-http": "url",
    "streamableHttp": "url",
}


class McpConnection:
    """一條已建立的 MCP 連線。

    用完一定要呼叫 close()（或用 with），伺服器程式才會結束。

    Attributes:
        name: 伺服器名稱，也是這組工具的 toolset 名稱。
        tools: 伺服器提供的工具。
    """

    def __init__(self, name: str, timeout_seconds: float) -> None:
        self.name = name
        self.tools: list[tools_lib.Tool] = []
        self._timeout = timeout_seconds
        self._client: Any = None
        self._stop = asyncio.Event()
        self._serving: concurrent.futures.Future[None] | None = None
        self._started = False
        self._finished = threading.Event()
        self._cleanups: list[Callable[[], None]] = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name=f"mcp-{name}", daemon=True
        )
        self._thread.start()

    def __enter__(self) -> "McpConnection":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """關閉連線並結束伺服器程式。可以重複呼叫。"""
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._stop.set)
        if self._started:
            # 等連線真正結束（伺服器程式被終止）才停下事件迴圈。
            self._finished.wait(timeout=_CLOSE_TIMEOUT_SECONDS)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=_CLOSE_TIMEOUT_SECONDS)
        if not self._thread.is_alive():
            self._loop.close()
        for cleanup in self._cleanups:
            cleanup()
        self._cleanups.clear()

    def _start(
        self, open_client: Callable[[], Any], timeout_seconds: float
    ) -> None:
        """在背景執行緒建立連線，等到取得工具清單才回傳。"""
        ready: concurrent.futures.Future[list[Any]] = (
            concurrent.futures.Future()
        )
        self._serving = asyncio.run_coroutine_threadsafe(
            self._serve(open_client, ready), self._loop
        )
        try:
            infos = ready.result(timeout=timeout_seconds)
        except BaseException:
            # 例如伺服器啟動後一直沒有回應：取消連線中的工作再關閉。
            self._serving.cancel()
            self.close()
            raise
        self.tools = [self._wrap(info) for info in infos]

    async def _serve(
        self,
        open_client: Callable[[], Any],
        ready: concurrent.futures.Future[list[Any]],
    ) -> None:
        """維持連線，直到 close() 要求結束。

        MCP SDK 的連線是 async context manager，進入與離開必須在同一個
        task 裡，所以整段連線的生命週期都在這個 coroutine 裡。
        """
        self._started = True
        try:
            async with open_client() as client:
                infos = await _list_all_tools(client)
                self._client = client
                ready.set_result(infos)
                await self._stop.wait()
        except BaseException as error:
            if not ready.done():
                ready.set_exception(error)
                return
            if isinstance(error, asyncio.CancelledError):
                raise
            # 連線中途斷掉（例如伺服器當掉）：之後的呼叫會回報已經斷線。
        finally:
            self._client = None
            self._finished.set()

    def _wrap(self, info: Any) -> tools_lib.Tool:
        """把 MCP 的工具資訊轉成 Tool。"""
        tool_name = info.name

        async def call(**arguments: Any) -> str:
            return await self._call(tool_name, arguments)

        parameters = dict(info.input_schema or {})
        parameters.setdefault("type", "object")
        parameters.setdefault("properties", {})
        annotations = info.annotations
        return tools_lib.Tool(
            name=tool_name,
            description=(info.description or "").strip() or tool_name,
            parameters=parameters,
            func=call,
            # 伺服器明確標記為破壞性操作的工具，執行前要先問使用者。
            needs_approval=bool(annotations and annotations.destructive_hint),
            # 參數由伺服器自己驗證。
            args_model=None,
        )

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """把工具呼叫轉送到背景執行緒的連線，等待結果。"""
        client = self._client
        if client is None:
            raise tools_lib.ToolError(f"MCP 伺服器 {self.name} 已經斷線")
        future = asyncio.run_coroutine_threadsafe(
            client.call_tool(
                tool_name, arguments, read_timeout_seconds=self._timeout
            ),
            self._loop,
        )
        # 呼叫端被取消（例如使用者按 Ctrl-C）時，背景的呼叫也會一起取消。
        result = await asyncio.wrap_future(future)
        text = content_to_text(result.content, result.structured_content)
        if result.is_error:
            raise tools_lib.ToolError(text or f"工具 {tool_name} 執行失敗")
        return text


def connect(
    name: str,
    *,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | pathlib.Path | None = None,
    url: str | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
) -> McpConnection:
    """連上一個 MCP 伺服器並取得它的工具。

    command（啟動本機程式，透過 stdio 溝通）與 url（遠端伺服器，
    Streamable HTTP）擇一。

    Example:
        with harness.connect_mcp_server(
            "report", command="python3", args=["server.py"]
        ) as report:
            agent = harness.Agent(name="bot", instructions="...",
                                  tools=report.tools)
            ...

    Args:
        name: 伺服器名稱，也是這組工具的 toolset 名稱。
        command: 要啟動的程式，例如 python3。
        args: 程式的參數。
        env: 額外的環境變數，會加在目前的環境變數之上。
        cwd: 啟動程式時的工作目錄。
        url: 遠端伺服器的網址。
        headers: 連遠端伺服器時附帶的 HTTP 標頭（例如認證）。
        timeout_seconds: 單次工具呼叫最多等幾秒。
        startup_timeout_seconds: 啟動並完成連線最多等幾秒（第一次用 npx
            這類指令時可能要先下載）。

    Returns:
        已連線的 McpConnection，用完要 close()。

    Raises:
        ImportError: 沒有安裝 mcp 套件。
        ValueError: command 與 url 都沒有設定，或兩者都設定了。
        RuntimeError: 伺服器無法啟動或連線失敗。
    """
    if bool(command) == bool(url):
        raise ValueError(f"MCP 伺服器 {name} 必須設定 command 或 url（擇一）")
    mcp = _import_mcp(name)
    stderr = _StderrTail() if command else None
    connection = McpConnection(name, timeout_seconds)

    @contextlib.asynccontextmanager
    async def open_client() -> AsyncIterator[Any]:
        async with contextlib.AsyncExitStack() as stack:
            if url:
                transport = await _http_transport(mcp, stack, url, headers)
            else:
                transport = mcp.client.stdio.stdio_client(
                    mcp.StdioServerParameters(
                        command=command,
                        args=[str(item) for item in args or []],
                        # 伺服器是專案自己的程式，需要完整的環境變數（虛擬
                        # 環境、資料庫連線設定…），所以不用 SDK 預設的精簡
                        # 環境。
                        env={**os.environ, **(env or {})},
                        cwd=cwd,
                    ),
                    errlog=stderr.writer,
                )
            client = mcp.Client(
                transport,
                client_info=mcp.Implementation(
                    name=_CLIENT_NAME, version=_harness_version()
                ),
            )
            yield await stack.enter_async_context(client)

    if stderr is not None:
        connection._cleanups.append(stderr.close)
    try:
        connection._start(open_client, startup_timeout_seconds)
    except BaseException as error:
        if isinstance(error, KeyboardInterrupt):
            raise
        detail = stderr.finish() if stderr is not None else ""
        if isinstance(error, TimeoutError):
            detail = f"{startup_timeout_seconds} 秒內沒有完成連線\n{detail}"
            detail = detail.strip()
        action = (
            f"連上 MCP 伺服器 {name}（{url}）"
            if url
            else f"啟動 MCP 伺服器 {name}"
        )
        raise RuntimeError(
            f"無法{action}：\n{detail or _describe(error)}"
        ) from error
    if stderr is not None:
        stderr.release_writer()
    return connection


def from_config(
    name: str, config: dict[str, Any] | None, cwd: pathlib.Path
) -> McpConnection:
    """依 harness.yaml 裡 mcpServers 的一個項目建立連線。

    Args:
        name: 伺服器名稱。
        config: 設定（command、args、env、url、headers、timeoutSeconds，
            以及從 Claude Code 等工具複製過來時會有的 type）。
        cwd: 啟動本機伺服器時的工作目錄（專案根目錄）。

    Returns:
        已連線的 McpConnection。
    """
    config = config or {}
    known = {"type", "command", "args", "env", "url", "headers"}
    known.add("timeoutSeconds")
    unknown = sorted(set(config) - known)
    if unknown:
        raise ValueError(
            f"mcpServers.{name} 有不認得的設定：{unknown}，"
            f"可用的有：{sorted(known)}"
        )
    _check_type(name, config)
    return connect(
        name,
        command=config.get("command"),
        args=[str(item) for item in config.get("args") or []],
        env={k: str(v) for k, v in (config.get("env") or {}).items()},
        cwd=cwd,
        url=config.get("url"),
        headers={k: str(v) for k, v in (config.get("headers") or {}).items()},
        timeout_seconds=config.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS,
    )


def _check_type(name: str, config: dict[str, Any]) -> None:
    """確認 type 與 command / url 一致；沒寫 type 時不檢查。"""
    kind = config.get("type")
    if kind is None:
        return
    if kind == "sse":
        raise ValueError(
            f"mcpServers.{name} 使用舊的 SSE 傳輸，目前不支援；請改用伺服器"
            "的 Streamable HTTP 端點（通常是 /mcp），並把 type 改成 http"
        )
    if kind not in _TRANSPORT_TYPES:
        raise ValueError(
            f"mcpServers.{name} 的 type 不認得：{kind}，"
            f"可用的有：{sorted(_TRANSPORT_TYPES)}"
        )
    needed = _TRANSPORT_TYPES[kind]
    if not config.get(needed):
        raise ValueError(
            f"mcpServers.{name} 的 type 是 {kind}，需要設定 {needed}"
        )


def content_to_text(content: Any, structured: Any) -> str:
    """把 MCP 工具的回傳內容轉成文字。

    Args:
        content: 回傳的內容區塊。
        structured: 結構化的回傳值（沒有文字內容時才使用）。

    Returns:
        給模型看的文字。
    """
    parts = []
    for block in content or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text or "")
        else:
            parts.append(f"[{getattr(block, 'type', '未知')} 內容]")
    text = "\n".join(parts).strip()
    if text or structured is None:
        return text
    return json.dumps(structured, ensure_ascii=False, default=str)


async def _list_all_tools(client: Any) -> list[Any]:
    """列出伺服器所有的工具（伺服器可能分頁回傳）。"""
    infos: list[Any] = []
    cursor = None
    while True:
        page = await client.list_tools(cursor=cursor)
        infos.extend(page.tools)
        cursor = page.next_cursor
        if not cursor:
            return infos


async def _http_transport(
    mcp: Any,
    stack: contextlib.AsyncExitStack,
    url: str,
    headers: dict[str, str] | None,
) -> Any:
    """建立連到遠端伺服器的 transport。"""
    from mcp.client import streamable_http

    http_client = await stack.enter_async_context(
        streamable_http.create_mcp_http_client(headers=headers or None)
    )
    return streamable_http.streamable_http_client(url, http_client=http_client)


def _import_mcp(name: str) -> Any:
    """載入 MCP SDK，沒有安裝時說明怎麼安裝。"""
    try:
        import mcp
        import mcp.client.stdio
    except ImportError as error:
        raise ImportError(
            f'連接 MCP 伺服器 {name} 需要 mcp 套件：pip install "mcp>=2"'
        ) from error
    if not hasattr(mcp, "Client"):
        raise ImportError(
            f'連接 MCP 伺服器 {name} 需要 mcp 2 以上：pip install -U "mcp>=2"'
        )
    return mcp


def _harness_version() -> str:
    """目前安裝的 harness 版本。"""
    from importlib import metadata

    try:
        return metadata.version("agent-harness")
    except metadata.PackageNotFoundError:
        return "0"


def _describe(error: BaseException) -> str:
    """取出例外裡最有用的訊息（anyio 會把錯誤包成 ExceptionGroup）。"""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return str(error) or type(error).__name__


class _StderrTail:
    """收集伺服器的 stderr，只保留最後一段供啟動失敗時顯示。

    伺服器的 log 不直接印到聊天畫面；設定環境變數 HARNESS_DEBUG 時才
    原樣轉印到 stderr。用 pipe 加讀取執行緒，而不是暫存檔，是為了讓
    長時間執行、log 很多的伺服器不會佔用越來越多空間。
    """

    def __init__(self) -> None:
        read_fd, write_fd = os.pipe()
        self.writer = os.fdopen(write_fd, "w", encoding="utf-8")
        self._reader = os.fdopen(read_fd, "rb")
        self._tail = b""
        self._echo = bool(os.environ.get("HARNESS_DEBUG"))
        self._thread = threading.Thread(
            target=self._drain, name="mcp-stderr", daemon=True
        )
        self._thread.start()

    def _drain(self) -> None:
        while chunk := self._reader.read1(4096):
            if self._echo:
                sys.stderr.write(chunk.decode("utf-8", "replace"))
            self._tail = (self._tail + chunk)[-_STDERR_TAIL_CHARS * 4 :]
        self._reader.close()

    def release_writer(self) -> None:
        """伺服器已經啟動，關閉這一端的寫入口（伺服器保有自己的）。"""
        if not self.writer.closed:
            self.writer.close()

    def finish(self) -> str:
        """等伺服器的 stderr 讀完，回傳最後一段。"""
        self.release_writer()
        self._thread.join(timeout=_CLOSE_TIMEOUT_SECONDS)
        text = self._tail.decode("utf-8", "replace")
        return text[-_STDERR_TAIL_CHARS:].strip()

    def close(self) -> None:
        """關閉寫入口；讀取執行緒會在伺服器結束後自己停止。"""
        self.release_writer()
