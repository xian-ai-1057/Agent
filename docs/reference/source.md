# 閱讀原始碼

這個專案的目的之一是讓人看懂 agent harness 怎麼運作。建議照這個順序讀 TypeScript 版（`src/`）：

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

Python 版（`python/src/harness/`）的結構相同（`loop.py`、`llm.py`、`tools.py`…），另外多了多個 agent 協作（`delegation.py`）、知識庫（`knowledge.py`）、對話壓縮（`compaction.py`）、沙盒（`sandbox.py`）、網頁上設定的模型金鑰（`credentials.py`、`providers.py`）等進階功能，可以在讀懂主迴圈之後再看。

## 設計來源

| 來源 | 採用的設計 |
|---|---|
| OpenAI Agents SDK | `Agent` 與執行迴圈分離、用 schema 定義工具參數、`context.deps` 傳遞相依物件、工具層級的 `needsApproval` |
| Claude Agent SDK / Claude Code | Hooks、權限模式與 allow / deny / ask 規則、只附加不改寫的 JSONL 對話紀錄、用 Markdown 檔定義 agent、用 MCP 接外部工具、內建工具（Read / Edit / Glob / Grep / Bash / WebFetch）、`SKILL.md` 格式的 skills |
| OpenAI Codex | `update_plan` 計畫工具、`shell` 指令工具、內建 skills（skill-creator 等） |
| Hermes Agent | 不綁定模型供應商、toolsets（成組啟用工具）、專案層級的 `AGENTS.md` |
| DeepSeek Harness | 對話壓縮的做法（Python 版） |
