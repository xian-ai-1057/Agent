# 專案格式

每個專案都是同樣的結構，兩個版本共用：

```text
my_bot/
├── harness.yaml      名稱、模型、預設 agent、權限、工具伺服器…
├── AGENTS.md         專案共用的背景知識，接在每個 agent 的指示後面
├── agents/
│   └── main.md       一個檔案一個 agent
├── tools/
│   ├── report.py     Python 工具（Python 版直接 import，或 MCP 伺服器）
│   └── extra.ts      TypeScript 工具（TypeScript 版，可省略）
├── skills/           需要時才載入的能力單元（Python 版，可省略）
├── knowledge/        知識庫文件（Python 版，可省略）
├── workspace/        沙盒掛載的資料夾（Python 版，可省略）
└── .harness/         對話紀錄、記憶、索引快取（自動產生，不用進版控）
```

## `harness.yaml`

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

| 欄位 | 說明 | 詳見 |
|---|---|---|
| `name`、`defaultAgent` | 專案名稱、預設使用的 agent | |
| `model`、`modelParams` | 模型與傳給模型的參數 | [設定模型](/guide/model) |
| `maxTurns` | 一輪對話最多呼叫模型幾次 | |
| `mcpServers` | 要啟動或連線的 MCP 伺服器 | [連接 MCP 伺服器](/guide/mcp) |
| `permissions` | 權限模式與規則 | [權限](/guide/permissions) |
| `knowledge`、`memory`、`guardrails`、`compaction`、`sandbox` | Python 版的進階功能 | [進階功能](/advanced/multi-agent) |

TypeScript 版會忽略只有 Python 版支援的區塊，同一個專案仍然可以用兩個版本執行。

## `agents/*.md`

一個檔案一個 agent。開頭是設定，內文就是給模型的指示：

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

`tools` 可以寫 toolset 名稱或單一工具名稱。toolset 有這幾種：

- `mcpServers` 裡的伺服器名稱
- `tools/` 底下的檔名（Python 版是 `.py`，TypeScript 版是 `.ts`）
- [內建 toolset](/guide/builtin-tools)：`files`、`shell`、`plan`、`web`，以及 Python 版的 `sandbox`

整個欄位不寫的話，agent 可以使用專案裡所有的工具（不含內建工具）。

agent 檔案的其他欄位：

| 欄位 | 說明 |
|---|---|
| `model`、`modelParams` | 這個 agent 專用的模型與參數 |
| `subagents`、`handoffs` | 可以找哪些 agent 幫忙（Python 版，見[多個 agent 協作](/advanced/multi-agent)） |
| `skills` | 可以使用的 skills（Python 版，見 [Skills](/advanced/skills)） |
| `knowledge` | 能不能搜尋知識庫（Python 版，見[知識庫](/advanced/knowledge)） |

## `AGENTS.md`

整個專案共用的背景知識（名詞定義、單位、格式慣例）寫在這裡，會接在每個 agent 的指示後面。
