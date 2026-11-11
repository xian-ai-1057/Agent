"""網頁版瀏覽工作資料夾（專案根目錄）裡的檔案。

GET /api/files/list 列出一個資料夾，GET /api/files 送出一個檔案，讓使用
者預覽或下載 agent 產生的報告、圖表。

能看到的範圍和內建檔案工具相同（見 builtin.paths.resolve_path）：只能
在專案根目錄底下，不能碰存放金鑰的資料夾；指向外面的捷徑（symlink）
以實際位置判斷。另外不提供任何以點開頭的檔案或資料夾，包括整個
.harness/（對話紀錄、金鑰）與 .env 這類設定檔，列出資料夾時也不會出現。

檔案是在 app 自己的網址下打開的，所以 HTML、SVG、JavaScript 這類瀏覽器會
執行的內容一律當成純文字送出，並加上 nosniff 與 CSP sandbox，預覽的檔案
不可能在 app 的網域裡執行程式。
"""

import mimetypes
import pathlib
import urllib.parse
from typing import Any

from harness import datatypes
from harness.builtin import paths

_FALLBACK_TYPE = "application/octet-stream"
_PLAIN_TEXT = "text/plain; charset=utf-8"
# 瀏覽器會當成網頁或程式執行的類型（含所有 XML：可以內嵌 XHTML）。
_RISKY_MARKERS = ("html", "xml", "javascript", "ecmascript")
# PDF 由瀏覽器內建的檢視器顯示，不會在 app 的網域執行程式；加上 CSP
# sandbox 反而會讓 Chrome 拒絕顯示。
_NO_SANDBOX = ("application/pdf",)


class FileError(Exception):
    """找不到或不能存取的檔案，訊息會直接顯示給使用者。

    Attributes:
        status: HTTP 狀態碼。
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def resolve(root: pathlib.Path, path: str) -> pathlib.Path:
    """把網頁送來的相對路徑轉成可以存取的絕對路徑。

    Args:
        root: 專案根目錄。
        path: 相對於專案根目錄的路徑，用 / 分隔；空字串表示根目錄。

    Returns:
        解析過 symlink 的絕對路徑。

    Raises:
        FileError: 路徑超出專案資料夾、指向金鑰資料夾，或含有以點開頭
            的部分（隱藏檔、.harness/）。
    """
    if "\x00" in path:
        raise FileError(400, "路徑不合法")
    context = datatypes.RunContext("", root, "")
    try:
        target = paths.resolve_path(context, path or ".")
    except ValueError as error:
        raise FileError(403, str(error)) from error
    relative = target.relative_to(root.resolve())
    requested = pathlib.PurePosixPath(path.replace("\\", "/"))
    if _hidden(relative.parts) or _hidden(requested.parts):
        raise FileError(403, f"不能存取隱藏的檔案或資料夾：{path}")
    return target


def list_dir(root: pathlib.Path, directory: str) -> list[dict[str, Any]]:
    """列出一個資料夾裡的檔案與子資料夾。

    Args:
        root: 專案根目錄。
        directory: 相對於專案根目錄的資料夾；空字串表示根目錄。

    Returns:
        FileEntry（見 src/web/protocol.ts）的列表，資料夾在前，再依名稱
        排序。隱藏的項目與指到專案外面的 symlink 不會列出。

    Raises:
        FileError: 不能存取這個資料夾，或它不存在。
    """
    target = resolve(root, directory)
    if not target.is_dir():
        raise FileError(404, f"找不到資料夾：{directory}")
    base = target.relative_to(root.resolve())
    entries = []
    for child in target.iterdir():
        relative = (base / child.name).as_posix()
        try:
            resolve(root, relative)
            stat = child.stat()
        except (FileError, OSError):
            continue
        is_dir = child.is_dir()
        entry = {
            "path": relative,
            "name": child.name,
            "kind": "dir" if is_dir else "file",
            "modifiedAt": stat.st_mtime * 1000,
        }
        if not is_dir:
            entry["size"] = stat.st_size
        entries.append(entry)
    return sorted(
        entries,
        key=lambda item: (item["kind"] != "dir", item["name"].lower()),
    )


def response_headers(target: pathlib.Path, download: bool) -> dict[str, str]:
    """送出檔案時的標頭：安全的 Content-Type 與顯示方式。

    Args:
        target: 要送出的檔案。
        download: True 時讓瀏覽器下載，否則直接顯示。

    Returns:
        HTTP 標頭。
    """
    content_type = content_type_of(target.name)
    disposition = "attachment" if download else "inline"
    quoted = urllib.parse.quote(target.name)
    headers = {
        "Content-Type": content_type,
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quoted}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-cache",
    }
    if content_type not in _NO_SANDBOX:
        headers["Content-Security-Policy"] = "sandbox"
    return headers


def content_type_of(name: str) -> str:
    """依檔名決定 Content-Type；會被執行的類型一律改成純文字。

    Args:
        name: 檔名。

    Returns:
        Content-Type 標頭的值；文字類型加上 charset=utf-8。
    """
    guessed, encoding = mimetypes.guess_type(name)
    if encoding is not None or guessed is None:
        # 壓縮檔（.gz）照原樣下載，不讓瀏覽器自動解壓。
        return _FALLBACK_TYPE
    if any(marker in guessed for marker in _RISKY_MARKERS):
        return _PLAIN_TEXT
    if guessed.startswith("text/"):
        return f"{guessed}; charset=utf-8"
    return guessed


def _hidden(parts: tuple[str, ...]) -> bool:
    return any(
        part.startswith(".") and part not in (".", "..") for part in parts
    )
