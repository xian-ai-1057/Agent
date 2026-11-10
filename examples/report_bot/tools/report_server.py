"""把既有的報告功能公開成工具（MCP 伺服器）。

這一層只做三件事：import 既有函式、決定要讓 agent 看到哪些參數、
寫清楚說明（模型靠 docstring 決定什麼時候呼叫哪個工具）。

harness 啟動時會依 harness.yaml 的 mcpServers 設定自動執行這個檔案。
"""

import pathlib
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# 讓這個檔案可以 import 專案根目錄底下既有的套件。
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from legacy import sales_report  # noqa: E402

mcp = MCPServer("report")


@mcp.tool()
def list_months() -> list[str]:
    """列出有銷售資料的月份。"""
    with sales_report.DATA_FILE.open(encoding="utf-8") as file:
        rows = file.read().splitlines()[1:]
    return sorted({row.split(",")[0] for row in rows})


@mcp.tool()
def get_sales_summary(month: str) -> dict[str, int]:
    """取得某個月份各產品的銷售總額。

    Args:
        month: 月份，格式為 YYYY-MM，例如 2026-10。
    """
    records = sales_report.load_sales(month)
    if not records:
        # ToolError 的訊息會原樣送給模型；其他例外的內容不會。
        raise ToolError(f"{month} 沒有銷售資料，請先用 list_months 確認")
    return sales_report.summarize(records)


@mcp.tool()
def build_sales_report(month: str) -> str:
    """產生某個月份的完整 Markdown 銷售報告。

    Args:
        month: 月份，格式為 YYYY-MM，例如 2026-10。
    """
    records = sales_report.load_sales(month)
    if not records:
        raise ToolError(f"{month} 沒有銷售資料，請先用 list_months 確認")
    return sales_report.render_markdown(month, sales_report.summarize(records))


if __name__ == "__main__":
    mcp.run()
