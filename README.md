# Agent Harness

把既有專案的功能包成工具，交給聊天 agent 使用的輕量框架。

你原本的報告產生器、專案小幫手、copilot，都可以變成同一種形狀：
**一個 agent（一段指示）加上一組工具（你既有的函式）**。框架負責其餘
的事：呼叫模型、執行工具、把結果送回模型、保存對話、權限確認。

## 兩個版本

這個 repo 有兩個實作，使用**同一種專案格式**（同一份 `harness.yaml` 與
`agents/*.md`），對話紀錄也互通。差別在於工具怎麼接、能嵌進什麼程式：

| | TypeScript 版（本文件） | [Python 版](python/README.md) |
|---|---|---|
| 指令 | `harness` | `pyharness` |
| Python 專案的工具 | 透過 MCP 伺服器（另一個行程） | 直接 import，加 `@harness.tool` |
| TypeScript 專案的工具 | 直接載入 `tools/*.ts` | 不支援 |
| 可以嵌進 | Node.js 服務 | Python 服務（FastAPI、Streamlit…） |
| 模型 | 任何 OpenAI 相容端點 | litellm（直接連供應商或連端點） |
| 連接 MCP 伺服器 | 支援 | [支援](python/README.md#連接-mcp-伺服器) |
| 網頁版 | `harness web` | [`pyharness web`](python/README.md#網頁版) |
| Subagents 與 handoffs | 尚未支援 | [支援](python/README.md#多個-agent-協作) |
| 知識庫（RAG） | 尚未支援 | [支援](python/README.md#知識庫rag) |
| Skills | 尚未支援 | [支援](python/README.md#skills) |
| 長期記憶 | 尚未支援 | [支援](python/README.md#長期記憶) |
| 訊息防護（個資遮蔽、提示注入） | 尚未支援 | [支援](python/README.md#訊息防護) |
| 對話壓縮 | 讀得懂 Python 版的壓縮紀錄，本身不壓縮 | [支援](python/README.md#對話壓縮) |

怎麼選：

- **專案是 Python、想用最少的步驟接上**，要把 agent 嵌進既有的 Python
  網頁服務，或需要多個 agent 協作、知識庫、skills、長期記憶、訊息防護、
  對話壓縮：
  用 [Python 版](python/README.md)。
- **專案是 TypeScript / Node.js**：用 TypeScript 版。

兩個版本都能連接現成的 MCP 伺服器，設定方式相同（`harness.yaml` 的
`mcpServers`）。

`examples/report_bot` 同時提供兩種工具寫法，所以兩個版本都能直接執行它。

以下是 TypeScript 版的說明。

```
                    ┌────────────────────────┐
  你 ── 聊天 ──────▶│        harness         │
                    │ 主迴圈 / 權限 / 對話紀錄 │
                    └───────┬────────┬───────┘
                            │        │
            OpenAI 相容端點 │        │ 工具
                            ▼        ▼
            LiteLLM Proxy、      Python 專案（MCP 伺服器）
            OpenRouter、         TypeScript 函式
            Ollama、OpenAI…      內建檔案工具
```

- **模型**：連接任何 OpenAI 相容的端點。用 LiteLLM Proxy 就能接 Claude、
  Gemini 等所有 litellm 支援的模型，換模型只要改設定。
- **Python 專案的工具**：在 Python 端用 `@mcp.tool()` 標記函式，harness
  會自動啟動並連上它。既有的程式碼不用改。

## 安裝

需要 Node.js 22.18 以上。

```bash
git clone https://github.com/xian-ai-1057/Agent.git
cd Agent
npm install          # 安裝相依套件並編譯
npm install -g .     # 讓 harness 指令在任何資料夾都能用
```

之後要更新：在這個資料夾執行 `git pull && npm install`。

只想在某個 Node.js 專案裡當函式庫用的話，在該專案執行：

```bash
npm install github:xian-ai-1057/Agent
```

開發這個框架本身：`npm test` 執行測試，`npm run lint` 檢查格式，
`npm run typecheck` 檢查型別。

## 設定模型

harness 用三個環境變數決定連到哪裡：

| 環境變數 | 用途 |
|---|---|
| `HARNESS_BASE_URL` | 端點網址 |
| `HARNESS_API_KEY` | 端點的金鑰（本機端點可以不設） |
| `HARNESS_MODEL` | 模型名稱，寫法由端點決定 |

### 用 LiteLLM Proxy 接任何模型（建議）

LiteLLM Proxy 是一個獨立的小服務，把各家模型轉成同一種介面。

```bash
pip install 'litellm[proxy]'
```

`litellm_config.yaml`：

```yaml
model_list:
  - model_name: claude            # 你在 harness 裡用的名稱
    litellm_params:
      model: anthropic/claude-opus-5-5
      api_key: os.environ/ANTHROPIC_API_KEY
```

```bash
export ANTHROPIC_API_KEY=...
export LITELLM_MASTER_KEY="sk-$(openssl rand -hex 32)"   # Proxy 要求一定要設
litellm --config litellm_config.yaml --port 4000
```

另一個終端機：

```bash
export HARNESS_BASE_URL=http://localhost:4000
export HARNESS_API_KEY=...        # 上面那個 LITELLM_MASTER_KEY 的值
export HARNESS_MODEL=claude
```

### 其他端點

| 端點 | `HARNESS_BASE_URL` | `HARNESS_MODEL` 範例 |
|---|---|---|
| OpenAI | 不用設（設 `HARNESS_API_KEY` 即可） | `gpt-5` |
| OpenRouter | `https://openrouter.ai/api/v1` | `nousresearch/hermes-4-405b` |
| Ollama | `http://localhost:11434/v1` | `hermes3` |

模型名稱是範例，實際可用的名稱以各端點為準。

決定用哪個模型的順序：命令列的 `-m` → agent 檔案的 `model` →
`harness.yaml` 的 `model` → 環境變數 `HARNESS_MODEL`。

其他要傳給模型的參數寫在 `modelParams`（agent 檔案或 `harness.yaml`
皆可），會原樣放進請求：

```yaml
modelParams:
  max_tokens: 8000
  reasoning_effort: high
```

## 快速開始

```bash
harness new my_bot          # 建立專案範本（預設是 Python 工具）
cd my_bot
pip install "mcp>=2"        # Python 端的工具伺服器需要這個套件
harness chat
```

想先看完整的例子，可以跑 `examples/report_bot`：它把一份「既有的」
Python 銷售報告程式包成三個工具。

```bash
cd examples/report_bot
pip install -r requirements.txt
harness chat
你> 十月賣得怎麼樣？幫我出一份報告
```

工具用 TypeScript 寫的話，用 `harness new my_bot --lang ts`。

## 專案格式

每個專案都長一樣，這也是既有專案要移轉成的形狀：

```
my_bot/
├── harness.yaml      名稱、模型、預設 agent、權限、工具伺服器
├── AGENTS.md         專案共用的背景知識，接在每個 agent 的指示後面
├── agents/
│   └── main.md       一個檔案一個 agent
├── tools/
│   ├── server.py     Python 工具伺服器
│   └── extra.ts      TypeScript 工具（可省略）
└── .harness/         對話紀錄（自動產生，不用進版控）
```

`harness.yaml`：

```yaml
name: my_bot
defaultAgent: main
model: claude                 # 可省略，改用 HARNESS_MODEL

mcpServers:
  report:                     # 伺服器名稱，也是這組工具的 toolset 名稱
    command: python3
    args: [tools/server.py]

permissions:
  mode: ask
```

`agents/main.md` 的開頭是設定，內文就是給模型的指示：

```markdown
---
name: main
description: 回答銷售問題並產生銷售報告
tools:
  - report        # harness.yaml 裡名為 report 的伺服器的所有工具
  - files         # 內建的檔案工具
---
你是銷售報告助手。需要數字時一定要呼叫工具取得，不要自己估算。
```

`tools` 可以寫 toolset 名稱或單一工具名稱。toolset 有三種：
`mcpServers` 裡的伺服器名稱、`tools/` 底下 TypeScript 檔的檔名、內建的
`files`。整個欄位不寫的話，agent 可以使用專案裡所有的工具。

## 用 Python 寫工具

Python 專案用 [MCP](https://modelcontextprotocol.io/)（工具伺服器的通用
標準）把函式公開成工具。參數格式來自型別註解，說明來自 docstring：

```python
# tools/server.py
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from my_project import reports        # 你既有的程式碼

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

幾個重點：

- **docstring 就是給模型的使用說明。** 模型靠它決定什麼時候呼叫、參數
  怎麼填，寫得越具體越好。
- **要讓模型看到的錯誤用 `ToolError`。** 它的訊息會原樣送給模型，模型
  會照著修正。其他例外只會讓模型看到「執行失敗」，看不到內容。
- **回傳值**可以是字串、dict、list，會自動轉成文字給模型。
- 伺服器在專案根目錄啟動，並繼承你目前的環境變數（虛擬環境、資料庫
  連線設定都拿得到）。
- 伺服器啟動失敗時（例如 import 錯誤），harness 會顯示它的錯誤訊息。
  設定 `HARNESS_DEBUG=1` 可以看到伺服器執行期間的所有 log。

這是 `mcp` 套件 2.x 版的寫法。

### 需要使用者同意的工具

會寄信、改資料、花錢的工具，執行前應該先問使用者。兩種做法擇一：

在 `harness.yaml` 用名稱樣式指定：

```yaml
permissions:
  ask: ["send_*", "delete_*"]
```

或在 Python 端標記：

```python
from mcp.types import ToolAnnotations


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def send_report(to: str, month: str) -> str:
    """把報告寄給指定的收件人。"""
    ...
```

## 用 TypeScript 寫工具

`tools/` 底下每個 `.ts` 檔匯出的工具會自動載入，檔名就是 toolset 名稱。
Node.js 會直接執行 TypeScript，不需要先編譯。

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

專案裡有安裝 `agent-harness` 與 `zod` 時，改用 `tool()` 可以自動驗證
參數並推導型別：

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

`execute` 的第二個參數 `context` 帶有 `workdir`、`sessionId`、`deps`
（呼叫 `run` 時傳進來的物件）與 `signal`（使用者中斷時觸發）。工具出錯
就直接 `throw`，錯誤訊息會送回給模型，對話不會中斷。

## 在 TypeScript 程式裡使用

不需要專案資料夾也能用，適合把 agent 嵌進既有的 Node.js 服務：

```ts
import { defineAgent, run } from "agent-harness";

const agent = defineAgent({
  name: "report_bot",
  instructions: "你是銷售報告助手。",
  tools: [buildReport],
  model: "claude",
});

const result = await run(agent, "給我十月的報告");
console.log(result.output);
```

延續對話：把同一個 session 傳進去。

```ts
import { createSession, loadSession, run } from "agent-harness";

const session = createSession("data/sessions"); // 會存成 JSONL 檔
await run(agent, "給我十月的報告", { session });
await run(agent, "跟九月比呢？", { session });

// 之後（例如下一個 HTTP 請求）再接回來
const restored = loadSession("data/sessions", session.id);
```

串流輸出（接到聊天介面時使用）：

```ts
import { runStream } from "agent-harness";

for await (const event of runStream(agent, "給我十月的報告")) {
  if (event.type === "text_delta") process.stdout.write(event.text);
  if (event.type === "tool_call") console.log(`\n[呼叫 ${event.call.name}]`);
}
```

事件種類：`text_delta`、`message`、`tool_call`、`tool_result`、
`run_end`。

載入專案資料夾（會啟動它的工具伺服器，用完要關閉）：

```ts
import { loadProject, run } from "agent-harness";

const project = await loadProject("path/to/my_bot");
try {
  const agent = project.agents[project.defaultAgent];
  await run(agent, "嗨", { permissions: project.permissions });
} finally {
  await project.close();
}
```

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

「需要同意的工具」是名稱符合 `ask` 規則的工具，以及定義時就標記的
工具（TypeScript 的 `needsApproval: true`、Python 的
`destructiveHint=True`、內建的 `write_file`）。

判斷順序是 `deny` 規則、`allow` 規則、最後才看模式。規則是工具名稱的
萬用字元樣式。在自己的程式裡使用時，用
`definePermissions({ approver })` 提供詢問使用者的函式；沒有提供的話，
需要同意的工具會被拒絕。

## Hooks

在固定的時間點插入自己的邏輯，例如稽核紀錄、攔截危險操作、遮蔽敏感
資料：

```ts
import { deny, Hooks, run } from "agent-harness";

const hooks = new Hooks()
  .on("preToolUse", (call) => {
    if (call.name === "send_report" && isWeekend()) {
      return deny("週末不寄信");
    }
  })
  .on("postToolUse", (call, result, context) => {
    console.log(context.sessionId, call.name, result.isError);
  });

await run(agent, "把報告寄給老闆", { hooks });
```

| 事件 | 參數 | 可以做什麼 |
|---|---|---|
| `runStart` | `userInput, context` | 記錄 |
| `preToolUse` | `call, context` | 回傳 `deny(原因)` 攔截、`allow()` 略過詢問，或修改 `call.arguments` |
| `postToolUse` | `call, result, context` | 修改 `result.output` |
| `runEnd` | `result, context` | 記錄用量、通知 |

## 命令列

```
harness new <資料夾> [--lang python|ts]   建立新專案
harness chat                              開始聊天
harness run "<訊息>"                      執行一次後結束（適合排程與腳本）
harness web                               開啟網頁版（見下一節）

chat 與 run 共用的選項：
  -p, --project <資料夾>     專案位置（預設為目前資料夾）
  -a, --agent <名稱>         使用哪個 agent
  -m, --model <名稱>         覆寫模型
  -c, --continue             接續最新的對話
  -r, --resume <ID>          接續指定的對話
      --bypass               所有工具都不詢問直接執行

web 的選項：
  -p, --project <資料夾>     專案位置（預設為目前資料夾）
      --port <埠號>          預設 4321
      --host <位址>          預設 127.0.0.1（只有本機能連）
```

聊天時按 Ctrl-C 會中斷目前這一輪，不會結束整個聊天。

## 網頁版

在專案資料夾執行：

```bash
harness web                 # 開啟 http://127.0.0.1:4321
harness web --port 8080     # 換埠號
```

網頁版和 `harness chat` 用同一個專案、同一份對話紀錄。Python 版的
`pyharness web` 用同一份前端與同一套 API，兩邊的網頁完全一樣。功能有：

- **對話**：回覆即時串流；工具呼叫收成「已執行 N 個步驟」，點開看
  參數與結果。工具回報的錯誤會標出來，agent 自己修正後繼續。
- **權限確認**：需要同意的工具會停下來，可以「允許這一次」、「這個
  專案以後都允許」（寫進 `harness.yaml` 的 `allow`）或拒絕，也可以
  按 1、2、3。寫檔前右側面板會顯示預覽與差異。
- **同時跑好幾段對話**：「工作中」列出所有正在跑、等你確認的對話，
  可以直接在列表上允許、拒絕或停止。
- **出錯與重試**：連不上模型時顯示原因，修好後按重試，從原本的訊息
  接著跑，不會重複送出。
- **設定**：修改模型名稱、`maxTurns`、權限模式與規則，儲存時寫回
  `harness.yaml` 並保留原本的註解。端點網址與金鑰來自環境變數，網頁
  上只顯示狀態。

伺服器可以執行專案的工具，所以預設只接受本機連線，並拒絕其他網站
送來的請求。要讓別台電腦連線時，用 `--host 0.0.0.0` 並自行加上防火牆
或反向代理的保護。

修改網頁時，先在專案資料夾執行 `harness web`（或 `pyharness web`），
再在這個 repo 執行 `npm run dev:web`，Vite 會把 `/api` 轉給它。原始碼
在 `web/`，前後端之間的資料格式定義在 `src/web/protocol.ts`，Python 版
的 `python/src/harness/web/` 要回傳一樣的格式。

`npm run build` 會把前端打包到 `dist/public`，並複製一份到
`python/src/harness/web/static`。Python 版用 pip 從 git 安裝時沒有
Node.js 可以打包，所以這份打包結果要一起提交。

## 移轉既有專案

見 [docs/MIGRATION.md](docs/MIGRATION.md)。

## 設計來源

| 來源 | 採用的設計 |
|---|---|
| OpenAI Agents SDK | `Agent` 與執行迴圈分離、用 schema 定義工具參數、`context.deps` 傳遞相依物件、工具層級的 `needsApproval` |
| Claude Agent SDK / Claude Code | Hooks、權限模式與 allow / deny / ask 規則、只附加不改寫的 JSONL 對話紀錄、用 Markdown 檔定義 agent、用 MCP 接外部工具 |
| Hermes Agent | 不綁定模型供應商、toolsets（成組啟用工具）、專案層級的 `AGENTS.md` |

## 目前的限制與還沒有的功能

- **Python 程式不能直接呼叫 TypeScript 版。** 要把 agent 嵌進既有的
  Python 網頁服務，請用 [Python 版](python/README.md)。
- **`deps` 不會傳到 Python 工具伺服器。** 伺服器是獨立的行程，使用者
  身分這類資訊目前只能靠環境變數傳遞，所以一個伺服器只適合服務一位
  使用者。需要依使用者區分資料時，同樣請用 Python 版。
- **以下功能目前只有 Python 版支援**，TypeScript 版會忽略相關設定，
  同一個專案仍然可以執行（只是沒有這些功能）：
  - Subagents 與 handoffs（agent 之間互相呼叫或轉交）
  - 知識庫（RAG）
  - Skills（需要時才載入的說明文件）
  - 跨對話的長期記憶
  - 訊息防護（個資遮蔽、提示注入偵測）
  - 對話過長時的自動壓縮（Python 版壓縮過的對話，TypeScript 版可以
    接著用）
- Slack / Telegram 等訊息平台的接口（兩個版本都還沒有）
- 網頁上的對話在伺服器重新啟動後可以繼續看與接著問，但「工作中」只
  列出這次啟動後跑過的對話（兩個版本的網頁版都一樣）
