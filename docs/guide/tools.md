# 撰寫工具

工具就是普通的函式。參數格式來自型別，說明來自 docstring 或 description——**這段說明就是給模型的使用手冊**，寫得越具體，模型越知道什麼時候呼叫、參數怎麼填。

不想寫程式的話，也可以直接用[內建工具](/guide/builtin-tools)或現成的 [MCP 伺服器](/guide/mcp)。

## Python 版：`@harness.tool`

在函式上加 `@harness.tool` 就完成了，不需要另外的工具伺服器：

```python
# tools/report.py
import harness
from my_project import reports  # 要公開給 agent 的程式碼


@harness.tool
def build_report(month: str, detailed: bool = False) -> str:
    """產生某個月份的銷售報告。

    Args:
        month: 月份，格式為 YYYY-MM。
        detailed: 是否包含每筆交易的明細。
    """
    if not reports.has_data(month):
        raise harness.ToolError(f"{month} 沒有資料，請先用 list_months 確認")
    return reports.build(month, detailed)
```

- **出錯就直接 `raise`。** 錯誤訊息會送回給模型，它會自己修正參數或換個做法，對話不會中斷。用 `harness.ToolError` 時模型只會看到你寫的訊息；其他例外會加上類別名稱（例如 `ValueError: ...`）。
- **回傳值**可以是字串、dict、list 或 pydantic 模型，框架會自動轉成文字給模型。
- 同步與 `async` 函式都支援。多個工具被同時呼叫時會平行執行。
- 加上 `@harness.tool` 之後，函式仍然可以像原本一樣直接呼叫。
- 專案根目錄會自動加入 Python 的搜尋路徑，`tools/` 裡可以直接 import 專案的其他套件。檔名（`report`）就是 toolset 名稱。

既有函式的簽名和 docstring 已經夠清楚時，可以不寫包裝函式：

```python
build_report = harness.tool(reports.build_report)
```

### 需要使用者同意的工具

會寄信、寫檔、改資料的工具加上 `needs_approval=True`，執行前會先問使用者：

```python
@harness.tool(needs_approval=True)
def send_report(to: str, month: str) -> str:
    """把報告寄給指定的收件人。"""
    ...
```

也可以不改程式，在 `harness.yaml` 用名稱樣式指定，見[權限](/guide/permissions)。

### 取得執行環境

工具需要目前的使用者、資料庫連線這類資訊時，加一個型別為 `harness.RunContext` 的參數。這個參數由框架填入，模型看不到：

```python
@harness.tool
def my_orders(context: harness.RunContext) -> list[dict]:
    """列出目前使用者的訂單。"""
    return context.deps["db"].orders_of(context.deps["user_id"])
```

`context.deps` 就是呼叫 `harness.run(..., deps=...)` 時傳進來的物件。

::: warning 使用者身分不要讓模型填
使用者身分應該從 `context.deps` 取得，不要做成工具的參數，否則模型可以填別人的 ID。
:::

## TypeScript 版：`tools/*.ts`

`tools/` 底下每個 `.ts` 檔匯出的工具會自動載入，檔名就是 toolset 名稱。Node.js 會直接執行 TypeScript，不需要先編譯。

最簡單的寫法不需要安裝任何套件，匯出一個物件即可：

```ts
// tools/text.ts
export const wordCount = {
  name: "word_count",
  description: "計算一段文字的字元數與行數。",
  parameters: {
    type: "object",
    properties: { text: { type: "string", description: "要計算的文字" } },
    required: ["text"],
  },
  execute: ({ text }: { text: string }) => ({
    characters: text.length,
    lines: text.split("\n").length,
  }),
};
```

專案裡有安裝 `agent-harness` 與 `zod` 時，改用 `tool()` 可以自動驗證參數並推導型別：

```ts
import { tool, ToolError } from "agent-harness";
import { z } from "zod";

export const buildReport = tool({
  name: "build_report",
  description: "產生某個月份的銷售報告。",
  parameters: z.object({
    month: z.string().describe("月份，格式為 YYYY-MM"),
    detailed: z.boolean().default(false).describe("是否包含交易明細"),
  }),
  needsApproval: false,
  execute: async ({ month, detailed }, context) => {
    if (!hasData(month)) throw new ToolError(`${month} 沒有資料`);
    return buildReportFor(month, detailed);
  },
});
```

`execute` 的第二個參數 `context` 帶有 `workdir`、`sessionId`、`deps`（呼叫 `run` 時傳進來的物件）與 `signal`（使用者中斷時觸發）。工具出錯就直接 `throw`，錯誤訊息會送回給模型，對話不會中斷。

## TypeScript 版接 Python 寫的工具

TypeScript 版用 [MCP](https://modelcontextprotocol.io/)（工具伺服器的通用標準）接 Python 寫的工具。參數格式來自型別註解，說明來自 docstring：

```python
# tools/server.py
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from my_project import reports

mcp = MCPServer("report")


@mcp.tool()
def build_report(month: str, detailed: bool = False) -> str:
    """產生某個月份的銷售報告。

    Args:
        month: 月份，格式為 YYYY-MM。
        detailed: 是否包含每筆交易的明細。
    """
    if not reports.has_data(month):
        raise ToolError(f"{month} 沒有資料，請先用 list_months 確認")
    return reports.build(month, detailed)


if __name__ == "__main__":
    mcp.run()
```

在 `harness.yaml` 的 `mcpServers` 設定啟動指令，harness 會自動啟動並連上它（見[連接 MCP 伺服器](/guide/mcp)）。

- **要讓模型看到的錯誤用 `ToolError`。** 其他例外只會讓模型看到「執行失敗」，看不到內容。
- 伺服器在專案根目錄啟動，並繼承你目前的環境變數（虛擬環境、資料庫連線設定都拿得到）。
- 要標記需要同意的工具，用 `@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))`。
- 這是 `mcp` 套件 2.x 版的寫法。

## 設計工具的原則

- **說明要具體。** 模型只看得到工具名稱、docstring 和參數說明。格式（`YYYY-MM`）、單位、範例值都寫進去。
- **錯誤訊息要告訴模型下一步。** `ToolError("2026-13 沒有資料，請先用 list_months 確認")` 模型會照著做。
- **回傳模型需要的資料就好。** 回傳內容會佔用模型的 context，查詢類工具回傳整理過的摘要或前幾筆，不要回傳整張資料表。
- **有副作用的工具要先問使用者。** 見[權限](/guide/permissions)。
