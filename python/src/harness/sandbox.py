"""在 Docker 容器裡執行 agent 寫的 Python 程式（toolset 名稱：sandbox）。

解析文件、計算與分析資料時，agent 常常需要自己寫程式來跑。這些程式由
模型產生，不能直接在伺服器上執行，所以每次都放進一個拋棄式的容器：

    - 預設沒有網路，資料不會被送出去，也無法下載東西。
    - 限制 CPU、記憶體、行程數與執行時間。
    - 容器的系統目錄唯讀，拿掉所有 Linux capabilities，並以目前的使用者
      （不是 root）身分執行。
    - 只掛載專案裡一個專用的資料夾（預設 workspace/）到容器的
      /workspace：要分析的文件放在這裡，程式產生的檔案也寫在這裡。
      專案根目錄不會被掛載，所以程式改不到 tools/、agents/ 或
      harness.yaml，無法藉此在主機上執行程式。

每次執行都是全新的容器，變數不會留到下一次；要沿用結果請寫成檔案。
"""

import asyncio
import dataclasses
import os
import pathlib
import uuid
from typing import Any

from harness import tools as tools_lib

TOOLSET = "sandbox"
TOOL_NAME = "run_python"
DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_WORKSPACE = "workspace"
CONTAINER_WORKDIR = "/workspace"
NO_OUTPUT = "（程式執行完畢，沒有輸出。要看的結果請用 print 印出來）"

# 專案裡會被 harness 讀取或執行的位置；掛進容器的話，容器裡的程式可以
# 改寫它們，進而在主機上執行程式。
_PROTECTED = (
    "harness.yaml",
    "AGENTS.md",
    "agents",
    "tools",
    "skills",
    ".harness",
)
_KILL_TIMEOUT = 15


