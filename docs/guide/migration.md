# 為既有專案加上 agent

這份指南適用於已經有一套程式碼（報表、資料查詢、內部工具…），想讓
agent 透過工具使用它的功能的情況。目標：原本的程式碼幾乎不動，只在
外面加一層。

從零開始建立新的 agent，請直接看 [快速開始](/guide/quickstart)。

完整的例子在 [`examples/report_bot`](https://github.com/xian-ai-1057/AgentHarness/tree/main/examples/report_bot)：
`legacy/` 是「原本就存在」的 Python 程式碼，`tools/` 是新加的那一層。

## 先選版本

| 你的專案 | 建議 | 工具怎麼寫 |
|---|---|---|
| Python | [Python 版](/guide/introduction#兩個版本)（`pyharness`） | `tools/*.py`，函式加 `@harness.tool` |
| Python，但要和 TypeScript 專案共用同一套 agent | TypeScript 版（`harness`） | Python 端寫 MCP 伺服器 |
| TypeScript / Node.js | TypeScript 版（`harness`） | `tools/*.ts` |

兩個版本的專案格式相同，之後要換版本只需要換工具那一層。下面先講共同
的步驟，再分別說明兩種工具寫法。

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
└── tools/reports.py        ← 新增：把既有函式包成工具
```

## 步驟

### 1. 安裝並建立範本

Python 版：

```bash
pip install "git+https://github.com/xian-ai-1057/AgentHarness.git#subdirectory=python"
cd my_project
pyharness new .
```

TypeScript 版：先依[安裝](/guide/install)裝好
`harness` 指令，然後：

```bash
cd my_project
pip install "mcp>=2"        # Python 端的工具伺服器套件
harness new .
```

`new` 只會建立不存在的檔案，不會覆寫任何既有檔案。模型的連線設定見
各版本 README 的「模型設定」。

### 2. 列出專案「能做的事」

把專案的功能列成清單，每一項就是一個工具的候選。以報告產生器為例：

| 使用者會說的話 | 需要的功能 | 既有的函式 |
|---|---|---|
| 「有哪些月份的資料？」 | 列出月份 | （要新寫幾行） |
| 「十月賣了多少？」 | 取得加總 | `load_sales` + `summarize` |
| 「幫我出十月的報告」 | 產生報告 | `render_markdown` |

從使用者的問題出發，而不是從既有函式出發。一個工具應該對應一件使用者
在意的事，內部可以串接好幾個既有函式。

### 3. 把功能包成工具

#### 寫法 A：Python 版（直接 import）

```python
# tools/reports.py
import harness
from my_project import reports


@harness.tool
def get_sales_summary(month: str) -> dict[str, int]:
    """取得某個月份各產品的銷售總額。

    Args:
        month: 月份，格式為 YYYY-MM，例如 2026-10。
    """
    records = reports.load_sales(month)
    if not records:
        raise harness.ToolError(
            f"{month} 沒有銷售資料，請先用 list_months 確認"
        )
    return reports.summarize(records)
```

專案根目錄會自動加入 Python 的搜尋路徑，所以可以直接
`import my_project`。檔名（`reports`）就是 toolset 名稱。

#### 寫法 B：TypeScript 版（MCP 伺服器）

```python
# tools/server.py
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

`harness.yaml` 裡對應的設定（`harness new` 已經幫你寫好）。伺服器名稱
就是 toolset 名稱：

```yaml
mcpServers:
  tools:
    command: python3
    args: [tools/server.py]
```

`harness chat` 啟動時會自動執行這個伺服器，結束時自動關閉。專案使用
虛擬環境時，先啟用虛擬環境再執行 `harness chat`，或把 `command` 改成
虛擬環境裡的 Python，例如 `.venv/bin/python`。

#### 兩個版本都要能跑

兩種寫法可以並存，就像 `examples/report_bot`：`tools/report.py`（寫法
A）與 `tools/report_server.py`（寫法 B）包裝同一份既有程式碼，
`mcpServers` 裡的伺服器也叫 `report`。agent 檔案寫 `tools: [report]`，
兩個版本各自找到自己的那一份。Python 版會自動略過被 `mcpServers`
當成伺服器執行的檔案。

### 4. 寫 agent 的指示

把原本寫死在程式裡的 prompt 搬到 `agents/main.md`。原本用程式控制的
流程（先查資料、再判斷、再產生報告）改用文字描述，讓模型自己決定
呼叫順序：

```markdown
---
name: main
tools: [reports]
---
你是銷售報告助手。

- 需要數字時一定要呼叫工具取得，不要自己估算。
- 使用者沒說月份時，先用 list_months 確認有哪些月份再詢問。
```

整個專案共用的背景知識（名詞定義、單位、格式慣例）寫在 `AGENTS.md`。

原本的專案如果有好幾個各司其職的流程（例如先分類問題、再交給不同的
處理程式），可以拆成多個 agent，用 subagents 或 handoffs 串起來。目前
只有 Python 版支援，寫法見
[多個 agent 協作](/advanced/multi-agent)。

### 5. 測試

```bash
pyharness chat      # 或 harness chat
```

工具本身是普通的 Python 函式，可以直接用 pytest 測試。Python 版要測試
「agent 加工具」的整體行為而不花錢呼叫模型，可以用假的模型函式，寫法見
[`python/tests/test_example_report_bot.py`](https://github.com/xian-ai-1057/AgentHarness/blob/main/python/tests/test_example_report_bot.py)。

TypeScript 版的工具伺服器啟動失敗時，harness 會顯示它的錯誤訊息（通常
是 import 錯誤或少裝套件）。要看伺服器執行期間的完整 log：

```bash
HARNESS_DEBUG=1 harness chat
```

## 設計工具的原則

**說明要具體。** 模型只看得到工具名稱、docstring 和參數說明。格式
（`YYYY-MM`）、單位、範例值都寫進去。

**錯誤訊息要告訴模型下一步，而且用 `ToolError`。**
`raise ToolError("2026-13 沒有資料，請先用 list_months 確認")` 模型會
照著做。在 MCP 伺服器裡這點特別重要：一般的例外（例如 `ValueError`）
模型只會看到「執行失敗」，看不到你寫的訊息。

**回傳模型需要的資料就好。** 回傳內容會佔用模型的 context。查詢類工具
回傳整理過的摘要或前幾筆，不要回傳整張資料表。

**有副作用的工具要先問使用者。** 會寄信、改資料庫、花錢的工具，在
`harness.yaml` 的 `permissions.ask` 加上名稱樣式（兩個版本通用），或在
定義工具時標記（Python 版 `needs_approval=True`，MCP 伺服器
`destructiveHint=True`）。

**使用者身分不要讓模型填。** Python 版從 `RunContext.deps` 取得目前的
使用者、權限、資料庫連線，不要做成工具的參數，否則模型可以填別人的
ID。MCP 伺服器拿不到 `deps`，需要依使用者區分資料的服務請用 Python 版。

**執行很久的工具要調整逾時（MCP 伺服器）。** 單次工具呼叫預設最多等
300 秒，產生大型報告需要更久的話，在伺服器設定加上 `timeoutSeconds`：

```yaml
mcpServers:
  tools:
    command: python3
    args: [tools/server.py]
    timeoutSeconds: 1800
```

## 嵌進既有的網頁服務

既有專案已經有 Python 網頁介面（FastAPI、Streamlit、Flask）時，用
Python 版直接在請求處理函式裡呼叫 `harness.run_stream(...)`，寫法見
[嵌進既有的網頁服務](/guide/embed#嵌進既有的網頁服務)。Node.js 服務
則用 TypeScript 版的 `runStream`。

## 其他語言的專案

- **其他語言**：任何語言只要有 MCP SDK（Go、Java、C#、Rust 等都有），
  做法和寫法 B 相同，把 `command` 換成啟動那個程式的指令，搭配
  TypeScript 版使用。
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

- [ ] `harness.yaml`、`agents/main.md`、`tools/` 已建立
- [ ] 每個使用者會用到的功能都有對應的工具
- [ ] 每個工具都有具體的 docstring 與參數說明
- [ ] 預期中的錯誤都用 `ToolError`，訊息有說明下一步
- [ ] 有副作用的工具已列入 `permissions.ask` 或在定義時標記
- [ ] 使用者身分從 `RunContext.deps` 取得，不是工具參數（Python 版）
- [ ] 原本寫死的 prompt 已搬到 `agents/*.md`
- [ ] `.harness/` 已加入 `.gitignore`
- [ ] 實際聊天問過幾個常見問題
