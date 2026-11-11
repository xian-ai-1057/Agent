# 網頁版

在專案資料夾執行：

```bash
harness web                 # TypeScript 版，開啟 http://127.0.0.1:4321
pyharness web               # Python 版
pyharness web --port 8080   # 換埠號
```

兩個版本用同一份前端與同一套 API，畫面完全一樣，也和命令列共用同一份[對話紀錄](/guide/sessions)。

## 功能

<figure>
  <img src="/images/web-chat.jpg" alt="網頁版對話畫面：回覆以表格呈現九月與十月的營收比較，上方的「已執行 2 個步驟」展開後列出兩次 get_sales_summary 工具呼叫" loading="lazy" width="2560" height="1600">
  <figcaption>對話：回覆即時串流，工具呼叫收成「已執行 N 個步驟」，點開看參數與結果。</figcaption>
</figure>

- **對話**：回覆即時串流；工具呼叫收成「已執行 N 個步驟」，點開看參數與結果。工具回報的錯誤會標出來，agent 自己修正後繼續。
- **權限確認**：需要同意的工具會停下來，可以「允許這一次」、「這個專案以後都允許」（寫進 `harness.yaml` 的 `allow`）或拒絕，也可以按 1、2、3。寫檔前右側面板會顯示預覽與差異。
- **同時跑好幾段對話**：「工作中」列出所有正在跑、等你確認的對話，可以直接在列表上允許、拒絕或停止。
- **出錯與重試**：連不上模型時顯示原因，修好後按重試，從原本的訊息接著跑，不會重複送出。
- **設定**：修改模型名稱、`maxTurns`、權限模式與規則，儲存時寫回 `harness.yaml` 並保留原本的註解。

<figure>
  <img src="/images/web-approval.jpg" alt="權限確認：agent 想寫入 reports/2026-10.md，畫面提供允許這一次、這個專案以後都允許、拒絕三個選項，右側面板顯示檔案差異" loading="lazy" width="2560" height="1600">
  <figcaption>權限確認：需要同意的工具會停下來；寫檔前右側面板顯示預覽與差異。</figcaption>
</figure>

<figure>
  <img src="/images/web-settings.jpg" alt="設定頁的權限分頁：可選擇先問我、全部直接執行、不問一律拒絕三種模式，並新增允許、先問我、拒絕規則" loading="lazy" width="2560" height="1600">
  <figcaption>設定：在網頁上調整權限模式與規則，下方顯示會寫入 harness.yaml 的內容。</figcaption>
</figure>

設定頁修改 `harness.yaml` 時只改 `model`、`maxTurns` 與 `permissions` 那幾行，其他內容和註解都保持不動。檔案用了錨點（`&`、`*`）等特殊寫法、無法安全修改時，會請你直接編輯檔案。

Python 版另外會顯示：handoff 後換成接手的 agent、subagent 呼叫的工具即時列在交辦步驟底下、標出「對話已壓縮」的位置、訊息被[防護](/advanced/guardrails)擋下的原因。

## 在網頁上連接模型 <span class="badge-py">Python 版</span> {#在網頁上連接模型}

還沒有設定金鑰時，打開網頁會先看到「先連接模型」；之後要更換，到「設定 → 模型」。

1. 選供應商：Anthropic、OpenAI、Google、OpenRouter、Ollama（本機模型，不需要金鑰），或自己的 OpenAI 相容端點（LiteLLM Proxy、vLLM 等，填端點網址）。
2. 貼上金鑰。模型名稱已經填好該供應商常用的模型，需要時再改。
3. 按「測試連線並儲存」。**伺服器會先用新的設定實際連一次模型，連得上才儲存**；金鑰打錯、模型名稱不對或連不到伺服器時，會顯示原因，原本的設定保持不變。

儲存的內容：

- 模型名稱寫進 `harness.yaml` 的 `model`。
- 金鑰與端點網址存在專案的 `.harness/secrets/env.json`，只有執行 pyharness 的使用者能讀（權限 600）。網頁上之後只顯示「已設定」，不會再顯示內容。
- 存起來的設定在每次啟動時套用，**優先於啟動時的環境變數**；同一個專案的 `pyharness chat`、`pyharness run` 也會使用。要回到只用環境變數，刪掉 `.harness/secrets/env.json` 再重新啟動。

