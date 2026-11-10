# Harness（Python 版）

把既有 Python 專案的功能包成工具，交給聊天 agent 使用的輕量框架。

這是 Harness 的 Python 實作，和根目錄的 TypeScript 版使用**同一種專案
格式**（同一份 `harness.yaml` 與 `agents/*.md`）。兩個版本的比較與選擇
方式見[根目錄的 README](../README.md#兩個版本)。

Python 版的特點：

- **工具直接 import 既有函式。** 在函式上加 `@harness.tool` 就完成，
  不需要另外的工具伺服器。
- **可以嵌進既有的 Python 服務。** FastAPI、Streamlit、Flask 裡直接
  `import harness`。
- **模型透過 litellm 連接**，Claude、OpenAI、Gemini、Hermes、Ollama 上
  的本地模型都能用。
- **多個 agent 可以協作。** 把工作交給 subagent，或把整段對話轉交
  （handoff）給另一個 agent。

## 安裝

需要 Python 3.11 以上。

```bash
pip install "git+https://github.com/xian-ai-1057/Agent.git#subdirectory=python"
```

開發這個框架本身：

```bash
git clone https://github.com/xian-ai-1057/Agent.git
cd Agent/python
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

想先看完整的例子，可以跑 `examples/report_bot`：它把一份「既有的」銷售
報告程式包成三個工具。

```bash
cd examples/report_bot
pyharness chat
你> 十月賣得怎麼樣？幫我出一份報告
```

## 專案格式

```
my_bot/
├── harness.yaml      名稱、模型、預設 agent、權限
├── AGENTS.md         專案共用的背景知識，接在每個 agent 的指示後面
├── agents/
│   └── main.md       一個檔案一個 agent
├── tools/
│   └── report.py     用 @harness.tool 包裝的函式
└── .harness/         對話紀錄（自動產生，不用進版控）
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

`tools` 可以寫 toolset 名稱（`tools/` 底下的檔名，或內建的 `files`）
或單一工具名稱。整個欄位不寫的話，agent 可以使用 `tools/` 底下所有的
工具。

專案根目錄會自動加入 Python 的搜尋路徑，所以 `tools/` 裡的檔案可以
直接 import 專案既有的套件。

## 寫工具

在函式上加 `@harness.tool` 就完成了。給模型看的參數格式來自型別註解，
工具與參數的說明來自 docstring。

```python
import harness
from my_project import reports  # 你既有的程式碼


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
`handoff`、`run_end`。

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
    ):
        if event.type == "text_delta":
            yield event.data["text"]
```

網頁服務沒辦法在終端機詢問使用者，所以這裡用 `dontAsk`：需要同意的
工具會被拒絕。要讓網頁使用者可以按「同意」，就提供自己的
`Permissions(approver=...)`，在裡面等待前端的回應。

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

Handoff 的細節：

- **每次都傳入同一個起始 agent 即可。** 目前由誰負責是從對話紀錄推算
  的，所以延續 session 時會自動由接手的 agent 繼續。
- `RunResult.agent_name` 是結束時負責對話的 agent；串流時會收到
  `handoff` 事件（`{"from": ..., "to": ...}`）。
- 要讓對方能把對話交回來，在對方的 `handoffs` 也列出原本的 agent。
- 一輪裡只有第一次轉交會生效。
- 命令列的 `-m` 會套用到專案裡所有的 agent。

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
| `pre_tool_use` | `call, context` | 回傳 `deny(原因)` 攔截、`allow()` 略過詢問，或修改 `call.arguments` |
| `post_tool_use` | `call, result, context` | 修改 `result.output` |
| `run_end` | `result, context` | 記錄用量、通知 |

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
`HARNESS_BASE_URL` 與 `HARNESS_API_KEY`，模型名稱照端點的寫法。這和
TypeScript 版使用相同的環境變數，兩個版本可以共用同一組設定。

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

chat 與 run 共用的選項：
  -p, --project <資料夾>     專案位置（預設為目前資料夾）
  -a, --agent <名稱>         使用哪個 agent
  -m, --model <名稱>         覆寫模型
  -c, --continue             接續最新的對話
  -r, --resume <ID>          接續指定的對話
  --bypass                   所有工具都不詢問直接執行
```

聊天時按 Ctrl-C 會中斷目前這一輪，不會結束整個聊天。

## 和 TypeScript 版的差異

- **不會啟動 `mcpServers`。** `harness.yaml` 裡的 `mcpServers` 只有
  TypeScript 版會使用。Python 版改從 `tools/*.py` 直接載入工具，被
  `mcpServers` 當成伺服器執行的檔案會自動略過。
- **不會載入 `tools/*.ts`。**
- **Subagents 與 handoffs 只有 Python 版支援。** TypeScript 版會忽略
  agent 檔案裡的 `subagents` 與 `handoffs` 欄位。
- 對話紀錄的格式相同，同一個專案的對話可以用任一個版本接續。

## 目前還沒有的功能

- 連接 MCP 工具伺服器
- 串流時顯示 subagent 內部的進度（目前只看得到交辦與結果）
- Skills（需要時才載入的說明文件）
- 跨對話的長期記憶
- 對話過長時的自動壓縮
- Slack / Telegram 等訊息平台的接口
