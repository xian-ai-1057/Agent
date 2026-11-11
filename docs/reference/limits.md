# 目前的限制

## TypeScript 版

- **Python 程式不能直接呼叫 TypeScript 版。** 要把 agent 嵌進 Python 網頁服務，請用 Python 版。
- **`deps` 不會傳到 Python 工具伺服器。** 伺服器是獨立的行程，使用者身分這類資訊目前只能靠環境變數傳遞，所以一個伺服器只適合服務一位使用者。需要依使用者區分資料時，同樣請用 Python 版。
- **以下功能目前只有 Python 版支援**，TypeScript 版會忽略相關設定，同一個專案仍然可以執行（只是沒有這些功能）：
  - Subagents 與 handoffs（agent 之間互相呼叫或轉交）
  - 知識庫（RAG）
  - Skills，包含內建的 skill-creator、code-review 等
  - 跨對話的長期記憶
  - 訊息防護（個資遮蔽、提示注入偵測）
  - 對話過長時的自動壓縮（Python 版壓縮過的對話，TypeScript 版可以接著用）
  - 在 Docker 沙盒裡執行 agent 寫的程式（agent 的 `tools` 列了 `sandbox` 時，TypeScript 版會因為找不到它而無法載入）
  - 網頁版的登入密碼、上傳檔案、在網頁上連接模型、自動建立助手專案、Docker Compose 部署

完整對照見[兩個版本的比較](/reference/versions)。

## 兩個版本都還沒有

- Slack / Telegram 等訊息平台的接口。
- **網頁版沒有分帳號**：所有登入的人共用同一組密碼、看得到同樣的對話與上傳的檔案，也都可以同意工具執行、修改設定。要依使用者區分資料，請把 agent [嵌進自己的網頁服務](/guide/embed#嵌進既有的網頁服務)。
- 網頁上的對話在伺服器重新啟動後可以繼續看與接著問，但「工作中」只列出這次啟動後跑過的對話。
