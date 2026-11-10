"""內建的 toolsets，可在 agent 設定的 tools 欄位用名稱引用。"""

from harness.builtin import files

TOOLSETS = {
    "files": files.TOOLS,
}
