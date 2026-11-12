"""網頁版上傳的檔案。

上傳的檔案存在工作資料夾（見 folders 模組，預設是自己的「我的檔案」）
底下的 uploads/。沙盒每一輪都把對話的工作資料夾掛載到 /workspace，所以
agent 可以用 run_python 直接處理，也可以用 files 工具讀取。工作資料夾是
專案根目錄時（本機模式與「舊資料」資料夾）照舊版存在 workspace/uploads/
（有沙盒時是 sandbox.workspace/uploads/），附件路徑和以前一樣。

沙盒裡的程式可以把 uploads/ 換成捷徑（symlink），所以存檔時一律不跟著
捷徑走（見 Uploads）。

使用者送出訊息時附上檔案，訊息後面會加上一行行的附件說明：

    [附件] uploads/a.xlsx（run_python 裡的路徑：/workspace/uploads/a.xlsx）

開頭固定是「[附件] 」，接著是相對於工作資料夾的路徑（檔名已經清理過，
不含空白），網頁用它把附件顯示成檔案標籤。
"""

import contextlib
import dataclasses
import errno
import os
import pathlib
import re
import unicodedata
import urllib.parse
from collections.abc import AsyncIterator
from collections.abc import Iterator

from harness import datatypes
from harness import sandbox as sandbox_lib
from harness.builtin import paths as paths_lib

MARKER = "[附件] "
UPLOADS_DIR = "uploads"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
MAX_ATTACHMENTS = 20
_NAME_LIMIT = 100
_UNSAFE = re.compile(r"[^\w.\-()（）]+")
_CREATE_FLAGS = (
    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
)


