"""既有專案的功能：讀取銷售資料並產生報告。

這個檔案代表「移轉前就存在」的程式碼，完全不知道 harness 的存在，
移轉時也不需要修改它。
"""

import csv
import pathlib

DATA_FILE = pathlib.Path(__file__).parent.parent / "data" / "sales.csv"


def load_sales(month: str) -> list[dict[str, str]]:
    """讀取某個月份的銷售紀錄。

    Args:
        month: 月份，格式為 YYYY-MM。

    Returns:
        該月份的銷售紀錄。
    """
    with DATA_FILE.open(encoding="utf-8", newline="") as file:
        return [row for row in csv.DictReader(file) if row["month"] == month]


def summarize(records: list[dict[str, str]]) -> dict[str, int]:
    """把銷售紀錄依產品加總金額。

    Args:
        records: 銷售紀錄。

    Returns:
        {產品名稱: 總金額}。
    """
    totals: dict[str, int] = {}
    for row in records:
        totals[row["product"]] = totals.get(row["product"], 0) + int(
            row["amount"]
        )
    return totals


def render_markdown(month: str, totals: dict[str, int]) -> str:
    """把加總結果排成 Markdown 報告。

    Args:
        month: 月份。
        totals: 各產品的總金額。

    Returns:
        Markdown 格式的報告。
    """
    lines = [f"# {month} 銷售報告", "", "| 產品 | 金額 |", "|---|---|"]
    for product, amount in sorted(totals.items()):
        lines.append(f"| {product} | {amount:,} |")
    lines.append(f"| **合計** | **{sum(totals.values()):,}** |")
    return "\n".join(lines)
