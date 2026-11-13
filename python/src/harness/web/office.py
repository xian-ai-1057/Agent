"""把 Word、PowerPoint 檔轉成 PDF，給網頁的預覽面板顯示。

瀏覽器沒辦法照原本的版面顯示 Office 檔案，所以用 LibreOffice
（soffice --headless --convert-to pdf）轉成 PDF，再用瀏覽器內建的 PDF
檢視器顯示。Excel 不在這裡：網頁直接把儲存格顯示成表格，比分頁的 PDF
好讀。

在哪裡轉換：

- 有設定沙盒時，在沙盒的容器裡轉換（映像檔要裝 LibreOffice，見
  docker/sandbox.Dockerfile）。檔案是使用者或 agent 給的，解析它的程式
  不在伺服器上執行。
- 沒有沙盒、而且是只在自己電腦上用的本機模式時，用這台電腦安裝的
  LibreOffice。
- 其他情況不轉換，網頁改用瀏覽器裡的簡易預覽。

轉好的 PDF 依檔案內容的 SHA-256 存在 .harness/previews/（內建工具與檔案
API 都碰不到 .harness/），內容沒變就不再轉換；總大小超過 CACHE_LIMIT
時從最舊的開始刪。轉換時的暫存資料夾在 .harness/previews/jobs/ 底下，
結束就刪掉；讀回 PDF 時不跟著捷徑（沙盒裡的程式可以在暫存資料夾裡建立
捷徑）。
"""

import asyncio
import hashlib
import os
import pathlib
import shutil
import uuid

from harness import project as project_lib
from harness import sandbox as sandbox_lib
from harness.builtin import paths

# 可以轉成 PDF 的副檔名。
CONVERTIBLE = frozenset(
    {".docx", ".docm", ".dotx", ".pptx", ".pptm", ".ppsx", ".potx"}
)
# 超過這個大小不轉換（和網頁的簡易預覽一致）。
MAX_SOURCE_BYTES = 30 * 1024 * 1024
# 轉好的 PDF 最多保留這麼多。
CACHE_LIMIT = 500 * 1024 * 1024
TIMEOUT_SECONDS = 120
# 同時最多幾個轉換（LibreOffice 很吃記憶體）。
CONCURRENCY = 2
PREVIEW_DIR = project_lib.DATA_DIR / "previews"
_JOBS = "jobs"
# macOS 安裝 LibreOffice 後，soffice 不在 PATH 裡。
_MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"


