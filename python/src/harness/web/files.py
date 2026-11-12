"""網頁版瀏覽工作資料夾（見 folders 模組）裡的檔案。

GET /api/files/list 列出一個資料夾，GET /api/files 送出一個檔案，讓使用
者預覽或下載 agent 產生的報告、圖表。

能看到的範圍和內建檔案工具相同（見 builtin.paths）：只能在工作資料夾
底下（伺服器先確認使用者可以使用那個資料夾），不能碰存放金鑰的資料夾。
檔案從工作資料夾開始一層一層用 O_NOFOLLOW 打開，再從同一個 fd 送出：
路徑上有任何捷徑（symlink）一律拒絕，列出資料夾時也不列出捷徑，沙盒裡
的程式換掉資料夾也騙不到伺服器。另外不提供任何以點開頭的檔案或資料夾，
包括整個 .harness/（對話紀錄、金鑰）與 .env 這類設定檔，列出資料夾時也
不會出現；資料夾另外指定不給看的名稱（例如「舊資料」資料夾的 private/）
也一樣。

檔案是在 app 自己的網址下打開的，所以 HTML、SVG、JavaScript 這類瀏覽器會
執行的內容一律當成純文字送出，並加上 nosniff 與 CSP sandbox，預覽的檔案
不可能在 app 的網域裡執行程式。
"""

import mimetypes
import os
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


def open_file(
    root: pathlib.Path, path: str, hidden: tuple[str, ...] = ()
) -> tuple[int, os.stat_result]:
    """打開工作資料夾裡要送出的檔案。

    Args:
        root: 工作資料夾。
        path: 相對於工作資料夾的路徑，用 / 分隔。
        hidden: 資料夾第一層不給看的名稱。

    Returns:
        (fd, stat)；fd 用完要自己關閉。

    Raises:
        FileError: 路徑不能存取（403），或檔案不存在、不是一般的檔案
            （404）。
    """
    if not path:
        raise FileError(400, "缺少 path")
    context = _context(root, path, hidden)
    try:
        fd = paths.open_path(context, path, paths.READ_FLAGS)
    except ValueError as error:
        raise FileError(403, str(error)) from error
    except OSError as error:
        raise FileError(404, f"找不到檔案：{path}") from error
    try:
        info = paths.require_file(fd, path)
    except (OSError, ValueError) as error:
        os.close(fd)
        raise FileError(404, f"找不到檔案：{path}") from error
    return fd, info


def list_dir(
    root: pathlib.Path, directory: str, hidden: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """列出一個資料夾裡的檔案與子資料夾。

    Args:
        root: 工作資料夾。
        directory: 相對於工作資料夾的資料夾；空字串表示資料夾本身。
        hidden: 資料夾第一層不給看的名稱。

    Returns:
        FileEntry（見 src/web/protocol.ts）的列表，資料夾在前，再依名稱
        排序。隱藏的項目與捷徑（symlink）不會列出。

    Raises:
        FileError: 不能存取這個資料夾，或它不存在。
    """
    context = _context(root, directory, hidden)
    try:
        parts = paths.relative_parts(context, directory or ".")
        items = paths.list_entries(context, directory or ".")
    except ValueError as error:
        raise FileError(403, str(error)) from error
    except NotADirectoryError as error:
        raise FileError(404, f"找不到資料夾：{directory}") from error
    except FileNotFoundError as error:
        raise FileError(404, f"找不到資料夾：{directory}") from error
    except OSError as error:
        raise FileError(403, f"不能讀取資料夾：{directory}") from error
    entries = []
    for item in items:
        entry = {
            "path": "/".join((*parts, item.name)),
            "name": item.name,
            "kind": "dir" if item.is_dir else "file",
            "modifiedAt": item.modified * 1000,
        }
        if not item.is_dir:
            entry["size"] = item.size
        entries.append(entry)
    return sorted(
        entries,
        key=lambda item: (item["kind"] != "dir", item["name"].lower()),
    )


def response_headers(name: str, download: bool) -> dict[str, str]:
    """送出檔案時的標頭：安全的 Content-Type 與顯示方式。

    Args:
        name: 檔名。
        download: True 時讓瀏覽器下載，否則直接顯示。

    Returns:
        HTTP 標頭。
    """
    content_type = content_type_of(name)
    disposition = "attachment" if download else "inline"
    quoted = urllib.parse.quote(name)
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


def _absolute(path: str) -> bool:
    """路徑是否為絕對路徑，或帶有磁碟代號、網路路徑等開頭。"""
    windows = pathlib.PureWindowsPath(path)
    return (
        pathlib.PurePosixPath(path).is_absolute()
        or bool(windows.drive or windows.anchor)
        or path.startswith(("\\", "//"))
    )


def _context(
    root: pathlib.Path, path: str, hidden: tuple[str, ...]
) -> datatypes.RunContext:
    """檢查網頁送來的路徑，回傳存取用的 RunContext（不給看以點開頭的）。

    Raises:
        FileError: 路徑不合法，或是絕對路徑、磁碟代號、網路路徑。
    """
    if "\x00" in path:
        raise FileError(400, "路徑不合法")
    # 絕對路徑、磁碟代號與網路路徑（\\host\share）在碰到檔案系統之前就
    # 擋下，避免網頁讓伺服器去讀專案外面或其他電腦上的位置。
    if _absolute(path):
        raise FileError(403, f"只能使用專案資料夾裡的相對路徑：{path}")
    return datatypes.RunContext(
        "", root, "", hidden=tuple(hidden), hide_dotfiles=True
    )
