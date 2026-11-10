# Harness

把既有 Python 專案的功能包成工具，交給聊天 agent 使用的輕量框架。

你原本的報告產生器、專案小幫手、copilot，都可以變成同一種形狀：
**一個 agent（一段指示）加上一組工具（你既有的函式）**。框架負責其餘
的事：呼叫模型、執行工具、把結果送回模型、保存對話、權限確認。

模型透過 [litellm](https://docs.litellm.ai/) 連接，所以 Claude、
OpenAI、Gemini、Hermes、Ollama 上的本地模型都能用，換模型只要改一行
設定。

## 安裝

需要 Python 3.11 以上。

```bash
pip install git+https://github.com/xian-ai-1057/Agent.git
```

開發這個框架本身：

```bash
git clone https://github.com/xian-ai-1057/Agent.git
cd Agent
pip install -e ".[dev]"
pytest
```

## 快速開始

```bash
harness new my_bot          # 建立專案範本
cd my_bot

export ANTHROPIC_API_KEY=...                      # 你所用模型的金鑰
export HARNESS_MODEL=anthropic/claude-opus-5-5    # litellm 的模型名稱

harness chat                # 開始聊天
```

想先看一個完整的例子，可以直接跑 `examples/report_bot`：它把一份
「既有的」銷售報告程式包成三個工具。

```bash
cd examples/report_bot
harness chat
你> 十月賣得怎麼樣？幫我出一份報告
```

## 專案格式

每個專案都長一樣，這也是既有專案要移轉成的形狀：

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
    return reports.build(month, detailed)
```

幾個重點：

- **docstring 就是給模型的使用說明。** 模型靠它決定什麼時候呼叫、參數
  怎麼填，寫得越具體越好。
- **回傳值**可以是字串、dict、list 或 pydantic 模型，框架會自動轉成
  文字給模型。
- **出錯就直接 `raise`。** 錯誤訊息會送回給模型，它會自己修正參數或
  換個做法，對話不會中斷。
- 同步與 `async` 函式都支援。多個工具被同時呼叫時會平行執行。
- 加上 `@harness.tool` 之後，函式仍然可以像原本一樣直接呼叫。

### 需要使用者同意的工具

會寄信、寫檔、改資料的工具，加上 `needs_approval=True`，執行前會先問
使用者：

```python
@harness.tool(needs_approval=True)
def send_report(to: str, month: str) -> str:
    """把報告寄給指定的收件人。"""
    ...
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

## 在 Python 裡使用

不需要專案資料夾也能用，適合把 agent 嵌進既有的網頁或服務：

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
`run_end`。

載入專案資料夾：

```python
project = harness.load_project("path/to/my_bot")
agent = project.agents[project.default_agent]
result = await harness.run(agent, "嗨", permissions=project.permissions)
```

## 權限

`harness.yaml`：

```yaml
permissions:
  mode: ask          # ask / bypass / dont_ask
  allow: ["send_report"]
  deny: ["delete_*"]
```

| 模式 | 行為 |
|---|---|
| `ask`（預設） | 標記 `needs_approval` 的工具，執行前先問使用者 |
| `bypass` | 全部直接執行 |
| `dont_ask` | 不詢問；需要同意的工具一律拒絕，適合無人值守的排程 |

判斷順序是 `deny` 規則、`allow` 規則、最後才看模式。規則是工具名稱的
萬用字元樣式。在自己的程式裡使用時，用 `Permissions(approver=...)`
提供詢問使用者的函式；沒有提供的話，需要同意的工具會被拒絕。

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

模型名稱用 litellm 的格式 `供應商/模型`，金鑰用該供應商的環境變數。
下表是格式範例，實際可用的模型名稱以各供應商與
[litellm 的文件](https://docs.litellm.ai/docs/providers)為準：

| 模型 | 名稱範例 | 環境變數 |
|---|---|---|
| Claude | `anthropic/claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-5` | `OPENAI_API_KEY` |
| Gemini | `gemini/gemini-2.5-pro` | `GEMINI_API_KEY` |
| OpenRouter（含 Hermes） | `openrouter/nousresearch/hermes-4-405b` | `OPENROUTER_API_KEY` |
| Ollama 本地模型 | `ollama_chat/hermes3` | 不需要 |

決定用哪個模型的順序：命令列的 `-m` → agent 檔案的 `model` →
`harness.yaml` 的 `model` → 環境變數 `HARNESS_MODEL` →
`anthropic/claude-opus-5-5`。

其他要傳給模型的參數寫在 `model_params`（agent 檔案或 `harness.yaml`
皆可），會原樣交給 litellm：

```yaml
model_params:
  max_tokens: 8000
  reasoning_effort: high
```

## 命令列

```
harness new <資料夾>         建立新專案
harness chat                 開始聊天
harness run "<訊息>"         執行一次後結束（適合排程與腳本）

chat 與 run 共用的選項：
  -p, --project <資料夾>     專案位置（預設為目前資料夾）
  -a, --agent <名稱>         使用哪個 agent
  -m, --model <名稱>         覆寫模型
  -c, --continue             接續最新的對話
  -r, --resume <ID>          接續指定的對話
  --bypass                   所有工具都不詢問直接執行
```

聊天時按 Ctrl-C 會中斷目前這一輪，不會結束整個聊天。

## 移轉既有專案

見 [docs/MIGRATION.md](docs/MIGRATION.md)。

## 設計來源

| 來源 | 採用的設計 |
|---|---|
| OpenAI Agents SDK | `Agent` 與執行迴圈分離、從型別註解產生工具格式、`RunContext` 傳遞相依物件、工具層級的 `needs_approval` |
| Claude Agent SDK / Claude Code | Hooks、權限模式與 allow / deny 規則、只附加不改寫的 JSONL 對話紀錄、用 Markdown 檔定義 agent |
| Hermes Agent | 不綁定模型供應商、toolsets（成組啟用工具）、專案層級的 `AGENTS.md` |

## 目前還沒有的功能

這一版只做核心。以下功能尚未實作：

- Subagents 與 handoffs（agent 之間互相呼叫或轉交）
- Skills（需要時才載入的說明文件）
- 跨對話的長期記憶
- 對話過長時的自動壓縮
- MCP 工具伺服器
- Slack / Telegram 等訊息平台的接口