class ConvertError(Exception):
    """轉換失敗。

    Attributes:
        status: 給瀏覽器的 HTTP 狀態碼；501 表示這台伺服器不能轉換，網頁
            改用簡易預覽。
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class OfficeConverter:
    """把 Office 檔轉成 PDF，並快取結果。

    Args:
        root: 專案根目錄。
        sandbox: 專案的沙盒設定；None 表示沒有沙盒。
        allow_host: 沒有沙盒時能不能用這台電腦的 LibreOffice（只給本機
            模式）。
    """

    def __init__(
        self,
        root: pathlib.Path,
        sandbox: sandbox_lib.Sandbox | None,
        allow_host: bool,
    ) -> None:
        self.root = root
        self.sandbox = sandbox
        self.host_soffice = None if sandbox else _find_soffice(allow_host)
        self._limit = asyncio.Semaphore(CONCURRENCY)
        self._running: dict[str, asyncio.Future[pathlib.Path]] = {}
        # 沙盒的映像檔沒有 LibreOffice 時記下來，不必每次都試。
        self._missing = False

    @property
    def available(self) -> bool:
        """這台伺服器有沒有辦法轉換（實際能不能轉要試了才知道）。"""
        if self._missing:
            return False
        return self.sandbox is not None or self.host_soffice is not None

    async def to_pdf(self, fd: int, size: int, suffix: str) -> pathlib.Path:
        """把打開的 Office 檔轉成 PDF，回傳快取裡 PDF 的位置。

        Args:
            fd: 已經打開的來源檔案（呼叫的人負責關閉）。
            size: 檔案大小。
            suffix: 副檔名（小寫，含點），決定 LibreOffice 怎麼讀。

        Raises:
            ConvertError: 不能轉換（501）、檔案太大（413）或轉換失敗（422）。
        """
        if suffix not in CONVERTIBLE:
            raise ConvertError(415, "這種檔案不能轉成 PDF")
        if not self.available:
            raise ConvertError(501, "這台伺服器沒有安裝 LibreOffice")
        if size > MAX_SOURCE_BYTES:
            raise ConvertError(413, "檔案太大，請下載後用 Office 開啟")
        data = await asyncio.to_thread(_read_all, fd, size)
        digest = hashlib.sha256(data).hexdigest()
        cached = self._cache_dir() / f"{digest}.pdf"
        if cached.is_file() and not cached.is_symlink():
            os.utime(cached)
            return cached
        # 同一個檔案同時被打開好幾次時只轉換一次；瀏覽器中途離開也讓
        # 轉換做完，下次打開就有快取。
        task = self._running.get(digest)
        if task is None:
            task = asyncio.ensure_future(self._limited(data, suffix, cached))
            self._running[digest] = task
            task.add_done_callback(lambda done: self._finished(digest, done))
        return await asyncio.shield(task)

    async def _limited(
        self, data: bytes, suffix: str, target: pathlib.Path
    ) -> pathlib.Path:
        async with self._limit:
            return await self._convert(data, suffix, target)

    def _finished(self, digest: str, task: asyncio.Future) -> None:
        self._running.pop(digest, None)
        # 等待的人都離開時，不要留下「例外沒有被讀取」的警告。
        if not task.cancelled():
            task.exception()

    def _cache_dir(self) -> pathlib.Path:
        directory = self.root / PREVIEW_DIR
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    async def _convert(
        self, data: bytes, suffix: str, target: pathlib.Path
    ) -> pathlib.Path:
        job = self._cache_dir() / _JOBS / uuid.uuid4().hex
        job.mkdir(parents=True)
        try:
            source = f"source{suffix}"
            (job / source).write_bytes(data)
            await self._run(job, source)
            pdf = await asyncio.to_thread(_read_output, job, "source.pdf")
            partial = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
            partial.write_bytes(pdf)
            os.replace(partial, target)
        finally:
            shutil.rmtree(job, ignore_errors=True)
        await asyncio.to_thread(_prune, target.parent, CACHE_LIMIT)
        return target

    async def _run(self, job: pathlib.Path, source: str) -> None:
        # 設定檔放在暫存的地方，不碰使用者自己的 LibreOffice 設定。
        args = [
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            "pdf",
            "--outdir",
        ]
        if self.sandbox is not None:
            box = self.sandbox.for_folder(self.root, job)
            workdir = sandbox_lib.CONTAINER_WORKDIR
            command = " ".join(
                [
                    "command -v soffice >/dev/null || exit 127;",
                    "soffice",
                    "-env:UserInstallation=file:///tmp/lo",
                    *args,
                    workdir,
                    f"{workdir}/{source}",
                ]
            )
            result = await box.run_shell(command, workdir, TIMEOUT_SECONDS)
            if result.exit_code == 127:
                self._missing = True
                raise ConvertError(
                    501, "沙盒的映像檔沒有安裝 LibreOffice，無法轉成 PDF"
                )
            _check(result.exit_code, result.timed_out, result.stderr)
            return
        assert self.host_soffice is not None
        profile = job / "profile"
        process = await asyncio.create_subprocess_exec(
            self.host_soffice,
            f"-env:UserInstallation={profile.as_uri()}",
            *args,
            str(job),
            str(job / source),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            _check(None, True, "")
            return
        _check(process.returncode, False, stderr.decode(errors="replace"))


def _check(exit_code: int | None, timed_out: bool, stderr: str) -> None:
    if timed_out:
        raise ConvertError(422, "轉換太久，請下載後用 Office 開啟")
    if exit_code != 0:
        detail = stderr.strip().splitlines()[-1:] or [""]
        raise ConvertError(422, f"無法轉成 PDF：{detail[0]}".rstrip("："))


def _find_soffice(allow_host: bool) -> str | None:
    if not allow_host:
        return None
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    return _MAC_SOFFICE if os.path.isfile(_MAC_SOFFICE) else None


def _read_all(fd: int, size: int) -> bytes:
    chunks = []
    offset = 0
    while offset < size:
        chunk = os.pread(fd, min(1 << 20, size - offset), offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _read_output(job: pathlib.Path, name: str) -> bytes:
    """讀回轉好的 PDF；是捷徑或不是一般檔案時拒絕。"""
    try:
        directory = paths.open_dir(job, ())
        try:
            fd = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory,
            )
        finally:
            os.close(directory)
    except OSError as error:
        raise ConvertError(422, "無法轉成 PDF：沒有產生檔案") from error
    try:
        info = paths.require_file(fd, name)
        if info.st_size > MAX_SOURCE_BYTES * 4:
            raise ConvertError(422, "轉出來的 PDF 太大")
        return _read_all(fd, info.st_size)
    except ValueError as error:
        raise ConvertError(422, "無法轉成 PDF：產生的不是檔案") from error
    finally:
        os.close(fd)


def _prune(directory: pathlib.Path, limit: int) -> None:
    """快取超過 limit 時，從最久沒用的 PDF 開始刪。"""
    files = []
    for entry in os.scandir(directory):
        if entry.is_file(follow_symlinks=False) and entry.name.endswith(".pdf"):
            info = entry.stat(follow_symlinks=False)
            files.append((info.st_mtime, info.st_size, entry.path))
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= limit:
            break
        os.unlink(path)
        total -= size
