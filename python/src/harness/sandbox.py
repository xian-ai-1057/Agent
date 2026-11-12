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
執行前後比對工作目錄，新增或修改的檔案會以 PRODUCED_MARKER 開頭的行
接在工具結果後面（路徑相對於專案根目錄），網頁版用它顯示檔案卡片。

pyharness 自己也跑在容器裡時（用 docker-compose.yml 部署），它看到的路徑
不是主機上的路徑，沒辦法直接掛載。這時專案放在一個 Docker volume 裡，
用環境變數 HARNESS_SANDBOX_VOLUME 告訴沙盒 volume 的名稱，沙盒改成只掛載
那個 volume 裡的工作目錄。映像檔與給模型看的說明也可以用環境變數提供
預設值（HARNESS_SANDBOX_IMAGE、HARNESS_SANDBOX_NOTES），harness.yaml 有寫
的話以 harness.yaml 為準。
"""

import asyncio
import dataclasses
import os
import pathlib
import shutil
import subprocess
import uuid
from typing import Any

from harness import datatypes
from harness import tools as tools_lib

TOOLSET = "sandbox"
TOOL_NAME = "run_python"
DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_WORKSPACE = "workspace"
CONTAINER_WORKDIR = "/workspace"
IMAGE_ENV = "HARNESS_SANDBOX_IMAGE"
NOTES_ENV = "HARNESS_SANDBOX_NOTES"
VOLUME_ENV = "HARNESS_SANDBOX_VOLUME"
NO_OUTPUT = "（程式執行完畢，沒有輸出。要看的結果請用 print 印出來）"
# 和 src/web/protocol.ts 的 PRODUCED_MARKER 相同，兩邊要一起改。
PRODUCED_MARKER = "[產生的檔案] "
# 比對產生的檔案時最多看幾個項目、最多列出幾個檔案。
_SCAN_LIMIT = 5000
_PRODUCED_LIMIT = 20

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
_AVAILABLE_TIMEOUT = 10


def available(command: str = "docker") -> bool:
    """確認這台機器現在能不能用 Docker 執行容器。

    Args:
        command: 容器工具的指令，docker 或 podman。

    Returns:
        指令存在，而且連得上 Docker 時為 True。
    """
    if shutil.which(command) is None:
        return False
    try:
        result = subprocess.run(
            [command, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=_AVAILABLE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


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
        volume: workspace 所在的 Docker volume 名稱；pyharness 本身跑在
            容器裡時使用，None 表示直接掛載主機上的資料夾。
        volume_subpath: workspace 在 volume 裡的相對路徑。
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
    volume: str | None = None
    volume_subpath: str = ""

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
            *self._mount_args(),
            "-w",
            CONTAINER_WORKDIR,
            self.image,
            "python",
            "-",
        ]
        return args

    def _mount_args(self) -> list[str]:
        """把工作目錄掛進容器的參數。"""
        if self.volume is None:
            return ["-v", f"{self.workspace}:{CONTAINER_WORKDIR}"]
        # volume-nocopy：資料夾是空的時，Docker 預設會把映像檔裡同一個
        # 位置的內容與擁有者複製過來，工作目錄會變成 root 的，之後
        # pyharness 就寫不進去（例如存不了上傳的檔案）。
        mount = (
            f"type=volume,src={self.volume},dst={CONTAINER_WORKDIR}"
            ",volume-nocopy"
        )
        if self.volume_subpath:
            mount += f",volume-subpath={self.volume_subpath}"
        return ["--mount", mount]

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

        async def run_python(context: datatypes.RunContext, code: str) -> str:
            before = await asyncio.to_thread(snapshot, self.workspace)
            result = await self.run(code)
            after = await asyncio.to_thread(snapshot, self.workspace)
            # 失敗或逾時前寫出的檔案也列出來，部分結果常常還用得上。
            produced = produced_lines(
                self.workspace, context.workdir, before, after
            )
            if result.timed_out:
                raise tools_lib.ToolError(
                    f"執行超過 {self.timeout_seconds:g} 秒，已停止。請縮小資料"
                    "範圍或改用更有效率的做法再試一次。" + produced
                )
            output = format_output(result) + produced
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
            "CSV 等）也存在這裡。新增或修改的檔案會列在結果最後"
            f"（{PRODUCED_MARKER.strip()} 開頭的行，路徑相對於專案）。\n"
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


Snapshot = dict[str, tuple[int, int]]


def snapshot(workspace: pathlib.Path) -> Snapshot:
    """記下工作目錄裡每個一般檔案的修改時間與大小。

    略過以 . 開頭的檔案與資料夾；最多看 5,000 個項目，避免資料夾很大
    時拖慢每一次執行。

    Args:
        workspace: 沙盒的工作目錄。

    Returns:
        以相對於 workspace 的路徑（用 / 分隔）為鍵，值為
        (修改時間的奈秒數, 位元組數)。
    """
    found: Snapshot = {}
    pending = [(workspace, "")]
    seen = 0
    while pending and seen < _SCAN_LIMIT:
        folder, prefix = pending.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > _SCAN_LIMIT:
                break
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue
            relative = f"{prefix}{entry.name}"
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append((pathlib.Path(entry.path), f"{relative}/"))
                elif entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    found[relative] = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
    return found


def produced_lines(
    workspace: pathlib.Path,
    root: pathlib.Path,
    before: Snapshot,
    after: Snapshot,
) -> str:
    """把這次執行新增或修改的檔案轉成要接在工具結果後面的文字。

    Args:
        workspace: 沙盒的工作目錄。
        root: 專案根目錄；網頁只能提供這裡面的檔案，不在裡面的不列出。
        before: 執行前的 snapshot。
        after: 執行後的 snapshot。

    Returns:
        開頭空一行、每個檔案一行（PRODUCED_MARKER 加上相對於專案根目錄
        的路徑），最新的排前面，最多 20 個；沒有產生檔案時為空字串。
    """
    changed = [
        (stamp[0], name)
        for name, stamp in after.items()
        if before.get(name) != stamp
    ]
    if not changed:
        return ""
    try:
        base = workspace.resolve().relative_to(root.resolve())
    except ValueError:
        return ""
    changed.sort(reverse=True)
    lines = [
        f"{PRODUCED_MARKER}{(base / name).as_posix()}"
        for _, name in changed[:_PRODUCED_LIMIT]
    ]
    omitted = len(changed) - _PRODUCED_LIMIT
    if omitted > 0:
        lines.append(f"（另外還有 {omitted} 個檔案沒有列出）")
    return "\n\n" + "\n".join(lines)


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
    # 部署環境提供的預設值；harness.yaml 有寫的話以它為準。
    for field, variable in (("image", IMAGE_ENV), ("notes", NOTES_ENV)):
        if field not in options and os.environ.get(variable):
            options[field] = os.environ[variable]
    volume = os.environ.get(VOLUME_ENV)
    if volume:
        # 專案根目錄就是 volume 的根目錄，所以相對路徑相同。
        options["volume"] = volume
        options["volume_subpath"] = workspace.relative_to(
            root.resolve()
        ).as_posix()
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
