"""內建的檔案工具（toolset 名稱：files）。

所有路徑都限制在專案根目錄（RunContext.workdir）底下。
"""

import pathlib

from harness import datatypes
from harness import tools

MAX_READ_CHARS = 100_000


def resolve_path(context: datatypes.RunContext, path: str) -> pathlib.Path:
    """把相對路徑轉成專案根目錄底下的絕對路徑。

    Args:
        context: 這次 run 的環境資訊。
        path: 相對於專案根目錄的路徑。

    Returns:
        解析後的絕對路徑。

    Raises:
        ValueError: 路徑跑到專案根目錄之外。
    """
    root = context.workdir.resolve()
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"路徑超出專案資料夾：{path}")
    return target


@tools.tool
def read_file(context: datatypes.RunContext, path: str) -> str:
    """讀取專案資料夾裡的文字檔。

    Args:
        path: 相對於專案根目錄的檔案路徑。
    """
    text = resolve_path(context, path).read_text(encoding="utf-8")
    if len(text) > MAX_READ_CHARS:
        omitted = len(text) - MAX_READ_CHARS
        return (
            f"{text[:MAX_READ_CHARS]}\n"
            f"[檔案過長，後面還有 {omitted} 個字元沒有顯示]"
        )
    return text


@tools.tool
def list_dir(context: datatypes.RunContext, path: str = ".") -> list[str]:
    """列出專案資料夾裡某個目錄的內容。

    Args:
        path: 相對於專案根目錄的目錄路徑。
    """
    target = resolve_path(context, path)
    return sorted(
        f"{item.name}/" if item.is_dir() else item.name
        for item in target.iterdir()
    )


@tools.tool(needs_approval=True)
def write_file(context: datatypes.RunContext, path: str, content: str) -> str:
    """把文字寫入專案資料夾裡的檔案，檔案已存在時會覆寫。

    Args:
        path: 相對於專案根目錄的檔案路徑。
        content: 要寫入的完整內容。
    """
    target = resolve_path(context, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"已寫入 {path}（{len(content)} 個字元）"


TOOLS = [read_file, list_dir, write_file]
