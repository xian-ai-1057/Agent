# 把既有專案移轉到 Harness

目標：原本的程式碼幾乎不動，只在外面加一層，讓 agent 可以透過工具
使用它的功能。

完整的例子在 [`examples/report_bot`](../examples/report_bot)：
`legacy/` 是「原本就存在」的 Python 程式碼，`tools/report_server.py` 是
新加的那一層。

## 移轉後的樣子

把 Harness 的檔案直接放在既有專案的根目錄：

```
my_project/                 ← 既有專案
├── my_project/             既有的程式碼（不用改）
│   ├── reports.py
│   └── db.py
├── harness.yaml            ← 新增
├── AGENTS.md               ← 新增
├── agents/main.md          ← 新增
└── tools/server.py         ← 新增：把既有函式公開成工具
```

`tools/server.py` 是一個小小的 Python 程式（MCP 伺服器）。`harness chat`
啟動時會自動執行它，結束時自動關閉，你不需要另外啟動。

## 步驟

### 1. 安裝並建立範本

先依 README 的「安裝」裝好 `harness` 指令，然後：

```bash
cd my_project
pip install "mcp>=2"        # Python 端的工具伺服器套件
harness new .
```

`harness new .` 只會建立不存在的檔案，不會覆寫任何既有檔案。

模型的連線設定見 README 的「設定模型」。

### 2. 列出專案「能做的事」

把專案的功能列成清單，每一項就是一個工具的候選。以報告產生器為例：

| 使用者會說的話 | 需要的功能 | 既有的函式 |
|---|---|---|
| 「有哪些月份的資料？」 | 列出月份 | （要新寫幾行） |
| 「十月賣了多少？」 | 取得加總 | `load_sales` + `summarize` |
| 「幫我出十月的報告」 | 產生報告 | `render_markdown` |

從使用者的問題出發，而不是從既有函式出發。一個工具應該對應一件使用者
在意的事，內部可以串接好幾個既有函式。

### 3. 把功能公開成工具

編輯 `tools/server.py`：

```python
import pathlib
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# 讓這個檔案可以 import 專案根目錄底下既有的套件。
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from my_project import reports  # noqa: E402

mcp = MCPServer("my_project")


@mcp.tool()
def get_sales_summary(month: str) -> dict[str, int]:
    """取得某個月份各產品的銷售總額。

    Args:
        month: 月份，格式為 YYYY-MM，例如 2026-10。
    """
    records = reports.load_sales(month)
    if not records:
        raise ToolError(f"{month} 沒有銷售資料，請先用 list_months 確認")
    return reports.summarize(records)


if __name__ == "__main__":
    mcp.run()
```

專案已經用 `pip install -e .` 安裝過的話，不需要 `sys.path` 那一行。

`harness.yaml` 裡對應的設定（`harness new` 已經幫你寫好）：

```yaml
mcpServers:
  tools:
    command: python3
    args: [tools/server.py]
```

專案使用虛擬環境時，先啟用虛擬環境再執行 `harness chat`，或把
`command` 改成虛擬環境裡的 Python，例如 `.venv/bin/python`。

### 4. 寫 agent 的指示

把原本寫死在程式裡的 prompt 搬到 `agents/main.md`。原本用程式控制的
流程（先查資料、再判斷、再產生報告）改用文字描述，讓模型自己決定
呼叫順序：

```markdown
---
name: main
tools: [tools]
---
你是銷售報告助手。

- 需要數字時一定要呼叫工具取得，不要自己估算。
- 使用者沒說月份時，先用 list_months 確認有哪些月份再詢問。
```

整個專案共用的背景知識（名詞定義、單位、格式慣例）寫在 `AGENTS.md`。

### 5. 測試

```bash
harness chat
```

工具本身是普通的 Python 函式，可以直接用 pytest 測試。

伺服器啟動失敗時，harness 會顯示它的錯誤訊息（通常是 import 錯誤或
少裝套件）。要看伺服器執行期間的完整 log：

```bash
HARNESS_DEBUG=1 harness chat
```

## 設計工具的原則

**說明要具體。** 模型只看得到工具名稱、docstring 和參數說明。格式
（`YYYY-MM`）、單位、範例值都寫進去。

**錯誤要用 `ToolError`，而且告訴模型下一步。**
`raise ToolError("2026-13 沒有資料，請先用 list_months 確認")` 模型會
照著做。用一般的 `ValueError` 的話，模型只會看到「執行失敗」，看不到
你寫的訊息。

**回傳模型需要的資料就好。** 回傳內容會佔用模型的 context。查詢類工具
回傳整理過的摘要或前幾筆，不要回傳整張資料表。

**有副作用的工具要先問使用者。** 會寄信、改資料庫、花錢的工具，在
`harness.yaml` 的 `permissions.ask` 加上名稱樣式，或在 Python 端標記
`destructiveHint=True`（寫法見 README）。

**執行很久的工具要調整逾時。** 單次工具呼叫預設最多等 300 秒，產生
大型報告需要更久的話，在伺服器設定加上 `timeoutSeconds`：

```yaml
mcpServers:
  tools:
    command: python3
    args: [tools/server.py]
    timeoutSeconds: 1800
```

## 非 Python 的專案

- **TypeScript / JavaScript 專案**：不需要伺服器，直接在 `tools/*.ts`
  匯出工具，寫法見 README 的「用 TypeScript 寫工具」。
- **其他語言**：任何語言只要有 MCP SDK（Go、Java、C#、Rust 等都有），
  做法和 Python 相同，把 `command` 換成啟動那個程式的指令。
- **已經是 HTTP 服務的專案**：伺服器支援 MCP 的 Streamable HTTP 時，
  設定改用 `url`：

```yaml
mcpServers:
  backend:
    url: https://my-service.example.com/mcp
    headers:
      Authorization: Bearer ...
```

## 檢查清單

- [ ] `harness.yaml`、`agents/main.md`、`tools/server.py` 已建立
- [ ] 每個使用者會用到的功能都有對應的工具
- [ ] 每個工具都有具體的 docstring 與參數說明
- [ ] 預期中的錯誤都用 `ToolError`，訊息有說明下一步
- [ ] 有副作用的工具已列入 `permissions.ask`
- [ ] 原本寫死的 prompt 已搬到 `agents/*.md`
- [ ] `.harness/` 已加入 `.gitignore`
- [ ] `harness chat` 實際問過幾個常見問題
