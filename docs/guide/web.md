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

- **對話**：回覆即時串流；工具呼叫收成「完成 N 個步驟」，每一步用看得懂的句子顯示（例如「讀取 `data/sales.csv`」、「執行 `python3 analyze.py`」），點開看參數與結果，執行指令的輸出以終端機的樣子呈現。工具回報的錯誤會標出來，agent 自己修正後繼續。
- **計畫清單**：agent 用 `update_plan` 列計畫時，執行期間會在輸入框上方顯示打勾清單（例如「計畫 2 / 4」），完成的項目會劃掉。
- **回覆的呈現**：表格、程式碼區塊（依語言上色，右上角有「複製」）、數學公式（KaTeX，`$…$` 與 `$$…$$`）。回覆底下可以複製整則內容。
- **權限確認**：需要同意的工具會停下來，可以「允許這一次」、「這個專案以後都允許」（寫進 `harness.yaml` 的 `allow`）或拒絕，也可以按 1、2、3。執行指令時直接顯示要執行的指令與資料夾；寫檔（`write_file`、`edit_file`）前右側面板會顯示預覽與差異。Python 版另外可以選「這段對話都允許」，見[減少確認](#減少確認)。
- **同時跑好幾段對話**：「工作中」列出所有正在跑、等你確認的對話，可以直接在列表上允許、拒絕或停止。
- **出錯與重試**：連不上模型時顯示原因，修好後按重試，從原本的訊息接著跑，不會重複送出。Python 版的伺服器在一輪跑到一半（或等你確認時）重新啟動的話，那段對話會顯示「服務重新啟動，這一輪中斷了」，按重試就接著做；中斷的工具呼叫不會被當成你拒絕。
- **設定**：修改模型名稱、`maxTurns`、權限模式與規則，儲存時寫回 `harness.yaml` 並保留原本的註解。
- **外觀**：配色跟著作業系統的淺色或深色模式，也可以在設定頁右上角固定成淺色或深色（記在這個瀏覽器裡）。系統開啟「減少動態效果」時，畫面上的動畫會全部關閉。
- **手機**：螢幕較窄時，側欄收成左上角的選單。

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

::: info 截圖
上面的截圖是改版前的畫面；目前的配色是白底、中性灰與紅色重點色。
:::

以下幾節是只有 Python 版的伺服器才有的功能。用 TypeScript 版的 `harness web` 時，網頁會自動把這些按鈕藏起來。

## 選擇模型、工具與 skills <span class="badge-py">Python 版</span> {#選擇模型工具與-skills}

每段對話可以各自決定用哪個模型、哪些工具和 skills，從下一則訊息開始生效：

- **模型**：輸入框右下角的模型名稱，點開可以搜尋，並標出「看得懂圖片」與「預設」。對話進行到一半也可以換。
- **＋ 選單**：輸入框左下角的 ＋ 可以上傳檔案，或在「Skills」、「工具」裡勾選這段對話要用的項目。選好的 skill 和關掉的工具會以標籤顯示在輸入框裡，按 × 取消。
  - 工具只能在 agent 原本就有的範圍內關閉，不能多加。
  - 勾選的 skill 會在送給模型的系統提示裡註明「先讀取這些 skill」。

清單裡的模型來自：專案的預設模型、各個 agent 檔案寫的模型，以及 `harness.yaml` 的 `models`。要多開放幾個模型給使用者選，在 `harness.yaml` 加上：

```yaml
models:
  - openai/gpt-5
  - name: anthropic/claude-haiku-4-5
    label: Claude Haiku（快）
```

每一項可以只寫 litellm 的模型名稱，或用 `label` 指定顯示的名稱。模型需要的金鑰要另外設定（見[設定模型](/guide/model)）。這段對話的選擇存在 `.harness/sessions/<對話>.meta.json`，伺服器重新啟動後仍然有效；選過的模型之後從清單移除時，改用 agent 原本的模型。

## 減少確認 <span class="badge-py">Python 版</span> {#減少確認}

- **這段對話都允許**：同意一個工具時選它，這段對話之後再用這個工具都不再問。只記在這段對話的 `.harness/sessions/<對話>.meta.json`，伺服器重新啟動後仍然有效，**不會寫進 `harness.yaml`**，其他對話照樣會問。
- **同樣的呼叫不再問**：在同一段對話裡同意過的呼叫（同一個工具、參數完全相同，例如讀同一個網址），之後再出現時直接執行。拒絕過的呼叫下次仍然會問。

## 預覽檔案 <span class="badge-py">Python 版</span> {#預覽檔案}

點訊息裡的附件、回覆底下的檔案卡片，或工具步驟裡的檔名，右側面板就會打開那個檔案。agent 用沙盒的 `run_python` 新增或修改的檔案（圖表、CSV、簡報等）也會出現在檔案卡片裡：伺服器執行前後比對工作目錄，把這些檔案的路徑接在工具結果後面（見[沙盒](/advanced/sandbox#產生的檔案)）。面板有三個分頁：

- **預覽**：圖片直接顯示；PDF 用瀏覽器內建的檢視器；Markdown 排版後顯示；CSV 顯示成表格（前 200 列）；文字與程式碼依語言上色。HTML、SVG 一律顯示原始碼，不會在網頁裡執行。Word、Excel 這類檔案顯示大小和「下載」。
- **變更**：寫檔前後的差異。
- **這段對話的檔案**：這段對話上傳與寫入的所有檔案。

右上角可以下載，或切換成全螢幕（按 Esc 離開）。

能預覽的範圍和內建的檔案工具相同：只有專案資料夾裡的檔案，不能存取 `.harness/`（對話紀錄與金鑰）和其他以點開頭的檔案或資料夾，指到專案外面的捷徑（symlink）也不行。

從網頁執行時，伺服器會在 agent 的指示後面加一段「網頁版說明」（不會改到 `agents/` 裡的檔案）：使用者只有瀏覽器，打不開伺服器上的資料夾、也不能執行指令；產生的檔案會以檔案卡片出現，提到檔案時用相對於專案的路徑；引用數字時只用程式實際印出來的值。

## 管理對話 <span class="badge-py">Python 版</span> {#管理對話}

- **搜尋**：側欄的「搜尋對話」或按 ⌘K（Windows 為 Ctrl+K），可以用關鍵字找對話標題與訊息內容，↑↓ 選擇、Enter 開啟。
- **分組**：側欄依最後更新時間分成「今天」、「昨天」、「過去 7 天」與「更早」。
- **重新命名與刪除**：滑到對話上，按右邊的 ⋮。刪除前會先確認，對話紀錄會直接刪掉、無法復原；正在跑或等你確認的對話不能刪除，先停止它。

## 回覆的評價 <span class="badge-py">Python 版</span> {#回覆的評價}

每則回覆底下有讚與倒讚。倒讚時可以勾選原因（資訊不正確、沒有照要求做、回答不完整、太長），再補充說明。評價記在這段對話裡，同時附加到專案的 `.harness/feedback.jsonl`（時間、對話、使用的模型、評價與原因），方便之後整理哪些回答不好。

## 在網頁上連接模型 <span class="badge-py">Python 版</span> {#在網頁上連接模型}

還沒有設定金鑰時，打開網頁會先看到「先連接模型」；之後要更換，到「設定 → 模型」。

1. 選供應商：Anthropic、OpenAI、Google、OpenRouter、Ollama（本機模型，不需要金鑰），或自己的 OpenAI 相容端點（LiteLLM Proxy、vLLM 等，填端點網址）。
2. 貼上金鑰。模型名稱已經填好該供應商常用的模型，需要時再改。
3. 按「測試連線並儲存」。**伺服器會先用新的設定實際連一次模型，連得上才儲存**；金鑰打錯、模型名稱不對或連不到伺服器時，會顯示原因（依錯誤的種類判斷，後面附上原始的錯誤訊息），原本的設定保持不變。

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