class UploadError(Exception):
    """上傳失敗，訊息會直接顯示給使用者。

    Attributes:
        status: HTTP 狀態碼。
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclasses.dataclass
class Uploads:
    """一個工作資料夾的上傳資料夾。

    存檔時從工作資料夾開始一層一層用 O_NOFOLLOW 打開上傳資料夾（見
    builtin.paths.open_dir），再用 O_CREAT | O_EXCL | O_NOFOLLOW 建立
    檔案：沙盒裡的程式把 uploads/（或任何一層）換成指向別處的捷徑
    （symlink）時一律拒絕，檔案不可能寫到工作資料夾外面。

    Attributes:
        root: 工作資料夾；回傳與檢查的路徑都相對於它。
        directory: 存放上傳檔案的資料夾（在 root 裡面）。
        sandbox_workspace: 沙盒掛載的資料夾，沒有沙盒時為 None。
        max_bytes: 單一檔案的大小上限。
    """

    root: pathlib.Path
    directory: pathlib.Path
    sandbox_workspace: pathlib.Path | None = None
    max_bytes: int = DEFAULT_MAX_BYTES

    @classmethod
    def for_folder(
        cls,
        folder: pathlib.Path,
        sandbox_workspace: pathlib.Path | None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        subdir: str = UPLOADS_DIR,
    ) -> "Uploads":
        """工作資料夾底下的上傳資料夾。

        Args:
            folder: 工作資料夾。
            sandbox_workspace: 沙盒掛載到 /workspace 的資料夾；有的話附件
                說明會加上容器裡的路徑。
            max_bytes: 單一檔案的大小上限。
            subdir: 上傳資料夾相對於工作資料夾的位置，預設是 uploads。
        """
        folder = folder.resolve()
        return cls(
            root=folder,
            directory=folder / subdir,
            sandbox_workspace=(
                sandbox_workspace.resolve() if sandbox_workspace else None
            ),
            max_bytes=max_bytes,
        )

    async def save(
        self, filename: str, chunks: AsyncIterator[bytes]
    ) -> dict[str, object]:
        """把上傳的內容存成檔案。

        Args:
            filename: 瀏覽器送來的檔名。
            chunks: 檔案內容，一段一段讀入，不會整個放進記憶體。

        Returns:
            {"name": 檔名, "path": 相對於工作資料夾的路徑, "size": 位元組數}

        Raises:
            UploadError: 檔案超過大小上限、是空的，或上傳資料夾是捷徑。
        """
        with self._open_directory() as directory:
            name, handle = self._create(directory, safe_name(filename))
            size = 0
            try:
                with handle:
                    async for chunk in chunks:
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise UploadError(
                                413,
                                f"{name} 超過上限 "
                                f"{self.max_bytes // (1024 * 1024)} MB",
                            )
                        handle.write(chunk)
                if size == 0:
                    raise UploadError(400, f"{name} 是空的檔案")
            except BaseException:
                _remove(directory, self.directory / name, name)
                raise
        return {
            "name": name,
            "path": "/".join((*self._parts(), name)),
            "size": size,
        }

    def describe(self, paths: list[str]) -> str:
        """把附件轉成接在訊息後面的說明，並確認每個都是上傳過的檔案。

        Args:
            paths: 上傳時回傳的 path。

        Returns:
            每個附件一行的說明；沒有附件時是空字串。

        Raises:
            UploadError: 附件太多，或不是上傳資料夾裡的檔案。
        """
        if len(paths) > MAX_ATTACHMENTS:
            raise UploadError(400, f"一次最多附上 {MAX_ATTACHMENTS} 個檔案")
        context = datatypes.RunContext("", self.root, "")
        prefix = self._parts()
        lines = []
        for path in paths:
            try:
                parts = paths_lib.relative_parts(context, path)
            except ValueError as error:
                raise UploadError(400, f"不是上傳的檔案：{path}") from error
            if len(parts) <= len(prefix) or parts[: len(prefix)] != prefix:
                raise UploadError(400, f"不是上傳的檔案：{path}")
            relative = "/".join(parts)
            if not _is_file(context, relative):
                raise UploadError(400, f"找不到上傳的檔案：{path}")
            line = f"{MARKER}{relative}"
            if self.sandbox_workspace is not None:
                target = self.root.joinpath(*parts)
                inside = target.relative_to(self.sandbox_workspace).as_posix()
                container = f"{sandbox_lib.CONTAINER_WORKDIR}/{inside}"
                line += f"（run_python 裡的路徑：{container}）"
            lines.append(line)
        return "\n".join(lines)

    def _parts(self) -> tuple[str, ...]:
        return self.directory.relative_to(self.root).parts

    @contextlib.contextmanager
    def _open_directory(self) -> Iterator[int | None]:
        """打開（必要時建立）上傳資料夾，路徑上有捷徑時拒絕。

        不支援 dir_fd 的平台回傳 None，改用路徑存取。
        """
        if not paths_lib.SAFE_OPEN:
            self.directory.mkdir(parents=True, exist_ok=True)
            yield None
            return
        try:
            directory = paths_lib.open_dir(self.root, self._parts(), True)
        except (paths_lib.LinkError, NotADirectoryError) as error:
            raise UploadError(
                403,
                "上傳資料夾是捷徑（symlink）或不是資料夾，不能存放檔案；"
                "請把它刪掉或改名後再上傳",
            ) from error
        try:
            yield directory
        finally:
            os.close(directory)

    def _create(self, directory: int | None, name: str):
        """建立新檔案；同名的檔案已經存在時，在檔名後面加上 -2、-3…。"""
        stem, dot, suffix = name.rpartition(".")
        if not dot or not stem:
            stem, suffix = name, ""
        for number in range(1, 1000):
            candidate = name if number == 1 else f"{stem}-{number}"
            if number > 1 and suffix:
                candidate += f".{suffix}"
            try:
                if directory is None:
                    handle = (self.directory / candidate).open("xb")
                else:
                    fd = os.open(
                        candidate, _CREATE_FLAGS, 0o666, dir_fd=directory
                    )
                    handle = os.fdopen(fd, "wb")
            except FileExistsError:
                continue
            except OSError as error:
                # O_NOFOLLOW：同名的是捷徑時打不開，也不會跟著寫出去。
                if error.errno in (errno.ELOOP, errno.EMLINK):
                    continue
                raise
            return candidate, handle
        raise UploadError(409, f"同名的檔案太多：{name}")


def _remove(directory: int | None, path: pathlib.Path, name: str) -> None:
    """刪掉寫到一半的檔案。"""
    try:
        if directory is None:
            path.unlink(missing_ok=True)
        else:
            os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def _is_file(context: datatypes.RunContext, path: str) -> bool:
    """路徑是不是工作資料夾裡的一般檔案（不跟著捷徑）。"""
    try:
        fd = paths_lib.open_path(context, path, paths_lib.READ_FLAGS)
    except (OSError, ValueError):
        return False
    try:
        paths_lib.require_file(fd, path)
    except (OSError, ValueError):
        return False
    finally:
        os.close(fd)
    return True


def safe_name(filename: str) -> str:
    """把瀏覽器送來的檔名整理成安全的檔名。

    有些程式會把非 ASCII 的檔名寫成 %E5%A0%B1 這種形式，先還原；接著
    只留最後一段（去掉任何路徑），空白與特殊字元換成底線，不允許以點
    開頭（隱藏檔），太長時保留副檔名截斷。

    Args:
        filename: 原始檔名。

    Returns:
        可以直接放進上傳資料夾的檔名。
    """
    name = unicodedata.normalize("NFC", urllib.parse.unquote(filename))
    name = re.split(r"[\\/]", name)[-1]
    name = _UNSAFE.sub("_", name).strip("._") or "file"
    if len(name) > _NAME_LIMIT:
        stem, dot, suffix = name.rpartition(".")
        if dot and stem and len(suffix) <= 10:
            name = f"{stem[: _NAME_LIMIT - len(suffix) - 1]}.{suffix}"
        else:
            name = name[:_NAME_LIMIT]
    return name
