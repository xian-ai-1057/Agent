"""依檔名或內容找檔案：find_files（類似 glob）與 search_files（類似 grep）。

屬於內建的 files toolset。樣式規則（TypeScript 版相同）：
    - `*` 比對檔名裡任意字元（不跨資料夾），`?` 比對一個字元，`**` 比對
      任意層資料夾，`{py,md}` 比對其中一個。
    - 不含 `/` 的樣式只比對檔名，所以 `*.py` 會找到任何一層的 .py 檔；
      含 `/` 的樣式比對相對於搜尋目錄的完整路徑，例如 `src/**/*.py`。

一律略過 .git、node_modules、.harness 等不需要看的資料夾，也不會跟著
符號連結走出專案資料夾。
"""

import dataclasses
import os
import pathlib
import re
from collections.abc import Callable

from harness import datatypes
from harness import tools
from harness.builtin import paths

# 永遠略過的資料夾名稱。
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".harness",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
# 最多檢查幾個檔案，避免在超大的資料夾裡跑太久。
MAX_WALK_FILES = 20_000
MAX_FIND_RESULTS = 200
MAX_SEARCH_MATCHES = 100
# 超過這個大小的檔案不搜尋內容。
MAX_SEARCH_FILE_BYTES = 1_000_000
_MAX_LINE_CHARS = 300


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """把 glob 樣式轉成比對相對路徑（以 / 分隔）的正規表示式。

    Args:
        pattern: glob 樣式。

    Returns:
        編譯好的正規表示式，需要整串相符。
    """
    body = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            body.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            body.append(".*")
            index += 2
        elif char == "*":
            body.append("[^/]*")
            index += 1
        elif char == "?":
            body.append("[^/]")
            index += 1
        elif char == "{" and pattern.find("}", index) > index:
            end = pattern.find("}", index)
            options = pattern[index + 1 : end].split(",")
            body.append(f"(?:{'|'.join(re.escape(item) for item in options)})")
            index = end + 1
        else:
            body.append(re.escape(char))
            index += 1
    return re.compile(f"^{''.join(body)}$")


def glob_matcher(pattern: str) -> Callable[[str], bool]:
    """建立判斷相對路徑是否符合樣式的函式。

    Args:
        pattern: glob 樣式；不含 / 時只比對檔名。

    Returns:
        接收以 / 分隔的相對路徑、回傳是否相符的函式。
    """
    regex = glob_to_regex(pattern)
    if "/" in pattern:
        return lambda path: regex.match(path) is not None
    return lambda path: regex.match(path.rsplit("/", 1)[-1]) is not None


@dataclasses.dataclass
class Walked:
    """walk_files 的結果。

    Attributes:
        files: 相對於搜尋目錄的路徑，以 / 分隔，依名稱排序。
        truncated: 檔案太多、沒有全部檢查完。
    """

    files: list[str]
    truncated: bool


def walk_files(directory: pathlib.Path) -> Walked:
    """列出目錄底下所有的檔案（略過 IGNORED_DIRS 與符號連結）。

    Args:
        directory: 要列出的目錄。

    Returns:
        找到的檔案，以及是否因為太多而沒有全部列出。
    """
    files: list[str] = []

    def visit(current: pathlib.Path) -> bool:
        with os.scandir(current) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if entry.name in IGNORED_DIRS:
                    continue
                if not visit(pathlib.Path(entry.path)):
                    return False
            elif entry.is_file(follow_symlinks=False):
                if len(files) >= MAX_WALK_FILES:
                    return False
                relative = pathlib.Path(entry.path).relative_to(directory)
                files.append(relative.as_posix())
        return True

    complete = visit(directory)
    return Walked(files=files, truncated=not complete)


def _search_root(context: datatypes.RunContext, path: str) -> pathlib.Path:
    """解析搜尋的起點，必須是專案資料夾裡存在的資料夾。"""
    directory = paths.resolve_path(context, path)
    if not directory.is_dir():
        raise tools.ToolError(f"找不到資料夾：{path}")
    return directory


