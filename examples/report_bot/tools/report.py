"""把既有的報告功能包成工具（Python 版 harness 使用）。

這一層只做三件事：import 既有函式、決定要讓 agent 看到哪些參數、
寫清楚說明（模型靠 docstring 決定什麼時候呼叫哪個工具）。

TypeScript 版 harness 使用的是同一個資料夾裡的 report_server.py。
"""

from legacy import sales_report

import harness


@harness.tool
def list_months() -> list[str]:
    """列出有銷售資料的月份。"""
    with sales_report.DATA_FILE.open(encoding="utf-8") as file:
        rows = file.read().splitlines()[1:]
    return sorted({row.split(",")[0] for row in rows})


@harness.tool
def get_sales_summary(month: str) -> dict[str, int]:
    """取得某個月份各產品的銷售總額。

    Args:
        month: 月份，格式為 YYYY-MM，例如 2026-10。
    """
    records = sales_report.load_sales(month)
    if not records:
        raise harness.ToolError(
            f"{month} 沒有銷售資料，請先用 list_months 確認"
        )
    return sales_report.summarize(records)


@harness.tool
def build_sales_report(month: str) -> str:
    """產生某個月份的完整 Markdown 銷售報告。

    Args:
        month: 月份，格式為 YYYY-MM，例如 2026-10。
    """
    records = sales_report.load_sales(month)
    if not records:
        raise harness.ToolError(
            f"{month} 沒有銷售資料，請先用 list_months 確認"
        )
    totals = sales_report.summarize(records)
    return sales_report.render_markdown(month, totals)
