# Agent Harness（Python 版）

Agent Harness 的 Python 實作：完全開源、不綁定模型供應商的 agent harness。
為誰而做見[根目錄的 README](../README.md#為誰而做)。

和根目錄的 TypeScript 版使用**同一種專案格式**（同一份 `harness.yaml` 與
`agents/*.md`）。兩個版本的比較與選擇方式見
[根目錄的 README](../README.md#兩個版本)。

Python 版的特點：

- **工具就是普通的 Python 函式。** 在函式上加 `@harness.tool` 就完成，
  不需要另外的工具伺服器，也可以直接包裝現有的程式碼。
- **可以嵌進 Python 服務。** FastAPI、Streamlit、Flask 裡直接
  `import harness`。
- **模型透過 litellm 連接**，Claude、OpenAI、Gemini、Hermes、Ollama 上
  的本地模型都能用。
- **多個 agent 可以協作。** 把工作交給 subagent，或把整段對話轉交
  （handoff）給另一個 agent。
- **正式上線的 agent 常用的能力**：[知識庫（RAG）](#知識庫rag)、
  [Skills](#skills)、[長期記憶](#長期記憶)、[訊息防護](#訊息防護)。
- **長時間的對話不會塞爆上下文**：[對話壓縮](#對話壓縮)。
- **參考 Claude Code 與 Codex 的[內建工具](#內建工具)與
  [內建 skills](#內建-skills)**：讀取、搜尋與修改檔案、執行指令、追蹤
  計畫、讀取網頁，以及 code-review、debugging 等現成的 skill。
- **可以連接現成的 MCP 伺服器**，和 TypeScript 版用同一份設定：
  [連接 MCP 伺服器](#連接-mcp-伺服器)。

## 安裝

需要 Python 3.11 以上。

```bash
pip install "git+https://github.com/xian-ai-1057/AgentHarness.git#subdirectory=python"
```

要連接 MCP 伺服器的話，另外安裝 MCP 套件：`pip install "mcp>=2"`。

開發這個框架本身：

```bash
git clone https://github.com/xian-ai-1057/AgentHarness.git
cd AgentHarness/python
pip install -e ".[dev]"
pytest
```

指令名稱是 `pyharness`（TypeScript 版是 `harness`，兩者可以同時安裝）。
也可以用 `python -m harness` 執行。

## 快速開始

```bash
pyharness new my_bot        # 建立專案範本
cd my_bot

export ANTHROPIC_API_KEY=...                      # 你所用模型的金鑰
export HARNESS_MODEL=anthropic/claude-opus-5-5    # litellm 的模型名稱

pyharness chat              # 開始聊天
```

想先看完整的例子，可以跑 `examples/report_bot`：它把一個銷售報告程式
包成三個工具，示範怎麼為現有的程式碼加上 agent。

```bash
cd examples/report_bot
pyharness chat
你> 十月賣得怎麼樣？幫我出一份報告
```

## 專案格式

```
my_bot/
├── harness.yaml      名稱、模型、預設 agent、權限、MCP 伺服器、記憶、防護
├── AGENTS.md         專案共用的背景知識，接在每個 agent 的指示後面
├── agents/
│   └── main.md       一個檔案一個 agent
├── tools/
│   └── report.py     用 @harness.tool 包裝的函式
├── skills/           需要時才載入的能力單元（可省略）
│   └── refund/
│       └── SKILL.md
├── knowledge/        知識庫文件（可省略）
└── .harness/         對話紀錄、記憶、索引快取（自動產生，不用進版控）
```

`harness.yaml`：

```yaml
name: my_bot
defaultAgent: main
model: anthropic/claude-opus-5-5     # 可省略，改用 HARNESS_MODEL

permissions:
  mode: ask
```

`agents/main.md` 的開頭是設定，內文就是給模型的指示：

```markdown
---
name: main
description: 回答銷售問題並產生銷售報告
tools:
  - report        # tools/report.py 裡所有的工具
  - files         # 內建的檔案工具
---
你是銷售報告助手。需要數字時一定要呼叫工具取得，不要自己估算。
```

`tools` 可以寫 toolset 名稱（`tools/` 底下的檔名、`mcpServers` 的
伺服器名稱，或[內建的](#內建工具) `files`、`shell`、`plan`、`web`）或
單一工具名稱。整個欄位不寫的話，agent 可以使用專案裡所有的工具（不含
內建工具）。

專案根目錄會自動加入 Python 的搜尋路徑，所以 `tools/` 裡的檔案可以
直接 import 專案裡的其他套件。

## 寫工具

在函式上加 `@harness.tool` 就完成了。給模型看的參數格式來自型別註解，
工具與參數的說明來自 docstring。

```python
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

幾個重點：

- **docstring 就是給模型的使用說明。** 模型靠它決定什麼時候呼叫、參數
  怎麼填，寫得越具體越好。
- **回傳值**可以是字串、dict、list 或 pydantic 模型，框架會自動轉成
  文字給模型。
- **出錯就直接 `raise`。** 錯誤訊息會送回給模型，它會自己修正參數或
  換個做法，對話不會中斷。用 `harness.ToolError` 時模型只會看到你寫的
  訊息；其他例外會加上類別名稱（例如 `ValueError: ...`）。
- 同步與 `async` 函式都支援。多個工具被同時呼叫時會平行執行。
- 加上 `@harness.tool` 之後，函式仍然可以像原本一樣直接呼叫。

既有函式的簽名和 docstring 已經夠清楚時，可以不寫包裝函式：

```python
build_report = harness.tool(reports.build_report)
```

### 需要使用者同意的工具

會寄信、寫檔、改資料的工具，加上 `needs_approval=True`，執行前會先問
使用者：

```python
@harness.tool(needs_approval=True)
def send_report(to: str, month: str) -> str:
    """把報告寄給指定的收件人。"""
    ...
```

也可以不改程式，在 `harness.yaml` 用名稱樣式指定：

```yaml
permissions:
  ask: ["send_*", "delete_*"]
```

### 取得執行環境

工具需要目前的使用者、資料庫連線這類資訊時，加一個型別為
`harness.RunContext` 的參數。這個參數由框架填入，模型看不到：

```python
@harness.tool
def my_orders(context: harness.RunContext) -> list[dict]:
    """列出目前使用者的訂單。"""
    return context.deps["db"].orders_of(context.deps["user_id"])
```

`context.deps` 就是呼叫 `harness.run(..., deps=...)` 時傳進來的物件。
使用者身分應該從這裡取得，不要做成工具的參數，否則模型可以填別人的
ID。

## 內建工具

不用寫任何程式就能給 agent 的工具，設計參考 Claude Code 與 Codex，和
TypeScript 版的名稱、參數與輸出都相同。內建工具**要在 agent 的 `tools`
明確列出**才會啟用；專案裡有同名的 toolset（例如 `tools/web.py` 或名為
`web` 的 MCP 伺服器）時以專案的為準。

| toolset | 工具 | 用途 | 需要同意 |
|---|---|---|---|
| `files` | `read_file` | 讀取文字檔；可用 `offset`、`limit` 只讀其中幾行 | |
| | `list_dir` | 列出資料夾內容 | |
| | `find_files` | 用 glob 樣式找檔案，例如 `*.py`、`src/**/*.ts` | |
| | `search_files` | 用正規表示式搜尋檔案內容，回傳「路徑:行號: 內容」 | |
| | `write_file` | 寫入整個檔案 | ✓ |
| | `edit_file` | 把一段原文精準換成新文字，不必重寫整個檔案 | ✓ |
| `shell` | `run_command` | 在專案資料夾執行指令（測試、建置、git…），回傳結束代碼與輸出 | ✓ |
| `plan` | `update_plan` | 列出多步驟工作的計畫並更新每一步的狀態 | |
| `web` | `web_fetch` | 讀取網址，HTML 轉成保留標題、清單與連結的純文字 | ✓ |

```markdown
---
name: dev
description: 幫忙修改程式、跑測試
tools: [files, shell, plan]
skills: [code-review, debugging]
---
修改前先讀檔；改完一定要跑測試確認。
```

- 所有路徑都限制在專案資料夾裡；搜尋會略過 `.git`、`node_modules`、
  `.harness`、`.venv` 等資料夾，也不跟著符號連結走。
- `edit_file` 要求原文只出現一次（或設定 `replace_all`），並保留檔案
  原本的換行字元。
- `run_command` 沒有標準輸入、有逾時（預設 120 秒，最多 600 秒），
  逾時或使用者中斷時會連同子行程一起終止。
- `update_plan` 不存任何狀態：最新一次呼叫的參數就是目前的計畫。
- `web_fetch` 會把網址送到外部，所以預設需要同意；信任的話加上
  `permissions.allow: [web_fetch]`。它會使用 `HTTPS_PROXY` 等代理設定。

在程式裡使用：

```python
from harness import builtin

agent = harness.Agent(
    name="dev",
    instructions="...",
    tools=[*builtin.TOOLSETS["files"], *builtin.TOOLSETS["shell"]],
)
```

## 連接 MCP 伺服器

除了直接 import 的工具，agent 也可以使用現成的
[MCP](https://modelcontextprotocol.io/) 伺服器提供的工具，例如其他語言寫的
服務、第三方提供的工具伺服器。設定方式與 TypeScript 版相同，需要先安裝
MCP 套件：

```bash
pip install "mcp>=2"
```

`harness.yaml`：

```yaml
mcpServers:
  github:                   # 伺服器名稱，也是這組工具的 toolset 名稱
    command: npx            # 本機程式，透過 stdio 溝通
    args: ["-y", "@modelcontextprotocol/server-github"]
    env: {LOG_LEVEL: info}  # 選用，加在目前的環境變數之上
    timeoutSeconds: 120     # 選用，單次工具呼叫最多等幾秒，預設 300
  search:
    url: https://example.com/mcp           # 遠端伺服器（Streamable HTTP）
    headers: {X-Team: sales}               # 選用，附帶的 HTTP 標頭
```

從 Claude Code、Cursor 等工具複製過來的設定可以直接用，裡面的 `type`
（`stdio`、`http`）會被接受；舊的 `sse` 傳輸不支援，請改用伺服器的
Streamable HTTP 端點（通常是 `/mcp`）。

agent 用伺服器名稱引用整組工具，也可以只列出其中幾個工具的名稱：

```markdown
---
name: main
tools: [report, github]
---
```

- **載入專案時就會啟動並連上所有伺服器**，連線在整個專案使用期間共用。
  伺服器無法啟動時，錯誤訊息會附上它輸出到 stderr 的最後一段。
  伺服器平常的 log 不會印到聊天畫面，設定環境變數 `HARNESS_DEBUG=1`
  才會顯示。
- **本機伺服器在專案根目錄執行**，並繼承目前所有的環境變數，所以金鑰
  這類設定用環境變數提供即可，不必寫進 `harness.yaml`。
- 伺服器標記為破壞性操作（`destructiveHint`）的工具，執行前會先問使用者；
  其他工具一樣可以用 `permissions.ask` 規則指定。
- 不同伺服器（或與 `tools/` 裡的工具）有同名的工具時，載入會失敗。
- **`tools/` 裡有與伺服器同名的 Python 檔時，Python 版直接載入那個檔案，
  不啟動伺服器。** 這是同一個專案同時給兩個版本使用時的寫法：
  `examples/report_bot` 的 `report` 伺服器是給 TypeScript 版用的，Python
  版改用 `tools/report.py`，所以執行它不需要安裝 MCP 套件。被
  `mcpServers` 當成伺服器執行的 `tools/*.py` 不會被當成工具模組載入。

用完專案要關閉伺服器。命令列會自動處理；在程式裡使用時，呼叫
`project.close()` 或用 `with`：

```python
with harness.load_project(".") as project:
    result = harness.run_sync(project.agents["main"], "幫我查一下")
```

不使用專案資料夾時，用 `harness.connect_mcp_server` 自己建立連線：

```python
with harness.connect_mcp_server(
    "github", command="npx", args=["-y", "@modelcontextprotocol/server-github"]
) as github:
    agent = harness.Agent(
        name="bot",
        instructions="你是開發助手。",
        tools=[build_report, *github.tools],
    )
    result = harness.run_sync(agent, "列出我最近的 PR")
```

連線在自己的背景執行緒裡維持，不屬於任何一個事件迴圈，所以可以在
FastAPI、Streamlit 這類服務裡建立一次，給之後每個請求共用。

## 在 Python 裡使用

不需要專案資料夾也能用：

```python
import harness

agent = harness.Agent(
    name="report_bot",
    instructions="你是銷售報告助手。",
    tools=[build_report],
    model="anthropic/claude-opus-5-5",
)

result = harness.run_sync(agent, "給我十月的報告")
print(result.output)
```

延續對話：把同一個 `Session` 傳進去。

```python
from harness import sessions

session = sessions.create("data/sessions")  # 會存成 JSONL 檔
await harness.run(agent, "給我十月的報告", session=session)
await harness.run(agent, "跟九月比呢？", session=session)

# 之後（例如下一個 HTTP 請求）再接回來
session = sessions.load("data/sessions", session.id)
```

串流輸出（接到聊天介面時使用）：

```python
async for event in harness.run_stream(agent, "給我十月的報告"):
    if event.type == "text_delta":
        print(event.data["text"], end="")
    elif event.type == "tool_call":
        print(f"\n[呼叫 {event.data['call'].name}]")
```

事件種類：`text_delta`、`message`、`tool_call`、`tool_result`、
`handoff`、`subagent`、`run_end`（另有對話壓縮的 `compaction`、
`compaction_failed`）。`subagent` 是 subagent 內部的進度，見
[看到 subagent 的進度](#看到-subagent-的進度)。

### 嵌進既有的網頁服務

```python
import harness
from harness import sessions

project = harness.load_project(".")
agent = project.agents["main"]


async def handle_chat(user_id: str, session_id: str | None, text: str):
    if session_id:
        session = sessions.load(project.sessions_dir, session_id)
    else:
        session = sessions.create(project.sessions_dir)
    async for event in harness.run_stream(
        agent,
        text,
        session=session,
        deps={"user_id": user_id, "db": get_db()},
        permissions=harness.Permissions(mode="dontAsk"),
        hooks=project.hooks,  # harness.yaml 的 guardrails
        memory=harness.MemoryStore(f"data/memory/{user_id}.json"),
    ):
        if event.type == "text_delta":
            yield event.data["text"]
```

網頁服務沒辦法在終端機詢問使用者，所以這裡用 `dontAsk`：需要同意的
工具會被拒絕。要讓網頁使用者可以按「同意」，就提供自己的
`Permissions(approver=...)`，在裡面等待前端的回應。

`hooks` 與 `memory` 不需要時可以省略。專案有設定 `mcpServers` 時，
服務關閉前呼叫 `project.close()` 結束伺服器。記憶屬於使用者，所以每個使用者
用自己的檔案（見[長期記憶](#長期記憶)）。

## 多個 agent 協作

一個 agent 可以找其他 agent 幫忙。有兩種方式，差別在於對話歸誰：

| | Subagent | Handoff |
|---|---|---|
| 做什麼 | 把一件工作交出去，拿回結果 | 把整段對話交出去 |
| 對方看得到目前的對話嗎 | 看不到，只收到交辦的內容 | 看得到全部 |
| 誰回答使用者 | 原本的 agent | 接手的 agent |
| 使用者的下一則訊息 | 仍由原本的 agent 處理 | 由接手的 agent 處理 |
| 適合 | 查資料、讀長文件這類會佔用大量 context 的工作 | 不同職責的窗口，例如總機轉給帳務 |

在 agent 檔案的開頭列出對方的名稱（`agents/` 底下其他 agent 的 `name`）：

```markdown
---
name: billing
description: 處理帳單與退款
subagents: [researcher]     # 可以把工作交給 researcher
handoffs: [main]            # 可以把對話轉交回 main
---
你是帳務專員。需要查訂單時交給 researcher。
```

或在 Python 裡設定：

```python
researcher = harness.Agent(
    name="researcher",
    description="查詢訂單資料",
    instructions="你是研究員。",
    tools=[find_order],
)
billing = harness.Agent(
    name="billing",
    description="處理帳單與退款",
    instructions="你是帳務專員。需要查訂單時交給 researcher。",
    subagents=[researcher],
)
front = harness.Agent(
    name="front",
    instructions="你是總機。帳單與退款的問題轉交給 billing。",
    handoffs=[billing],
)
billing.handoffs.append(front)  # 讓帳務可以把對話交回總機

session = sessions.create("data/sessions")
result = await harness.run(front, "我要退訂單 123", session=session)
print(result.agent_name)  # billing

# 下一則訊息仍然傳入 front，會自動由 billing 接續
result = await harness.run(front, "謝謝", session=session)
```

共同的規則：

- **`description` 很重要。** 模型靠對方的 `description` 決定要不要找它，
  沒寫的話模型只看得到名稱。
- 兩者都是以工具的形式提供給模型，名稱分別是 `ask_<名稱>` 與
  `transfer_to_<名稱>`，所以權限規則與 hooks 都管得到。例如轉交前先問
  使用者：`permissions.ask: ["transfer_to_*"]`。
- 名稱裡不是英數、底線、連字號的字元（例如中文）會換成底線，所以
  agent 名稱建議用英文。

Subagent 的細節：

- 使用它自己的指示、工具、模型與 `max_turns`；hooks、權限設定與 `deps`
  沿用上一層的。
- 它的 token 用量會算進 `RunResult.usage`。
- 它的對話不會存檔，主 agent 的對話紀錄裡只有交辦內容與它的最後回覆。
- `run_start` / `run_end` 這兩個 hook 不會為 subagent 觸發（它屬於一次
  工具呼叫）；它內部的工具呼叫照常觸發 `pre_tool_use` / `post_tool_use`，
  `context.agent_name` 是 subagent 的名稱。
- subagent 可以再有自己的 subagent，最多 3 層。
- 它在輪數上限內沒做完時，主 agent 會收到錯誤說明與它最後的回覆。

### 看到 subagent 的進度

subagent 工作時，它自己的每個事件（呼叫工具、拿到結果、逐字輸出的
文字等）都會包成 `subagent` 事件，在它還在做的時候就送出，不用等它做完：

```python
async for event in harness.run_stream(main, "十月賣得如何？"):
    if event.type == "subagent":
        inner = event.data["event"]  # subagent 原本的事件
        if inner.type == "tool_call":
            print(f"  {event.data['agent']} 呼叫 {inner.data['call'].name}")
```

| 欄位 | 內容 |
|---|---|
| `agent` | 產生這個事件的 subagent |
| `path` | 由外到內經過的 agent，例如 `["researcher", "helper"]`（subagent 再交給 subagent 時） |
| `call_id` | 最上層那次 `ask_<名稱>` 工具呼叫的 id，可以用來把進度放在對應的 `tool_call` 底下 |
| `event` | subagent 原本的事件（`text_delta`、`tool_call`、`tool_result`、`message`、`run_end` 等） |

同一輪同時交給好幾個 subagent 時，用 `call_id` 區分各自的進度。命令列
與網頁版會把 subagent 呼叫的工具顯示在交辦的步驟底下。

Handoff 的細節：

- **每次都傳入同一個起始 agent 即可。** 目前由誰負責是從對話紀錄推算
  的，所以延續 session 時會自動由接手的 agent 繼續。
- `RunResult.agent_name` 是結束時負責對話的 agent；串流時會收到
  `handoff` 事件（`{"from": ..., "to": ...}`）。
- 要讓對方能把對話交回來，在對方的 `handoffs` 也列出原本的 agent。
- 一輪裡只有第一次轉交會生效。
- 命令列的 `-m` 會套用到專案裡所有的 agent。

## 知識庫（RAG）

讓 agent 從文件裡找答案並附上出處。把文件放進 `knowledge/` 資料夾，
再在 agent 設定 `knowledge: true`：

```
knowledge/
├── hr/
│   └── leave.md
├── product.md
└── manual.pdf        需要 pypdf（安裝時加上 [pdf]，見下方）
```

```markdown
---
name: faq
knowledge: true       # 這個 agent 可以用 search_knowledge 搜尋知識庫
---
你是公司的 FAQ 助手。回答前先搜尋知識庫，並註明出處。
```

agent 會得到 `search_knowledge` 工具，搜尋結果附上來源檔案與所在標題
（PDF 則是頁碼），例如 `[1] 來源：hr/leave.md › 請假規定 › 特休`。

- **檢索方式。** 預設用關鍵字比對（BM25），不需要任何模型，中文也能
  用（以單字與相鄰兩字比對，不用另外斷詞）。設定 `embeddingModel` 後
  再加上語意檢索，兩者的排名合併（hybrid search），同義詞、換句話說
  也找得到。
- **檔案改了會自動更新。** 每次搜尋前會檢查檔案有沒有變動，有就重建
  索引。embedding 存在內建的向量資料庫，沒變的片段不會重新計算（見
  [向量資料庫](#向量資料庫)）。
- **過濾不相關的結果與重新排序。** 可以設定相似度門檻，把與問題不夠
  相似的片段丟掉；也可以用 reranker 重新評估前幾名的相關度（見
  [相似度門檻與 reranker](#相似度門檻與-reranker)）。
- **切片。** 依段落切成約 `chunkSize` 字的片段，不會從段落中間切開。
- 支援 `.md`、`.txt`、`.pdf`；其他格式會略過，用 `pyharness knowledge`
  可以看到哪些檔案被略過。讀取 PDF 需要 pypdf：

  ```bash
  pip install "agent-harness[pdf] @ git+https://github.com/xian-ai-1057/AgentHarness.git#subdirectory=python"
  ```

`harness.yaml`（整個區塊可省略，有 `knowledge/` 資料夾就會啟用）：

```yaml
knowledge:
  path: knowledge                              # 預設
  embeddingModel: openai/text-embedding-3-small  # 省略時只用關鍵字
  chunkSize: 800                               # 預設
  minSimilarity: 0.3            # 選用，語意相似度門檻（需要 embeddingModel）
  reranker: cohere/rerank-v3.5  # 選用，重新排序用的模型
  rerankCandidates: 20          # 交給 reranker 的候選數，預設 20
  minRerankScore: 0.2           # 選用，rerank 分數門檻（需要 reranker）
```

`embeddingModel` 與 `reranker` 是 litellm 的模型名稱（上面的例子是直接
連供應商的寫法）。有設定 `HARNESS_BASE_URL` 時會改連那個端點，和聊天
模型一樣，名稱要寫端點上的名稱；用 LiteLLM Proxy 時就是它設定檔裡的
`model_name`（見[模型設定](#模型設定)）。reranker 走 Proxy 的
`/v1/rerank`。

### 相似度門檻與 reranker

只靠排名的話，知識庫裡就算沒有答案，也一定會回傳「最接近」的幾段，
agent 可能拿不相關的內容硬湊答案。兩種設定可以擋掉這些結果：

- **`minSimilarity`**：語意相似度（餘弦，-1 到 1）低於門檻的片段不回傳，
  關鍵字找到的片段也一樣要過門檻。需要設定 `embeddingModel`。
- **`reranker`**：先用關鍵字與語意檢索取出前 `rerankCandidates` 名，
  再交給 rerank 模型逐段判斷與問題的相關度、重新排序，排名比單純的
  向量相似度準確。可以搭配 **`minRerankScore`** 丟掉分數太低的片段。
  litellm 支援的 rerank 服務都能用（Cohere、Voyage、Jina、Together、
  自架的 Infinity / vLLM 等），例如 `cohere/rerank-v3.5`
  （環境變數 `COHERE_API_KEY`）。

全部被擋下時，agent 收到「知識庫裡沒有找到相關的內容」，會照指示回答
找不到資料，而不是自己編造。

**門檻的合適值依模型而定**（不同 embedding 模型的相似度分布差很多），
用 `pyharness knowledge "問題"` 試搜尋，結果會列出每筆的相似度與 rerank
分數，拿相關與不相關的問題各試幾次再決定：

```
$ pyharness knowledge "特休怎麼算"
3 個檔案、12 個片段，檢索方式：關鍵字 + 語意（embedding），再用 reranker 重新排序
門檻：相似度 ≥ 0.3、rerank ≥ 0.2

[1] 來源：hr/leave.md › 請假規定 › 特休（相似度 0.712、rerank 0.954）
到職滿六個月給三天特休……
```

### 向量資料庫

embedding 存在內建的向量資料庫裡，不需要另外安裝或架設服務：

- 專案的向量存在 `.harness/knowledge/vectors.sqlite3`（一個 SQLite 檔案），
  以「模型名稱 + 片段內容」識別。重新啟動、檔案部分改動時，只有新的
  或改過的片段需要重新計算 embedding；刪掉的檔案，它的向量也會一併
  清掉。舊版的 `embeddings.json` 會自動搬進來。
- 搜尋在記憶體中計算，有安裝 numpy 時用它加速（一萬個片段約 0.02 秒），
  沒有的話用純 Python（幾千個片段以內也很快）：

  ```bash
  pip install numpy
  ```

要改用外部的向量資料庫（pgvector、Qdrant 等），實作
`harness.VectorStore` 的四個方法（`missing`、`add`、`retain`、`search`），
在程式裡交給知識庫：

```python
project = harness.load_project(".")
project.knowledge.vector_store = MyPgVectorStore(...)
```

一個向量資料庫只給一個知識庫使用：`retain` 會刪除這個知識庫用不到的
向量。

### 依部門限制可以搜尋的文件

文件可以標上標籤。**有標籤的文件只有被允許該標籤的 agent 搜尋得到，
沒有標籤的文件所有人都搜尋得到。** 標籤有兩種寫法：

```markdown
---
labels: [hr]
---
# 請假規定
...
```

或在 `knowledge/labels.yaml` 用路徑樣式指定（PDF 只能用這種寫法）：

```yaml
"hr/*": [hr]
"finance/*.pdf": [finance]
```

限制某個 agent 只能搜尋特定標籤：

```markdown
---
name: hr_bot
knowledge:
  labels: [hr]        # 只搜尋得到 hr 與沒有標籤的文件
  topK: 5             # 每次最多回傳幾筆，預設 5
---
```

要依**登入的使用者**決定範圍（例如不同部門看到不同文件），在程式裡
設定 `label_filter`，它收到 `RunContext`，從 `deps` 取得使用者資訊：

```python
project = harness.load_project(".")
project.knowledge.label_filter = lambda context: context.deps["departments"]

await harness.run(agent, text, deps={"departments": ["hr"]})
```

agent 設定的 `labels` 與 `label_filter` 同時存在時，兩者都允許的標籤
才搜尋得到。

### 在程式裡使用

```python
kb = harness.Knowledge(
    "knowledge",
    embedding_model="openai/text-embedding-3-small",  # 可省略
    cache_dir="data/kb-cache",  # 向量資料庫的位置，省略時只存在記憶體
    min_similarity=0.3,  # 可省略
    reranker="cohere/rerank-v3.5",  # 可省略
    min_rerank_score=0.2,  # 可省略
)
kb.add_text("退貨請在七天內申請", source="FAQ#3")  # 也可以直接加文字

agent = harness.Agent(name="faq", instructions="...", tools=[kb.as_tool()])

hits = await kb.search("退貨期限")  # 也可以直接搜尋
hits[0].similarity, hits[0].rerank_score  # 沒有使用時是 None
```

用自己的 reranker（例如本機的 cross-encoder）時，傳入
`rerank_fn=async (query, documents) -> list[float]`，分數越大越相關。

## Skills

把一段任務指示（加上專屬的工具與參考檔案）打包成 skill。agent 平常只
看得到每個 skill 的名稱與一句說明，判斷用得到時才呼叫 `load_skill`
讀進完整指示，skill 的專屬工具也在這時才開放。agent 可以掛很多 skill，
又不會讓 system prompt 塞滿用不到的說明。

```
skills/
└── refund/
    ├── SKILL.md
    └── policy.md       參考檔案，agent 用 read_skill_file 讀取
```

`SKILL.md`：

```markdown
---
name: refund                           # 省略時用資料夾名稱
description: 處理退款申請，依退款政策判斷能不能退
tools: [find_order, issue_refund]      # 選用，tools/ 裡的工具或 toolset
---
1. 先用 find_order 查訂單，確認付款日期。
2. 讀 policy.md 判斷是否在可退款期間內。
3. 符合條件才呼叫 issue_refund。
```

- **`description` 必填**，agent 靠它決定什麼時候載入，寫清楚「什麼情況
  要用」。
- agent 設定沒寫 `skills` 欄位時可以使用全部 skill；用 `skills: [refund]`
  指定，`skills: []` 表示不用。
- skill 的專屬工具在 skill 載入前不會出現在 agent 的工具裡（即使 agent
  沒寫 `tools` 欄位）。想讓 agent 隨時都能用，就在 agent 的 `tools`
  明確列出。
- 載入狀態是從對話紀錄推算的，接續對話時已載入的 skill 仍然有效。

在程式裡使用：

```python
refund = harness.Skill(
    name="refund",
    description="處理退款申請",
    instructions="1. 先用 find_order 查訂單……",
    tools=[issue_refund],
    root=pathlib.Path("skills/refund"),  # 有參考檔案時才需要
)
agent = harness.Agent(name="cs", instructions="...", skills=[refund])
```

### 內建 skills

框架附帶幾個常用的 skill，寫法參考 Claude 與 Codex 的 skills。在 agent
的 `skills` 欄位列出名稱就能使用（沒寫 `skills` 欄位時只會拿到專案自己
的 skill，不含內建的）：

| skill | 用途 | 載入後開放的工具 |
|---|---|---|
| `skill-creator` | 建立或改寫 skill，附 SKILL.md 範本 | `files` |
| `code-review` | 審查 git 變更，依嚴重程度回報問題，附檢查清單 | `files`、`shell` |
| `debugging` | 重現 → 縮小範圍 → 驗證假設 → 修正 → 確認 | `files`、`shell`、`plan` |
| `planning` | 把多步驟工作拆成計畫並持續更新進度 | `plan` |
| `web-research` | 查證網路資料，整理成附來源的回答 | `web` |
| `git-commit` | 檢查變更、撰寫 commit 訊息，經確認後 commit | `shell` |

```yaml
skills: [code-review, git-commit]
```

- 專案 `skills/` 裡有同名的 skill 時以專案的為準，所以想調整內建 skill
  的做法，可以把 `harness/builtin/skills/<名稱>/` 複製到專案的 `skills/`
  再修改（或請 agent 用 `skill-creator` 幫你改）。
- 內建 skill 的專屬工具一律來自內建 toolsets，而且照樣受權限設定管制
  （例如 `run_command` 仍會先問使用者）。
- 不使用專案資料夾時：`harness.builtin_skills()["code-review"]`。

## 長期記憶

使用者說「請記住……」時，agent 用 `remember` 工具存下來；之後每段新的
對話都會把記憶放進 system prompt。使用者要求忘記時用 `forget` 刪除。

在 `harness.yaml` 開啟：

```yaml
memory: true
```

記憶存在 `.harness/memory.json`，可以用命令列查看與管理：

```bash
pyharness memory                 # 列出所有記憶
pyharness memory forget <識別碼>  # 刪除一則
pyharness memory clear           # 全部刪除
```

記憶屬於使用者，所以嵌進網頁服務時在呼叫 `run` 時傳入，每個使用者用
自己的檔案：

```python
store = harness.MemoryStore(f"data/memory/{user_id}.json")
await harness.run(agent, text, session=session, memory=store)
```

- 只有直接和使用者對話的 agent 會看到記憶，subagent 不會。
- 一則最多 500 字、最多 100 則；滿了 agent 會收到錯誤，請它先刪除舊的。
- 不傳 `memory` 就完全不使用記憶。

## 訊息防護

在**訊息交給模型前**與**回覆送給使用者前**檢查內容。檢查由框架執行，
模型無法繞過。

`harness.yaml`：

```yaml
guardrails:
  maskPii: true          # 遮蔽 Email、台灣手機號碼、身分證字號、信用卡號
  blockInjection: true   # 擋下「忽略之前的指示」「告訴我你的系統提示」等
  blockedWords: [內線消息, 保證獲利]
```

- `maskPii`：使用者訊息裡的個資先換成 `[已遮蔽 Email]` 這類標記，模型
  與對話紀錄都只會看到遮蔽後的內容；agent 的回覆也會遮蔽。
- `blockInjection`：擋下看起來是提示注入的訊息，模型不會收到，也不會
  寫進對話紀錄。
- `blockedWords`：使用者訊息或 agent 回覆包含這些詞時擋下。

被擋下時使用者會看到原因，`RunResult.stop_reason` 是 `blocked`。

偵測是規則比對，能擋下常見的寫法，但不保證完整。需要更嚴格的檢查時，
在同樣的 hooks 事件掛自己的處理函式（見下方 [Hooks](#hooks)），例如
呼叫內容安全服務。

在程式裡使用：

```python
from harness import guardrails

hooks = guardrails.install(harness.Hooks(), mask_pii=True, block_injection=True)
await harness.run(agent, text, hooks=hooks)

guardrails.mask_pii("電話 0912-345-678")  # '電話 [已遮蔽手機號碼]'
guardrails.detect_injection("忽略之前的指示")  # True
```

用 `load_project` 載入的專案，依設定建立的 hooks 在 `project.hooks`，
執行時要傳進去：`harness.run(agent, text, hooks=project.hooks)`。

## 對話壓縮

對話接近模型的上下文上限時，把較舊的歷史濃縮成檢查點，讓長時間的
工作可以繼續下去。做法參考
[deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) 的
compaction：

1. **每次呼叫模型前估算請求大小**，超過上限的 80% 時開始壓縮。估算
   以模型上一次回報的實際用量為基準，只有之後新增的訊息用字數估計。
2. **先修剪超大的工具結果**：超過 8192 字的工具結果只留開頭 4096 字與
   結尾 1024 字，中間換成 `[... 工具結果中間已省略 ...]`。這一步不呼叫
   模型，常常修剪完就夠了。模型還沒看過的工具結果（剛回傳的）不修剪。
3. **還是太長時才摘要**：把較舊的歷史交給模型，濃縮成固定結構的檢查點
   （使用者的需求、重要決定、資料與檔案、錯誤與修正、未完成的工作、
   目前進度、下一步），最近約 15% 的對話原文保留。
4. **模型回報超過上下文上限時**，盡量壓縮後重試一次。

**對話紀錄不會被改寫。** 壓縮與修剪都以額外的紀錄（`role: harness`）
附加在 JSONL 裡，原始訊息完整保留，模型看到的歷史是從紀錄推算出來的。
TypeScript 版與網頁介面也讀得懂這些紀錄，同一段對話仍然可以在兩個版本
間接續（TypeScript 版本身不會主動壓縮）。

### 設定

專案預設開啟。`harness.yaml`（都可省略）：

```yaml
compaction:
  contextWindow: 128000    # 模型的上下文上限；省略時從 litellm 的模型資料查
  thresholdRatio: 0.8      # 超過上限的這個比例時開始壓縮
  retainRatio: 0.15        # 原文保留的近期對話，佔上限的比例
  summaryModel: openai/gpt-5-mini   # 負責摘要的模型，省略時用 agent 的模型
  summaryMaxTokens: 8000   # 摘要的輸出上限
  pruneChars: 8192         # 工具結果超過這個字數才修剪
  pruneHead: 4096
  pruneTail: 1024
  auto: true               # false：只在手動 /compact 時壓縮
  maxOverflowRetries: 1    # 模型回報超過上限時，壓縮後重試幾次
```

不要壓縮的話寫 `compaction: false`。

- **自架端點、LiteLLM Proxy 的模型名稱 litellm 查不到上限**，這時要寫
  `contextWindow`，否則不會主動壓縮（模型回報超過上限時仍會壓縮後
  重試）。
- 預留給回覆的 `modelParams.max_tokens` 會從門檻扣掉。
- 摘要多一次模型呼叫，它的用量會算進 `RunResult.usage`。
- 摘要失敗（模型出錯、沒有回傳文字、摘要沒有比原文短）時，對話照常
  繼續，聊天畫面會顯示原因。
- 已載入的 skill 被壓縮掉之後，它的專屬工具會再次隱藏，agent 需要時會
  重新載入。

### 手動壓縮

聊天時輸入 `/compact`，會把最近一段之外的對話都交給摘要。也可以用
`pyharness run -c /compact` 壓縮最新的對話。

### 在程式裡使用

```python
settings = harness.Compaction(context_window=128_000)

await harness.run(agent, text, session=session, compaction=settings)

result = await harness.compact(agent, session, settings=settings)  # 手動壓縮
```

`run` 不傳 `compaction` 時不壓縮（`load_project` 載入的專案設定在
`project.compaction`）。串流時會收到 `compaction` 事件
（`{"result": CompactionResult}`），自動壓縮失敗時是 `compaction_failed`
（`{"error": str}`）。

`session.messages` 與 `RunResult.messages` 是完整紀錄（含壓縮紀錄）；
模型實際看到的歷史用 `harness.compaction.model_history(messages)` 取得。

## 權限

`harness.yaml`：

```yaml
permissions:
  mode: ask          # ask / bypass / dontAsk
  allow: ["send_report"]
  deny: ["delete_*"]
  ask: ["send_*"]
```

| 模式 | 行為 |
|---|---|
| `ask`（預設） | 需要同意的工具，執行前先問使用者 |
| `bypass` | 全部直接執行 |
| `dontAsk` | 不詢問；需要同意的工具一律拒絕，適合無人值守的排程 |

「需要同意的工具」是標記 `needs_approval=True` 的工具，以及名稱符合
`ask` 規則的工具。判斷順序是 `deny` 規則、`allow` 規則、最後才看模式。
規則是工具名稱的萬用字元樣式。

## Hooks

在固定的時間點插入自己的邏輯，例如稽核紀錄、攔截危險操作、遮蔽敏感
資料：

```python
hooks = harness.Hooks()


@hooks.on("pre_tool_use")
def block_weekend_mail(call, context):
    if call.name == "send_report" and is_weekend():
        return harness.deny("週末不寄信")


@hooks.on("post_tool_use")
def audit(call, result, context):
    log.info("%s %s -> %s", context.session_id, call.name, result.is_error)


await harness.run(agent, "把報告寄給老闆", hooks=hooks)
```

| 事件 | 參數 | 可以做什麼 |
|---|---|---|
| `run_start` | `user_input, context` | 記錄 |
| `pre_user_message` | `text, context` | 回傳新的字串改寫使用者訊息，或回傳 `deny(原因)` 擋下 |
| `pre_tool_use` | `call, context` | 回傳 `deny(原因)` 攔截、`allow()` 略過詢問，或修改 `call.arguments` |
| `post_tool_use` | `call, result, context` | 修改 `result.output` |
| `pre_reply` | `text, context` | 回傳新的字串改寫回覆，或回傳 `deny(原因)` 換成拒絕說明 |
| `run_end` | `result, context` | 記錄用量、通知 |

`pre_user_message` 與 `pre_reply` 只對直接和使用者對話的 agent 觸發。
有註冊 `pre_reply` 時，回覆會整段檢查完才送出，不再逐字串流。

```python
@hooks.on("pre_reply")
def no_competitors(text, context):
    if "競品名稱" in text:
        return harness.deny("這個問題請洽業務窗口。")
    return text.replace("內部代號", "新產品")
```

## 模型設定

兩種連線方式擇一。

**直接連模型供應商。** 模型名稱用 litellm 的格式 `供應商/模型`，金鑰用
該供應商的環境變數。下表是格式範例，實際可用的模型名稱以各供應商與
[litellm 的文件](https://docs.litellm.ai/docs/providers)為準：

| 模型 | 名稱範例 | 環境變數 |
|---|---|---|
| Claude | `anthropic/claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-5` | `OPENAI_API_KEY` |
| Gemini | `gemini/gemini-2.5-pro` | `GEMINI_API_KEY` |
| OpenRouter（含 Hermes） | `openrouter/nousresearch/hermes-4-405b` | `OPENROUTER_API_KEY` |
| Ollama 本地模型 | `ollama_chat/hermes3` | 不需要 |

**連 OpenAI 相容的端點**（LiteLLM Proxy、自架的 vLLM 等）。設定
`HARNESS_BASE_URL` 與 `HARNESS_API_KEY`，模型名稱照端點的寫法，會原樣
送給端點（`openai/` 之類的開頭也不會被拿掉）。這和 TypeScript 版使用
相同的環境變數，兩個版本可以共用同一組設定。

用 LiteLLM Proxy 時，模型名稱就是 Proxy 設定檔裡的 `model_name`。聊天、
embedding 與 reranker 都一樣：

```yaml
# litellm_config.yaml（Proxy 端）
model_list:
  - model_name: claude
    litellm_params: {model: anthropic/claude-opus-5-5, api_key: os.environ/ANTHROPIC_API_KEY}
  - model_name: embed
    litellm_params: {model: openai/text-embedding-3-small, api_key: os.environ/OPENAI_API_KEY}
  - model_name: rerank
    litellm_params: {model: cohere/rerank-v3.5, api_key: os.environ/COHERE_API_KEY}
```

```bash
export HARNESS_BASE_URL=http://localhost:4000   # 結尾有沒有 /v1 都可以
export HARNESS_API_KEY=sk-...                   # Proxy 的 master key 或 virtual key
export HARNESS_MODEL=claude
```

```yaml
# harness.yaml
knowledge:
  embeddingModel: embed
  reranker: rerank
```

litellm 文件裡連 Proxy 的寫法 `litellm_proxy/claude` 也可以，送出前會
拿掉 `litellm_proxy/`。

決定用哪個模型的順序：命令列的 `-m` → agent 檔案的 `model` →
`harness.yaml` 的 `model` → 環境變數 `HARNESS_MODEL` →
`anthropic/claude-opus-5-5`。

其他要傳給模型的參數寫在 `modelParams`（agent 檔案或 `harness.yaml`
皆可），會原樣交給 litellm：

```yaml
modelParams:
  max_tokens: 8000
  reasoning_effort: high
```

## 命令列

```
pyharness new <資料夾>       建立新專案
pyharness chat               開始聊天
pyharness run "<訊息>"       執行一次後結束（適合排程與腳本）
pyharness run -c /compact    壓縮最新的對話（聊天時直接輸入 /compact）
pyharness memory             查看長期記憶（forget <識別碼> / clear）
pyharness knowledge ["查詢"]  檢查知識庫索引，或試著搜尋
                             （-l hr,sales 模擬受限的 agent）
pyharness web                開啟網頁版（--port、--host，見下一節）

chat 與 run 共用的選項：
  -p, --project <資料夾>     專案位置（預設為目前資料夾）
  -a, --agent <名稱>         使用哪個 agent
  -m, --model <名稱>         覆寫模型
  -c, --continue             接續最新的對話
  -r, --resume <ID>          接續指定的對話
  --bypass                   所有工具都不詢問直接執行
```

聊天時按 Ctrl-C 會中斷目前這一輪，不會結束整個聊天。

## 網頁版

在專案資料夾執行：

```bash
pyharness web               # 開啟 http://127.0.0.1:4321
pyharness web --port 8080   # 換埠號
```

和 TypeScript 版的 `harness web` 是同一份網頁，功能說明見
[根目錄 README 的網頁版](../README.md#網頁版)：串流對話、工具步驟、
權限確認與寫檔差異、同時跑好幾段對話、出錯重試，以及寫回
`harness.yaml` 的設定頁。

Python 版另外會處理：

- **Handoff**：對話轉交後，網頁上顯示的 agent 會換成接手的那個。
- **Subagent 的進度**：subagent 呼叫的工具即時列在交辦步驟底下，標出
  是哪個 subagent，「工作中」也會顯示它正在做什麼。subagent 的對話不
  存檔，所以伺服器重新啟動後只剩交辦內容與結果。
- **對話壓縮**：壓縮的地方會標出「對話已壓縮」。
- **訊息防護**：使用者的訊息被擋下時，顯示被擋下的原因。
- **金鑰**：沒有設定 `HARNESS_BASE_URL`（直接連供應商）時，設定頁會
  列出已設定的 `*_API_KEY` 環境變數名稱（不會顯示內容）。

設定頁修改 `harness.yaml` 時只改 `model`、`maxTurns` 與 `permissions`
那幾行，其他內容和註解都保持不動。檔案用了錨點（`&`、`*`）等特殊
寫法、無法安全修改時，會請你直接編輯檔案。

伺服器可以執行專案的工具，所以預設只接受本機連線，並拒絕其他網站
送來的請求。

## 和 TypeScript 版的差異

- **`tools/` 裡有與 `mcpServers` 伺服器同名的 Python 檔時，不啟動那個
  伺服器**，改為直接載入該檔案（見[連接 MCP 伺服器](#連接-mcp-伺服器)）。
- **不會載入 `tools/*.ts`。**
- **Subagents、handoffs、知識庫、skills、長期記憶、訊息防護與對話壓縮
  只有 Python 版支援。** TypeScript 版會忽略 agent 檔案裡的 `subagents`、
  `handoffs`、`skills`、`knowledge` 欄位，以及 `harness.yaml` 的
  `knowledge`、`memory`、`guardrails`、`compaction` 區塊，所以同一個
  專案仍然可以用 TypeScript 版執行（只是沒有這些功能）。Python 版壓縮
  過的對話，TypeScript 版會讀取壓縮後的歷史接續。
- 對話紀錄的格式相同，同一個專案的對話可以用任一個版本接續。

## 目前還沒有的功能

- Slack / Telegram 等訊息平台的接口
- 網頁版的「工作中」只列出這次啟動後跑過的對話；更早的對話仍然可以
  在列表裡打開、接著問
