"""內建的 toolsets 與 skills。

toolsets 可在 agent 設定的 tools 欄位用名稱引用：
    files  讀取、搜尋、寫入、修改專案資料夾裡的檔案
    shell  執行命令列指令（需要使用者同意）
    plan   列出並更新多步驟工作的計畫
    web    讀取網頁（需要使用者同意）

內建工具不會自動給 agent：agent 沒寫 tools 欄位時只會拿到專案自己的
工具，要用內建工具得明確列出 toolset 名稱。專案裡有同名的 toolset 時，
以專案的為準。

內建 skills 放在 builtin/skills/ 底下（格式和專案的 skills/ 相同），
同樣要在 agent 的 skills 欄位明確列出才會啟用，見 project 模組。
"""

import pathlib

from harness import tools
from harness.builtin import files
from harness.builtin import plan
from harness.builtin import shell
from harness.builtin import web

TOOLSETS: dict[str, list[tools.Tool]] = {
    "files": files.TOOLS,
    "shell": shell.TOOLS,
    "plan": plan.TOOLS,
    "web": web.TOOLS,
}

SKILLS_DIR = pathlib.Path(__file__).parent / "skills"


def is_builtin_toolset(name: str, items: list[tools.Tool]) -> bool:
    """判斷某個 toolset 是不是內建的那一個（而不是專案裡同名的 toolset）。

    Args:
        name: toolset 名稱。
        items: toolset 裡的工具。

    Returns:
        是內建 toolset 時為 True。
    """
    return TOOLSETS.get(name) is items