def _from_root(
    context: datatypes.RunContext, directory: pathlib.Path, path: str
) -> str:
    """把相對於搜尋目錄的路徑換成相對於專案根目錄的路徑。"""
    root = context.workdir.resolve()
    return (directory / path).relative_to(root).as_posix()


@tools.tool
def find_files(
    context: datatypes.RunContext, pattern: str, path: str = "."
) -> str:
    """用 glob 樣式依檔名找檔案，例如 *.py、src/**/*.ts、test_*.{py,md}。

    不含 / 的樣式比對任何一層的檔名。回傳相對於專案根目錄的路徑。

    Args:
        pattern: glob 樣式。
        path: 從哪個資料夾開始找，相對於專案根目錄。
    """
    if not pattern:
        raise tools.ToolError("pattern 不能是空字串")
    directory = _search_root(context, path)
    matches = glob_matcher(pattern)
    walked = walk_files(directory)
    found = [item for item in walked.files if matches(item)]
    if not found:
        return f"沒有符合 {pattern} 的檔案"
    lines = [
        _from_root(context, directory, item)
        for item in found[:MAX_FIND_RESULTS]
    ]
    if len(found) > MAX_FIND_RESULTS:
        lines.append(
            f"[共 {len(found)} 個，只顯示前 {MAX_FIND_RESULTS} 個，請縮小範圍]"
        )
    if walked.truncated:
        lines.append(f"[資料夾裡的檔案太多，只檢查了前 {MAX_WALK_FILES} 個]")
    return "\n".join(lines)


def _clip(line: str) -> str:
    """去掉行尾空白，過長時截斷。"""
    trimmed = line.rstrip()
    if len(trimmed) <= _MAX_LINE_CHARS:
        return trimmed
    return f"{trimmed[:_MAX_LINE_CHARS]}…"


def _read_text(path: pathlib.Path) -> str | None:
    """讀取要搜尋的檔案；太大或是二進位檔時回傳 None。"""
    if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
        return None
    data = path.read_bytes()
    # 有 NUL 字元的視為二進位檔，不搜尋。
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", errors="replace")


@tools.tool
def search_files(
    context: datatypes.RunContext,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    ignore_case: bool = False,
) -> str:
    """用正規表示式搜尋檔案內容，回傳「路徑:行號: 內容」。

    可以用 glob 只搜尋某些檔案（例如 *.py）。適合找函式定義、設定值、
    錯誤訊息出現的位置。

    Args:
        pattern: 正規表示式，例如 def\\s+load_。
        path: 從哪個資料夾開始搜尋，相對於專案根目錄。
        glob: 只搜尋符合這個 glob 樣式的檔案，例如 *.ts。
        ignore_case: 是否忽略大小寫。
    """
    if not pattern:
        raise tools.ToolError("pattern 不能是空字串")
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as error:
        raise tools.ToolError(f"正規表示式有誤：{error}") from error
    directory = _search_root(context, path)
    wanted = glob_matcher(glob) if glob else (lambda _: True)
    walked = walk_files(directory)
    results: list[str] = []
    total = 0
    for file in walked.files:
        if not wanted(file):
            continue
        text = _read_text(directory / file)
        if text is None:
            continue
        for number, line in enumerate(paths.split_lines(text), start=1):
            if not regex.search(line):
                continue
            total += 1
            if len(results) < MAX_SEARCH_MATCHES:
                where = _from_root(context, directory, file)
                results.append(f"{where}:{number}: {_clip(line)}")
    if total == 0:
        return f"沒有找到符合 {pattern} 的內容"
    if total > MAX_SEARCH_MATCHES:
        results.append(
            f"[共 {total} 筆，只顯示前 {MAX_SEARCH_MATCHES} 筆，請縮小範圍]"
        )
    if walked.truncated:
        results.append(f"[資料夾裡的檔案太多，只檢查了前 {MAX_WALK_FILES} 個]")
    return "\n".join(results)
