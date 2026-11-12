"""內建的檔案工具（toolset 名稱：files）。

所有路徑都限制在專案根目錄（RunContext.workdir）底下，讀寫都經過
paths.open_path：路徑上的捷徑（symlink）一律拒絕，列資料夾時也不列出捷徑。

設計參考 Claude Code 的 Read / Write / Edit / Glob / Grep：
    read_file: 讀整個檔案，或用 offset / limit 只讀其中幾行。
    edit_file: 精準取代一段文字，不必為了改一行重寫整個檔案。
    find_files、search_files: 依檔名或內容找檔案（見 search 模組）。
"""

from harness import datatypes
from harness import tools
from harness.builtin import paths
from harness.builtin import search

MAX_READ_CHARS = 100_000

# 保留原本的匯入路徑（harness.builtin.files.resolve_path）。
resolve_path = paths.resolve_path


def _truncate(text: str) -> str:
    """超過 MAX_READ_CHARS 時截斷，並註明還有多少沒顯示。"""
    if len(text) <= MAX_READ_CHARS:
        return text
    omitted = len(text) - MAX_READ_CHARS
    note = f"[檔案過長，後面還有 {omitted} 個字元沒有顯示]"
    return f"{text[:MAX_READ_CHARS]}\n{note}"


def _slice_lines(text: str, offset: int, limit: int | None) -> str:
    """依 offset（從 1 起算）與 limit 取出部分行，並註明範圍。"""
    lines = paths.split_lines(text)
    if offset > max(len(lines), 1):
        raise tools.ToolError(f"檔案只有 {len(lines)} 行，offset 超出範圍")
    end = len(lines) if limit is None else min(len(lines), offset - 1 + limit)
    selected = "\n".join(lines[offset - 1 : end])
    if offset == 1 and end == len(lines):
        return selected
    return f"{selected}\n[第 {offset}–{end} 行，共 {len(lines)} 行]"


@tools.tool
def read_file(
    context: datatypes.RunContext,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    """讀取專案資料夾裡的文字檔。

    檔案很長時，用 offset 與 limit 只讀需要的那幾行。

    Args:
        path: 相對於專案根目錄的檔案路徑。
        offset: 從第幾行開始讀（從 1 起算），不寫表示從第一行。
        limit: 最多讀幾行，不寫表示讀到檔案結尾。
    """
    for name, value in (("offset", offset), ("limit", limit)):
        if value is not None and value < 1:
            raise tools.ToolError(f"{name} 必須大於或等於 1")
    text = paths.read_text(context, path)
    if offset is None and limit is None:
        return _truncate(text)
    return _truncate(_slice_lines(text, offset or 1, limit))


@tools.tool
def list_dir(context: datatypes.RunContext, path: str = ".") -> list[str]:
    """列出專案資料夾裡某個目錄的內容。

    Args:
        path: 相對於專案根目錄的目錄路徑。
    """
    return sorted(
        f"{item.name}/" if item.is_dir else item.name
        for item in paths.list_entries(context, path)
    )


@tools.tool(needs_approval=True)
def write_file(context: datatypes.RunContext, path: str, content: str) -> str:
    """把文字寫入專案資料夾裡的檔案，檔案已存在時會覆寫。

    只改檔案的一部分時用 edit_file。

    Args:
        path: 相對於專案根目錄的檔案路徑。
        content: 要寫入的完整內容。
    """
    paths.write_text(context, path, content)
    return f"已寫入 {path}（{len(content)} 個字元）"


@tools.tool(needs_approval=True)
def edit_file(
    context: datatypes.RunContext,
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> str:
    """把檔案裡的一段文字換成新的文字。

    old_text 必須和檔案內容逐字相同（含縮排與換行），而且只出現一次；
    要全部取代時設定 replace_all。修改前先用 read_file 看過檔案。

    Args:
        path: 相對於專案根目錄的檔案路徑。
        old_text: 要被取代的原文，必須逐字相同。
        new_text: 取代後的新文字。
        replace_all: 是否取代所有出現的地方，預設只取代唯一的一處。
    """
    if not old_text:
        raise tools.ToolError("old_text 不能是空字串")
    if old_text == new_text:
        raise tools.ToolError("old_text 與 new_text 相同，沒有需要修改的地方")
    # newline="" 保留原本的換行字元（\r\n 不會被換成 \n）。
    text = paths.read_text(context, path, newline="")
    count = text.count(old_text)
    if count == 0:
        raise tools.ToolError(
            f"{path} 裡找不到 old_text，請先用 read_file 確認原文"
            "（含縮排與換行）"
        )
    if count > 1 and not replace_all:
        raise tools.ToolError(
            f"old_text 在 {path} 裡出現 {count} 次：請加上前後文讓它只出現"
            "一次，或設定 replace_all 取代全部"
        )
    updated = text.replace(old_text, new_text, -1 if replace_all else 1)
    paths.write_text(context, path, updated, newline="")
    return f"已修改 {path}（取代 {count if replace_all else 1} 處）"


TOOLS = [
    read_file,
    list_dir,
    write_file,
    edit_file,
    search.find_files,
    search.search_files,
]
