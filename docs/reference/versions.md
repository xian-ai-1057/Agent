# 兩個版本的比較

兩個版本使用同一種專案格式，對話紀錄也互通。TypeScript 版會忽略只有 Python 版支援的設定，同一個專案仍然可以用兩個版本執行。

| | TypeScript 版 | Python 版 |
|---|---|---|
| 指令 | `harness` | `pyharness` |
| Python 寫的工具 | 透過 MCP 伺服器（另一個行程） | 直接 import，加 `@harness.tool` |
| TypeScript 寫的工具 | 直接載入 `tools/*.ts` | 不支援 |
| 可以嵌進 | Node.js 服務 | Python 服務（FastAPI、Streamlit…） |
| 模型 | 任何 OpenAI 相容端點 | litellm（直連供應商或連端點） |
| 連接 MCP 伺服器 | ✓ | ✓ |
| 內建工具（files、shell、plan、web） | ✓ | ✓ |
| 權限與 hooks | ✓ | ✓ |
| 網頁版 | ✓ `harness web` | ✓ `pyharness web` |
| [Subagents 與 handoffs](/advanced/multi-agent) | 尚未支援 | ✓ |
| [知識庫（RAG）](/advanced/knowledge) | 尚未支援 | ✓ |
| [Skills](/advanced/skills) | 尚未支援 | ✓ |
| [長期記憶](/advanced/memory) | 尚未支援 | ✓ |
| [訊息防護](/advanced/guardrails) | 尚未支援 | ✓ |
| [對話壓縮](/advanced/compaction) | 讀得懂壓縮紀錄，本身不壓縮 | ✓ |
| [在沙盒（Docker）裡執行程式](/advanced/sandbox) | 尚未支援 | ✓ |
| [架在伺服器上（登入密碼）](/advanced/server) | 尚未支援 | ✓ |
| [網頁上傳檔案](/guide/web#上傳檔案) | 尚未支援 | ✓ |
| [在網頁上填模型的金鑰](/guide/web#在網頁上連接模型) | 尚未支援（用環境變數） | ✓ |
| [網頁上每段對話選模型、工具與 skills](/guide/web#選擇模型工具與-skills) | 尚未支援 | ✓ |
| [網頁預覽檔案](/guide/web#預覽檔案) | 尚未支援 | ✓ |
| [網頁搜尋、重新命名、刪除對話](/guide/web#管理對話) | 尚未支援 | ✓ |
| [網頁對回覆按讚或倒讚](/guide/web#回覆的評價) | 尚未支援 | ✓ |
| [一行部署（Docker Compose）](/guide/deploy) | 尚未支援 | ✓ |

## 行為上的差異

- **Python 版：`tools/` 裡有與 `mcpServers` 伺服器同名的 Python 檔時，不啟動那個伺服器**，改為直接載入該檔案（見[連接 MCP 伺服器](/guide/mcp)）。
- **Python 版不會載入 `tools/*.ts`。**
- TypeScript 版會忽略 agent 檔案裡的 `subagents`、`handoffs`、`skills`、`knowledge` 欄位，以及 `harness.yaml` 的 `knowledge`、`memory`、`guardrails`、`compaction`、`sandbox` 區塊。但 agent 的 `tools` 列了 `sandbox` 的話，TypeScript 版會因為找不到它而**無法載入**。
- 兩個版本的內建檔案工具都不能存取 `.harness/secrets/`。
- Python 版壓縮過的對話，TypeScript 版會讀取壓縮後的歷史接續。

## 怎麼選

- **工具是 Python**、要嵌進 Python 網頁服務、要架在伺服器上給團隊用，或需要進階功能 → Python 版。
- **工具是 TypeScript**、或要嵌進 Node.js 服務 → TypeScript 版。
- **只想架起來直接用，不寫程式** → [快速部署](/guide/deploy)。