@dataclasses.dataclass
class Result:
    """一次執行的結果。

    Attributes:
        exit_code: 程式的結束代碼，0 表示成功；逾時時為 None。
        stdout: 標準輸出（太長時已截斷）。
        stderr: 標準錯誤（太長時已截斷）。
        timed_out: 是否因為超過時間上限而被停止。
    """

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclasses.dataclass
class Sandbox:
    """執行 Python 程式的 Docker 沙盒。

    Example:
        sandbox = Sandbox(pathlib.Path("data"), image="harness-sandbox")
        agent = harness.Agent(name="analyst", tools=sandbox.tools())

    Attributes:
        workspace: 掛載到容器 /workspace 的主機資料夾（可讀寫）。
        image: 容器映像檔，要裝好分析需要的套件（pandas 等）。
        timeout_seconds: 單次執行的時間上限。
        memory: 記憶體上限，Docker 的寫法（例如 1g、512m）。
        cpus: 可以使用的 CPU 數。
        network: 是否允許連網，預設不允許。
        command: 容器工具的指令，docker 或 podman。
        notes: 附加在工具說明後面給模型看的文字，例如已安裝的套件。
        max_output_chars: stdout、stderr 各自最多保留的字數。
    """

    workspace: pathlib.Path
    image: str = DEFAULT_IMAGE
    timeout_seconds: float = 60
    memory: str = "1g"
    cpus: float = 1.0
    network: bool = False
    command: str = "docker"
    notes: str = ""
    max_output_chars: int = 20_000

    def __post_init__(self) -> None:
        self.workspace = pathlib.Path(self.workspace).resolve()
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必須大於 0")
        if self.cpus <= 0:
            raise ValueError("cpus 必須大於 0")

    def docker_args(self, name: str) -> list[str]:
        """組合 docker run 的參數。

        Args:
            name: 容器名稱，逾時或中斷時用它停止容器。

        Returns:
            完整的指令與參數。
        """
        args = [
            self.command,
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--label",
            "harness.sandbox=1",
            "--memory",
            self.memory,
            "--memory-swap",
            self.memory,
            "--cpus",
            str(self.cpus),
            "--pids-limit",
            "256",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
        ]
        if not self.network:
            args += ["--network", "none"]
        if hasattr(os, "getuid"):
            # 用目前的使用者執行：不是 root，寫出的檔案也屬於這個使用者。
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        args += [
            "-e",
            "HOME=/tmp",
            "-e",
            "MPLBACKEND=Agg",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{self.workspace}:{CONTAINER_WORKDIR}",
            "-w",
            CONTAINER_WORKDIR,
            self.image,
            "python",
            "-",
        ]
        return args

    async def run(self, code: str) -> Result:
        """在新的容器裡執行一段 Python 程式。

        Args:
            code: 完整的 Python 程式，從標準輸入交給容器裡的 python。

        Returns:
            執行結果。

        Raises:
            RuntimeError: 找不到 docker 指令。
        """
        self.workspace.mkdir(parents=True, exist_ok=True)
        name = f"harness-sandbox-{uuid.uuid4().hex[:12]}"
        try:
            process = await asyncio.create_subprocess_exec(
                *self.docker_args(name),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                f"找不到 {self.command} 指令，沙盒需要安裝 Docker（或 Podman）"
            ) from error
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(code.encode()), self.timeout_seconds
            )
        except TimeoutError:
            await self._stop(name, process)
            return Result(None, "", "", timed_out=True)
        except BaseException:
            # 使用者中斷這一輪時，容器不會跟著 docker 指令結束，要另外停掉。
            await self._stop(name, process)
            raise
        return Result(
            process.returncode,
            self._shorten(stdout.decode(errors="replace")),
            self._shorten(stderr.decode(errors="replace")),
        )

    def tools(self) -> list[tools_lib.Tool]:
        """包成 agent 可以使用的工具（run_python）。"""

        async def run_python(code: str) -> str:
            result = await self.run(code)
            if result.timed_out:
                raise tools_lib.ToolError(
                    f"執行超過 {self.timeout_seconds:g} 秒，已停止。請縮小資料"
                    "範圍或改用更有效率的做法再試一次。"
                )
            output = format_output(result)
            if result.exit_code != 0:
                raise tools_lib.ToolError(
                    f"程式結束代碼 {result.exit_code}。\n{output}"
                )
            return output

        return [
            tools_lib.tool(
                run_python, name=TOOL_NAME, description=self._description()
            )
        ]

    def _description(self) -> str:
        network = "可以連網" if self.network else "沒有網路"
        text = (
            "在隔離的沙盒裡執行一段 Python 程式並回傳輸出，用來解析文件、"
            "計算與分析資料。\n\n"
            f"- 程式在{network}的全新容器裡執行，工作目錄是 "
            f"{CONTAINER_WORKDIR}：要處理的檔案在這裡，產生的檔案（圖表、"
            "CSV 等）也存在這裡。\n"
            "- 每次執行都是全新的環境，變數不會保留；要沿用的結果請寫成"
            "檔案。\n"
            "- 用 print 輸出要看的結果，輸出太長會被截斷，所以只印需要的"
            "部分（例如摘要統計，而不是整份資料）。\n"
            f"- 每次最多執行 {self.timeout_seconds:g} 秒，無法安裝套件。"
        )
        if self.notes:
            text += f"\n\n{self.notes.strip()}"
        return text

    def _shorten(self, text: str) -> str:
        """太長的輸出只留開頭與結尾。"""
        limit = self.max_output_chars
        if len(text) <= limit:
            return text
        head = text[: limit * 3 // 4]
        tail = text[-(limit // 4) :]
        omitted = len(text) - len(head) - len(tail)
        return f"{head}\n[... 中間省略 {omitted} 個字元 ...]\n{tail}"

    async def _stop(
        self, name: str, process: asyncio.subprocess.Process
    ) -> None:
        """停止容器與 docker 指令本身。"""
        try:
            killer = await asyncio.create_subprocess_exec(
                self.command,
                "kill",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), _KILL_TIMEOUT)
        except (OSError, TimeoutError):
            pass
        if process.returncode is None:
            process.kill()
        await process.wait()


def format_output(result: Result) -> str:
    """把執行結果轉成給模型看的文字。

    Args:
        result: 執行結果。

    Returns:
        stdout，有 stderr 時接在後面。
    """
    parts = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(f"--- stderr ---\n{result.stderr.rstrip()}")
    return "\n\n".join(parts) or NO_OUTPUT


def from_config(root: pathlib.Path, config: Any) -> Sandbox | None:
    """依 harness.yaml 的 sandbox 區塊建立沙盒。

    Args:
        root: 專案根目錄。
        config: sandbox 區塊；true 表示全部用預設值，沒寫或 false 表示
            不使用沙盒。

    Returns:
        沙盒，沒有設定時為 None。

    Raises:
        ValueError: 設定有誤，或 workspace 會讓容器改到專案的程式與設定。
    """
    if config is None or config is False:
        return None
    if config is True:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("sandbox 必須是 true 或設定區塊")
    unknown = sorted(set(config) - set(_FIELDS))
    if unknown:
        raise ValueError(
            f"sandbox 有不認得的設定：{unknown}，可用的有：{sorted(_FIELDS)}"
        )
    options = {_FIELDS[key]: value for key, value in config.items()}
    workspace = _workspace(
        root, str(options.pop("workspace", DEFAULT_WORKSPACE))
    )
    return Sandbox(workspace=workspace, **options)


_FIELDS = {
    "image": "image",
    "workspace": "workspace",
    "timeoutSeconds": "timeout_seconds",
    "memory": "memory",
    "cpus": "cpus",
    "network": "network",
    "command": "command",
    "notes": "notes",
}


def _workspace(root: pathlib.Path, path: str) -> pathlib.Path:
    """檢查要掛進容器的資料夾：要在專案裡，且不能碰到專案的程式與設定。"""
    root = root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"sandbox.workspace 必須在專案資料夾裡：{path}")
    if target == root:
        raise ValueError(
            "sandbox.workspace 不能是專案根目錄：容器裡的程式可以改寫 tools/ "
            f"與 harness.yaml。請改用專用的資料夾，例如 {DEFAULT_WORKSPACE}"
        )
    for name in _PROTECTED:
        protected = root / name
        if target == protected or target.is_relative_to(protected):
            raise ValueError(
                f"sandbox.workspace 不能放在 {name} 裡：容器裡的程式可以改寫"
                "它，進而在主機上執行程式"
            )
    return target
