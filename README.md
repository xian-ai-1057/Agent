# Agent Harness

**完全開源、不綁定模型供應商的 agent harness。**

用一份 `harness.yaml` 和幾個 Markdown 檔定義 agent，用普通的函式或現成的
MCP 伺服器給它工具，再從命令列、網頁或自己的程式裡和它對話。其餘的事
交給框架：呼叫模型、執行工具並把結果送回模型、保存與接續對話、在危險
操作前取得使用者同意、在關鍵時間點插入自訂邏輯。

提供 TypeScript 與 Python 兩個實作，共用同一種專案格式。

**說明文件：<https://agent-harness-docs-ten.vercel.app>**

## 快速部署

想直接架起來用、不寫任何設定：需要一台裝好 Docker 的電腦。

```bash
git clone https://github.com/xian-ai-1057/AgentHarness.git
cd AgentHarness
docker compose up -d
docker compose logs harness      # 看網址與自動產生的登入密碼
```

用瀏覽器打開 `http://<這台電腦的位址>:4321`，登入後依畫面選模型供應商、
貼上金鑰，就可以開始對話、上傳檔案請它分析。不需要先建立專案，也不需要
設定環境變數。公司網路要透過 proxy 才能連外時，把 proxy 寫在 `.env`
（見 `.env.example`）。細節見
[Python 版的說明](python/README.md#快速部署docker)。

## 為誰而做

Agent Harness 和 [Hermes Agent](https://github.com/NousResearch/hermes-agent)、
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 屬於
同一類工具，主要為以下三種使用者而做：

- **需要避開中國大陸應用與服務的人。** 不少機關、企業與受管制產業因為
  法規或資安政策，不能使用中國大陸的 AI 應用。Agent Harness 不依賴任何
  中國大陸的服務，也沒有內建的雲端服務：模型由你自己選擇，可以接
  Claude、GPT、Gemini 等，也可以透過 Ollama 完全在本機執行。
- **希望完全開源、由自己掌控的人。** 框架、網頁介面與範例都在這個 repo
  裡。不需要註冊帳號，框架本身不收集使用資料，對話紀錄是存在專案資料夾
  裡的純文字檔。可以部署在自己的電腦或內網。
- **想學 agent 怎麼運作的人。** 程式碼刻意保持精簡：TypeScript 版的主
  迴圈約 300 行，執行時的相依套件只有四個；兩種語言的實作並排，註解與
  文件都是繁體中文。適合拿來教學、改寫或做實驗，見
  [閱讀原始碼](#閱讀原始碼)。

## 特色

**不綁定模型。** TypeScript 版連接任何 OpenAI 相容端點，Python 版透過
litellm 直接連各家供應商。換模型只要改設定，不用改程式。

**用檔案定義 agent。** `harness.yaml` 加上 `agents/*.md`，不寫程式就能
調整 agent 的行為，也方便放進版控與審查。

**工具來源不限。** Python 函式、TypeScript 函式、現成的 MCP 伺服器，
以及參考 Claude Code 與 Codex 設計的內建工具：讀取、搜尋與修改檔案、
執行指令、追蹤計畫、讀取網頁。

**安全的預設值。** 權限模式與 allow / deny / ask 規則：會寄信、改資料、
花錢的工具，執行前先問使用者。Hooks 可以稽核、攔截或改寫工具呼叫。

**可接續的對話紀錄。** 只附加、不改寫的 JSONL 檔，兩個版本互通。

**多種使用方式。** 命令列聊天、單次執行（適合排程與腳本）、網頁介面，
或當成函式庫嵌進自己的服務。

**進階能力（Python 版）。** 多個 agent 協作、知識庫（RAG）、skills、
長期記憶、訊息防護、對話壓縮。

**部署後就能用（Python 版）。** `docker compose up -d` 啟動後在網頁上
填模型金鑰即可；agent 寫的 Python 程式在隔離的 Docker 沙盒裡執行，可以
上傳 Excel、PDF 請它分析；有登入密碼，能架在伺服器上給團隊使用。

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
| 在沙盒（Docker）裡執行程式 | 尚未支援 | [支援](python/README.md#在沙盒裡執行程式) |
| 架在伺服器上（登入密碼） | 尚未支援 | [支援](python/README.md#部署到伺服器) |
| 網頁上傳檔案 | 尚未支援 | [支援](python/README.md#上傳檔案) |
| 一行部署（Docker Compose） | 尚未支援 | [支援](python/README.md#快速部署docker) |
| 在網頁上填模型的金鑰 | 尚未支援（用環境變數） | [支援](python/README.md#在網頁上連接模型) |

怎麼選：

- **工具是 Python、想用最少的步驟接上**，要把 agent 嵌進 Python
  網頁服務，或需要多個 agent 協作、知識庫、skills、長期記憶、訊息防護、
  對話壓縮、在沙盒裡執行程式、架在伺服器上給大家用：
  用 [Python 版](python/README.md)。
- **工具是 TypeScript、或要嵌進 Node.js 服務**：用 TypeScript 版。

兩個版本都能連接現成的 MCP 伺服器，設定方式相同（`harness.yaml` 的
`mcpServers`）。遠端伺服器的金鑰可以寫成 `${環境變數}` 從環境變數帶入，
也能接上 LiteLLM Proxy 管理的 MCP 工具（例如網頁搜尋，見
[文件](https://agent-harness-docs-ten.vercel.app/guide/mcp#litellm-proxy)）。

`examples/report_bot` 同時提供兩種工具寫法，兩個版本都能直接執行它。

以下是 TypeScript 版的說明。

```
                    ┌────────────────────────┐
使用者 ── 對話 ────▶│        harness         │
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
- **Python 寫的工具**：在 Python 端用 `@mcp.tool()` 標記函式，harness
  會自動啟動並連上它，原本的程式碼不需要改動。

## 安裝

需要 Node.js 22.18 以上。

```bash
git clone https://github.com/xian-ai-1057/AgentHarness.git
cd AgentHarness
npm install          # 安裝相依套件並編譯
npm install -g .     # 讓 harness 指令在任何資料夾都能用
```

之後要更新：在這個資料夾執行 `git pull && npm install`。

只想在某個 Node.js 專案裡當函式庫用的話，在該專案執行：

```bash
npm install github:xian-ai-1057/AgentHarness
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

想先看完整的例子，可以跑 `examples/report_bot`：它把一個 Python 銷售
報告程式包成三個工具，示範怎麼為現有的程式碼加上 agent。

```bash
cd examples/report_bot
pip install -r requirements.txt
harness chat
你> 十月賣得怎麼樣？幫我出一份報告
```

工具用 TypeScript 寫的話，用 `harness new my_bot --lang ts`。

## 專案格式

每個專案都是同樣的結構：

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
`mcpServers` 裡的伺服器名稱、`tools/` 底下 TypeScript 檔的檔名、
[內建 toolset](#內建工具)（`files`、`shell`、`plan`、`web`）。整個欄位
不寫的話，agent 可以使用專案裡所有的工具（不含內建工具）。

## 內建工具

不用寫任何程式就能給 agent 的工具，設計參考 Claude Code 與 Codex。兩個
版本的名稱、參數與輸出都相同。內建工具**要在 agent 的 `tools` 明確列出**
才會啟用；專案裡有同名的 toolset 時以專案的為準。

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
| `sandbox`（僅 Python 版） | `run_python` | 在拋棄式 Docker 容器裡執行 agent 寫的 Python 程式，預設沒有網路；`harness.yaml` 要有 `sandbox` 設定，見[在沙盒裡執行程式](python/README.md#在沙盒裡執行程式) | |

```markdown
---
name: dev
description: 幫忙修改程式、跑測試
tools: [files, shell, plan]
---
修改前先讀檔；改完一定要跑測試確認。
```

幾個設計重點：

- **所有路徑都限制在專案資料夾裡**，`../` 或絕對路徑會被拒絕；搜尋會
  略過 `.git`、`node_modules`、`.harness` 等資料夾，也不跟著符號連結走。
- **`edit_file` 要求原文只出現一次**（或設定 `replace_all`），避免改錯
  地方；找不到或出現多次時，錯誤訊息會告訴模型怎麼修正。
- **`run_command` 沒有標準輸入、有逾時**（預設 120 秒，最多 600 秒），
  輸出過長時保留頭尾。指令在專案資料夾裡執行，但指令本身能做的事不受
  限制，所以預設需要同意。
- **`update_plan` 不存任何狀態**：每次傳入完整的步驟清單，最新一次呼叫
  就是目前的計畫，對話紀錄裡看得到、接續對話時也還在（同 Codex 的做法）。
- **`web_fetch` 會把網址送到外部**，所以預設需要同意。信任的話在
  `harness.yaml` 加上 `permissions.allow: [web_fetch]`。

## 用 Python 寫工具

Python 專案用 [MCP](https://modelcontextprotocol.io/)（工具伺服器的通用
標準）把函式公開成工具。參數格式來自型別註解，說明來自 docstring：

```python
# tools/server.py
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from my_project import reports        # 要公開給 agent 的程式碼

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

不需要專案資料夾也能用，適合把 agent 嵌進 Node.js 服務：

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
- **設定**：查看專案的工具與 agent，每個人看到的都一樣。
- **管理平台**（`/admin`）：和聊天介面分開的版面，只有管理者能用。
  修改模型、`maxTurns`、權限模式與規則，儲存時寫回 `harness.yaml` 並
  保留原本的註解；Python 版要登入時另外有成員管理。TypeScript 版的端點
  網址與金鑰來自環境變數，網頁上只顯示狀態；Python 版可以直接在網頁上
  選供應商、填金鑰，測試連線成功才儲存（見
  [在網頁上連接模型](python/README.md#在網頁上連接模型)）。管理者從帳號
  選單（不必登入時是側欄底部）進入管理平台，聊天介面對管理者與成員
  都一樣（見[說明文件的「管理平台」](https://agent-harness-docs-ten.vercel.app/guide/web#管理平台)）。
- **存取群組**（Python 版要登入時）：管理者在「管理平台 → 群組」決定每位
  成員能用哪些模型、agent、工具組與知識庫標籤。每位成員都屬於內建的
  「所有人」群組，加入其他群組後能用的是這些群組加起來的範圍；管理者不受
  限制。限制在伺服器上執行，成員直接呼叫 API 也繞不過（見
  [說明文件的「存取群組」](https://agent-harness-docs-ten.vercel.app/guide/web#存取群組)）。

Python 版的 `pyharness web` 另外支援：
[上傳檔案](python/README.md#上傳檔案)（迴紋針、拖曳或貼上）、在空資料夾
執行時自動建立可以直接使用的助手，以及多人帳號、工作資料夾、
`--allowed-host` 與 `--trusted-proxy`（見[帳號與成員](python/README.md#帳號與成員)、
[部署到伺服器](python/README.md#部署到伺服器)）。只在自己電腦上用（不必
登入）時和以前一樣，和命令列共用 `.harness/sessions/` 與記憶；從單人版
升級成多人帳號時，舊對話交給第一位管理者、放在只有他看得到的「舊資料
（專案資料夾）」裡，舊的 `workspace/` 不會分享給其他成員，預設的「共用」
資料夾換成新的 `shared/`。

伺服器可以執行專案的工具，所以預設只接受本機連線，並拒絕其他網站
送來的請求。TypeScript 版沒有登入機制，要讓別台電腦連線時，用
`--host 0.0.0.0` 並自行加上防火牆或反向代理的保護；要架給團隊使用，
建議用 Python 版的[快速部署](#快速部署)。

修改網頁時，先在專案資料夾執行 `harness web`（或 `pyharness web`），
再在這個 repo 執行 `npm run dev:web`，Vite 會把 `/api` 轉給它。原始碼
在 `web/`，前後端之間的資料格式定義在 `src/web/protocol.ts`，Python 版
的 `python/src/harness/web/` 要回傳一樣的格式。

`npm run build` 會把前端打包到 `dist/public`，並複製一份到
`python/src/harness/web/static`。Python 版用 pip 從 git 安裝時沒有
Node.js 可以打包，所以這份打包結果要一起提交。

## 為既有專案加上 agent

已經有一套程式碼，想讓 agent 透過工具使用它的功能：見
[docs/guide/migration.md](docs/guide/migration.md)。

## 閱讀原始碼

想了解 agent harness 怎麼運作，建議照這個順序讀 TypeScript 版（`src/`）：

| 順序 | 檔案 | 內容 |
|---|---|---|
| 1 | `types.ts` | 訊息、工具呼叫、執行結果等共用資料型別 |
| 2 | `loop.ts` | 主迴圈：呼叫模型 → 執行工具 → 把結果送回，直到模型不再呼叫工具 |
| 3 | `model.ts` | 呼叫 OpenAI 相容端點；主迴圈只認得一個模型函式介面，測試時可以換成假的模型 |
| 4 | `tool.ts` | 工具的定義方式與參數驗證 |
| 5 | `permissions.ts`、`hooks.ts` | 工具執行前的權限判斷與自訂邏輯 |
| 6 | `session.ts` | 對話紀錄怎麼存成 JSONL 檔、怎麼接續 |
| 7 | `mcp.ts`、`project.ts` | 連接 MCP 伺服器、載入專案資料夾 |
| 8 | `builtin/` | 內建工具：檔案、搜尋、指令、計畫、網頁 |

Python 版（`python/src/harness/`）的結構相同（`loop.py`、`llm.py`、
`tools.py`…），另外多了多個 agent 協作（`delegation.py`）、知識庫
（`knowledge.py`）、對話壓縮（`compaction.py`）、沙盒（`sandbox.py`）、
網頁上設定的模型金鑰（`credentials.py`、`providers.py`）等進階功能，
可以在讀懂主迴圈之後再看。

## 設計來源

| 來源 | 採用的設計 |
|---|---|
| OpenAI Agents SDK | `Agent` 與執行迴圈分離、用 schema 定義工具參數、`context.deps` 傳遞相依物件、工具層級的 `needsApproval` |
| Claude Agent SDK / Claude Code | Hooks、權限模式與 allow / deny / ask 規則、只附加不改寫的 JSONL 對話紀錄、用 Markdown 檔定義 agent、用 MCP 接外部工具、內建工具（Read / Edit / Glob / Grep / Bash / WebFetch）、`SKILL.md` 格式的 skills |
| OpenAI Codex | `update_plan` 計畫工具、`shell` 指令工具、內建 skills（skill-creator 等） |
| Hermes Agent | 不綁定模型供應商、toolsets（成組啟用工具）、專案層級的 `AGENTS.md` |
| DeepSeek Harness | 對話壓縮的做法（Python 版） |

## 目前的限制與還沒有的功能

- **Python 程式不能直接呼叫 TypeScript 版。** 要把 agent 嵌進
  Python 網頁服務，請用 [Python 版](python/README.md)。
- **`deps` 不會傳到 Python 工具伺服器。** 伺服器是獨立的行程，使用者
  身分這類資訊目前只能靠環境變數傳遞，所以一個伺服器只適合服務一位
  使用者。需要依使用者區分資料時，同樣請用 Python 版。
- **以下功能目前只有 Python 版支援**，TypeScript 版會忽略相關設定，
  同一個專案仍然可以執行（只是沒有這些功能）：
  - Subagents 與 handoffs（agent 之間互相呼叫或轉交）
  - 知識庫（RAG）
  - Skills（需要時才載入的說明文件），包含內建的 skill-creator、
    code-review 等 skills
  - 跨對話的長期記憶
  - 訊息防護（個資遮蔽、提示注入偵測）
  - 對話過長時的自動壓縮（Python 版壓縮過的對話，TypeScript 版可以
    接著用）
  - 在 Docker 沙盒裡執行 agent 寫的程式（agent 的 `tools` 列了
    `sandbox` 時，TypeScript 版會因為找不到它而無法載入）
  - 網頁版的多人帳號與工作資料夾、上傳檔案、在網頁上連接模型、自動
    建立助手專案、Docker Compose 部署
- Slack / Telegram 等訊息平台的接口（兩個版本都還沒有）
- 網頁版的多人帳號是本機帳號密碼（Python 版）；還不能接公司的單一
  登入（OIDC、SAML），管理者的「執行指令」也還在伺服器行程裡執行
- 網頁上的對話在伺服器重新啟動後可以繼續看與接著問，但「工作中」只
  列出這次啟動後跑過的對話（兩個版本的網頁版都一樣）
