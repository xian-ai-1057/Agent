"""內建的指令工具（toolset 名稱：shell）。

設計參考 Claude Code 的 Bash 與 Codex 的 shell：在專案資料夾裡執行一個
不需要互動的指令，回傳結束代碼與輸出，讓 agent 能跑測試、建置、git 等
命令列工具。指令可以做任何事，所以預設需要使用者同意。
"""

import asyncio
import contextlib
import dataclasses
import os
import signal

from harness import datatypes
from harness import tools
from harness.builtin import paths

DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
# 每個輸出串流（stdout、stderr）最多回傳幾個字元，超過時保留頭尾。
MAX_STREAM_CHARS = 15_000
# 每個輸出串流最多收集幾個位元組，避免失控的輸出吃光記憶體。
_MAX_CAPTURE_BYTES = 1_000_000
_POSIX = os.name != "nt"


def clip_middle(text: str, limit: int = MAX_STREAM_CHARS) -> str:
    """文字過長時保留開頭與結尾，中間註明省略了多少。

    Args:
        text: 原始文字。
        limit: 最多保留幾個字元。

    Returns:
        不超過 limit（加上省略說明）的文字。
    """
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - half * 2
    return f"{text[:half]}\n…[中間省略 {omitted} 個字元]…\n{text[-half:]}"


@dataclasses.dataclass
class CommandResult:
    """一次指令執行的結果。

    Attributes:
        exit_code: 結束代碼；被終止時為 None。
        stdout: 標準輸出。
        stderr: 標準錯誤。
        timed_out: 是否因為逾時而被終止。
    """

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


async def _capture(stream: asyncio.StreamReader, sink: bytearray) -> None:
    """持續讀取串流直到結束，最多保留 _MAX_CAPTURE_BYTES。"""
    while chunk := await stream.read(65536):
        room = _MAX_CAPTURE_BYTES - len(sink)
        if room > 0:
            sink.extend(chunk[:room])


def _kill(process: asyncio.subprocess.Process) -> None:
    """終止指令；POSIX 上連同它產生的子行程一起終止。"""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        if _POSIX:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()


async def execute(command: str, cwd: str, timeout: float) -> CommandResult:
    """執行指令並收集輸出；逾時或被取消時終止整個行程群組。

    Args:
        command: 要交給 shell 執行的指令。
        cwd: 執行的資料夾。
        timeout: 逾時秒數。

    Returns:
        執行結果。

    Raises:
        ToolError: 無法啟動指令（例如資料夾不存在）。
    """
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # 在 POSIX 上自成一個行程群組，終止時連子行程一起結束。
            start_new_session=_POSIX,
        )
    except OSError as error:
        raise tools.ToolError(f"無法執行指令：{error}") from error
    stdout = bytearray()
    stderr = bytearray()
    readers = asyncio.gather(
        _capture(process.stdout, stdout), _capture(process.stderr, stderr)
    )
    timed_out = False
    try:
        try:
            await asyncio.wait_for(process.wait(), timeout)
        except TimeoutError:
            timed_out = True
            _kill(process)
            await process.wait()
        await readers
    except BaseException:
        # 使用者中斷（task 被取消）時也要終止指令。
        _kill(process)
        readers.cancel()
        raise
    return CommandResult(
        exit_code=None if timed_out else process.returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        timed_out=timed_out,
    )


def format_result(result: CommandResult, timeout: float) -> str:
    """把執行結果整理成給模型看的文字。

    Args:
        result: 執行結果。
        timeout: 這次的逾時秒數。

    Returns:
        結束代碼與各輸出串流。
    """
    lines = []
    if result.timed_out:
        lines.append(f"指令執行超過 {timeout:g} 秒，已終止。")
    exit_code = "無（被終止）" if result.exit_code is None else result.exit_code
    lines.append(f"結束代碼：{exit_code}")
    if result.stdout.strip():
        lines += ["[stdout]", clip_middle(result.stdout.rstrip())]
    if result.stderr.strip():
        lines += ["[stderr]", clip_middle(result.stderr.rstrip())]
    if not result.stdout.strip() and not result.stderr.strip():
        lines.append("（沒有輸出）")
    return "\n".join(lines)


@tools.tool(needs_approval=True)
async def run_command(
    context: datatypes.RunContext,
    command: str,
    cwd: str = ".",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """在專案資料夾裡執行一個 shell 指令，回傳結束代碼、stdout 與 stderr。

    適合跑測試、建置、git 與其他命令列工具。指令不能互動（沒有標準
    輸入），需要確認的指令請加上 --yes 之類的參數。讀寫檔案請優先用
    files 工具。

    Args:
        command: 要執行的指令，例如 npm test 或 git status。
        cwd: 在哪個資料夾執行，相對於專案根目錄。
        timeout: 逾時秒數，超過就終止指令（最多 600）。
    """
    if not command.strip():
        raise tools.ToolError("command 不能是空字串")
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise tools.ToolError(
            f"timeout 必須介於 1 到 {MAX_TIMEOUT_SECONDS} 秒之間"
        )
    directory = paths.resolve_path(context, cwd)
    result = await execute(command, str(directory), timeout)
    return format_result(result, timeout)


TOOLS = [run_command]
