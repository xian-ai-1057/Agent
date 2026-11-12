"""網頁版上傳的檔案。

上傳的檔案存在工作資料夾（見 folders 模組，預設是自己的「我的檔案」）
底下的 uploads/。沙盒每一輪都把對話的工作資料夾掛載到 /workspace，所以
agent 可以用 run_python 直接處理，也可以用 files 工具讀取。

使用者送出訊息時附上檔案，訊息後面會加上一行行的附件說明：

    [附件] uploads/a.xlsx（run_python 裡的路徑：/workspace/uploads/a.xlsx）

開頭固定是「[附件] 」，接著是相對於工作資料夾的路徑（檔名已經清理過，
不含空白），網頁用它把附件顯示成檔案標籤。
"""

import dataclasses
import pathlib
import re
import unicodedata
import urllib.parse
from collections.abc import AsyncIterator

from harness import sandbox as sandbox_lib

MARKER = "[附件] "
UPLOADS_DIR = "uploads"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
MAX_ATTACHMENTS = 20
_NAME_LIMIT = 100
_UNSAFE = re.compile(r"[^\w.\-()（）]+")


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

    Attributes:
        root: 工作資料夾；回傳與檢查的路徑都相對於它。
        directory: 存放上傳檔案的資料夾。
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
        sandbox: bool,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> "Uploads":
        """工作資料夾底下的 uploads/。

        Args:
            folder: 工作資料夾。
            sandbox: 專案有沒有沙盒；有的話附件說明會加上容器裡的路徑。
            max_bytes: 單一檔案的大小上限。
        """
        folder = folder.resolve()
        return cls(
            root=folder,
            directory=folder / UPLOADS_DIR,
            sandbox_workspace=folder if sandbox else None,
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
            UploadError: 檔案超過大小上限或是空的。
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        target, handle = self._create(safe_name(filename))
        size = 0
        try:
            with handle:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UploadError(
                            413,
                            f"{target.name} 超過上限 "
                            f"{self.max_bytes // (1024 * 1024)} MB",
                        )
                    handle.write(chunk)
            if size == 0:
                raise UploadError(400, f"{target.name} 是空的檔案")
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return {
            "name": target.name,
            "path": self._relative(target),
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
        lines = []
        for path in paths:
            target = (self.root / path).resolve()
            if not target.is_relative_to(self.directory.resolve()):
                raise UploadError(400, f"不是上傳的檔案：{path}")
            if not target.is_file():
                raise UploadError(400, f"找不到上傳的檔案：{path}")
            line = f"{MARKER}{self._relative(target)}"
            if self.sandbox_workspace is not None:
                inside = target.relative_to(self.sandbox_workspace).as_posix()
                container = f"{sandbox_lib.CONTAINER_WORKDIR}/{inside}"
                line += f"（run_python 裡的路徑：{container}）"
            lines.append(line)
        return "\n".join(lines)

    def _create(self, name: str):
        """建立新檔案；同名的檔案已經存在時，在檔名後面加上 -2、-3…。"""
        stem, dot, suffix = name.rpartition(".")
        if not dot or not stem:
            stem, suffix = name, ""
        for number in range(1, 1000):
            candidate = name if number == 1 else f"{stem}-{number}"
            if number > 1 and suffix:
                candidate += f".{suffix}"
            target = self.directory / candidate
            try:
                return target, target.open("xb")
            except FileExistsError:
                continue
        raise UploadError(409, f"同名的檔案太多：{name}")

    def _relative(self, target: pathlib.Path) -> str:
        return target.relative_to(self.root).as_posix()


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