::: warning
內建的檔案工具不能存取 `.harness/secrets/`，沙盒也掛載不到它。但 `shell` 的 `run_command` 可以執行任何指令（讀得到這個檔案，也讀得到環境變數），所以架給別人用的 agent 不建議開啟 `shell`。
:::

清單以外的供應商（litellm 支援的其他服務）仍然用原本的方式：在 `harness.yaml` 寫模型名稱、用環境變數提供金鑰（見[設定模型](/guide/model)）。TypeScript 版的端點與金鑰一律來自環境變數，網頁上只顯示狀態。

## 上傳檔案 <span class="badge-py">Python 版</span> {#上傳檔案}

按輸入框左下角的迴紋針、把檔案拖進輸入框，或直接貼上，就能把檔案附在訊息上。

上傳的檔案存在[沙盒](/advanced/sandbox)工作目錄底下的 `uploads/`（沒有設定沙盒時是專案的 `workspace/uploads/`），所以 agent 可以直接用 `run_python` 解析，也可以用 `files` 工具讀取。

- 選好檔案就開始上傳，全部上傳完才能送出；只附檔案、不打字也可以送出。
- 送出時，伺服器會在訊息後面加上每個附件的路徑，讓模型知道檔案在哪裡：

  ```text
  幫我分析這份報表

  [附件] workspace/uploads/十月_業績.xlsx（run_python 裡的路徑：/workspace/uploads/十月_業績.xlsx）
  ```

  網頁上這幾行會顯示成訊息底下的檔案標籤。
- 檔名裡的空白與特殊字元會換成底線，同名的檔案會自動加上 `-2`、`-3`。
- 單一檔案預設最多 50 MB，用 `pyharness web --max-upload-mb 200` 調整。檔案邊收邊寫入磁碟，不會整個放進記憶體。
- 上傳的檔案不會自動刪除，所有對話共用同一個 `uploads/` 資料夾。
- 上傳只接受帶有特定標頭、來自同一個網站的請求，其他網站無法借用使用者的登入狀態上傳檔案。

## 自動建立助手 <span class="badge-py">Python 版</span> {#自動建立助手}

資料夾是空的（或還不存在）時，`pyharness web` 會自動建立一個可以直接使用的通用助手，不必先執行 `pyharness new`：

```bash
pyharness web -p ~/my-assistant
```

這個助手可以讀寫專案裡的檔案、列計畫、讀取網頁、記住使用者交代的事；這台電腦能使用 Docker 時，還會開啟[沙盒](/advanced/sandbox)，讓它寫程式分析上傳的檔案。建立的就是一般的專案（`harness.yaml`、`agents/main.md`、`AGENTS.md`），之後可以照自己的需要修改。資料夾裡已經有其他檔案、卻沒有 `harness.yaml` 時不會自動建立，以免指錯位置。

## 讓其他電腦連線

伺服器可以執行專案的工具，所以預設只接受本機連線，並拒絕其他網站送來的請求。

- **Python 版**：有登入密碼與 `--allowed-host`，見[部署到伺服器](/advanced/server)或[快速部署](/guide/deploy)。
- **TypeScript 版**：沒有登入機制。要讓別台電腦連線時，用 `--host 0.0.0.0` 並自行加上防火牆或反向代理的保護；要架給團隊用，建議用 Python 版。

## 修改網頁本身

先在專案資料夾執行 `harness web`（或 `pyharness web`），再在這個 repo 執行 `npm run dev:web`，Vite 會把 `/api` 轉給它。原始碼在 `web/`，前後端之間的資料格式定義在 `src/web/protocol.ts`，Python 版的 `python/src/harness/web/` 要回傳一樣的格式。

`npm run build` 會把前端打包到 `dist/public`，並複製一份到 `python/src/harness/web/static`。Python 版用 pip 從 git 安裝時沒有 Node.js 可以打包，所以這份打包結果要一起提交。
