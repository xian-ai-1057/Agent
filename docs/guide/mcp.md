# 連接 MCP 伺服器

除了自己寫的工具，agent 也可以使用現成的 [MCP](https://modelcontextprotocol.io/) 伺服器提供的工具，例如其他語言寫的服務、第三方提供的工具伺服器。兩個版本的設定方式相同。

Python 版需要先安裝 MCP 套件：`pip install "mcp>=2"`。

## 設定

```yaml
# harness.yaml
mcpServers:
  github:                   # 伺服器名稱，也是這組工具的 toolset 名稱
    command: npx            # 本機程式，透過 stdio 溝通
    args: ["-y", "@modelcontextprotocol/server-github"]
    env: {LOG_LEVEL: info}  # 選用，加在目前的環境變數之上
    timeoutSeconds: 120     # 選用，單次工具呼叫最多等幾秒，預設 300
  search:
    url: https://example.com/mcp           # 遠端伺服器（Streamable HTTP）
    headers:                               # 選用，附帶的 HTTP 標頭
      Authorization: Bearer ${SEARCH_API_KEY}
```

`args`、`env`、`url`、`headers` 裡可以寫 `${環境變數名稱}`，載入時換成環境變數的值，**金鑰就不必寫進 `harness.yaml`**。`${NAME:-預設值}` 在環境變數沒有設定時使用預設值；沒有設定也沒有預設值時，載入會失敗並指出缺少哪個環境變數。

從 Claude Code、Cursor 等工具複製過來的設定可以直接用，裡面的 `type`（`stdio`、`http`）會被接受；舊的 `sse` 傳輸不支援，請改用伺服器的 Streamable HTTP 端點（通常是 `/mcp`）。

agent 用伺服器名稱引用整組工具，也可以只列出其中幾個工具的名稱：

```markdown
---
name: main
tools: [report, github]
---
```

## 透過 LiteLLM Proxy 使用（例：網頁搜尋 webiq） {#litellm-proxy}

[LiteLLM Proxy](https://docs.litellm.ai/docs/mcp) 可以集中管理 MCP 伺服器，用 LiteLLM 的金鑰控管誰能用哪些工具。在 OpenAI Responses API 裡的寫法是這樣：

```json
"tools": [{
  "type": "mcp",
  "server_label": "litellm",
  "server_url": "litellm_proxy/mcp/webiq",
  "require_approval": "never",
  "headers": {"x-litellm-api-key": "Bearer APIKEY"}
}]
```

其中 `litellm_proxy` 是「Proxy 自己」的簡寫，只在呼叫 Proxy 的模型 API 時有效。harness 是自己連 MCP 伺服器，所以要寫出 Proxy 的完整網址。換成 harness 的設定：

```yaml
# harness.yaml
mcpServers:
  webiq:                    # toolset 名稱，agent 的 tools 寫 webiq
    url: ${LITELLM_PROXY_URL}/webiq/mcp
    headers:
      x-litellm-api-key: Bearer ${LITELLM_API_KEY}
```

```bash
# .env（或啟動前的環境變數）
LITELLM_PROXY_URL=https://litellm.example.com   # Proxy 的網址，不含 /v1
LITELLM_API_KEY=sk-...
```

再把 `webiq` 加進 agent 的 `tools`：

```markdown
---
name: main
tools: [files, plan, web, webiq]
---
```

- 網址是 Proxy 替每個 MCP 伺服器開的端點 `/<伺服器名稱>/mcp`。Proxy 版本較舊、這個網址連不上時，改用 `${LITELLM_PROXY_URL}/mcp`，並在 `headers` 加上 `x-mcp-servers: webiq` 指定伺服器。
- 金鑰前面要有 `Bearer `，少了會得到 401。
- `require_approval: never` 不用另外設定：MCP 工具除非伺服器標記為破壞性操作，預設都直接執行。想每次都先問，用 `permissions.ask` 加上工具名稱。
- **連不上 Proxy 時整個專案會載入失敗**（網頁版無法啟動），錯誤訊息會寫出網址。啟動後在網頁版「設定 → 工具」看得到 webiq 的工具，就表示連上了。

### 用 Docker 部署時 {#litellm-docker}

[快速部署](/guide/deploy)的專案在 `harness-data` volume 裡，在 repo 根目錄這樣加上：

```bash
# 1. 在 .env 加上 LITELLM_PROXY_URL 與 LITELLM_API_KEY（見 .env.example）

# 2. 把設定加到 harness.yaml 的最後面（檔案裡原本沒有 mcpServers 時）
docker compose exec -T -u harness harness sh -c 'cat >> /assistant/harness.yaml' <<'YAML'

mcpServers:
  webiq:
    url: ${LITELLM_PROXY_URL}/webiq/mcp
    headers:
      x-litellm-api-key: Bearer ${LITELLM_API_KEY}
YAML

# 3. 讓助手可以使用：在 agents/main.md 的「- web」下面加一行「- webiq」
docker compose exec -u harness harness sed -i 's/^  - web$/&\n  - webiq/' /assistant/agents/main.md

# 4. 重新建立容器，讓 .env 的新設定生效
docker compose up -d --force-recreate harness
```

內網的 Proxy 記得放進 `.env` 的 `NO_PROXY`。這個版本之後自動建立的助手，`harness.yaml` 與 `agents/main.md` 裡已經有註解掉的 webiq 設定，也可以改成取消註解。

## 運作方式

- **載入專案時就會啟動並連上所有伺服器**，連線在整個專案使用期間共用。伺服器無法啟動時，錯誤訊息會附上它輸出到 stderr 的最後一段。
- 伺服器平常的 log 不會印到聊天畫面，設定環境變數 `HARNESS_DEBUG=1` 才會顯示。
- **本機伺服器在專案根目錄執行**，並繼承目前所有的環境變數，所以金鑰這類設定用環境變數提供即可，不必寫進 `harness.yaml`；遠端伺服器的金鑰用 `${NAME}` 從環境變數帶入。專案使用虛擬環境時，先啟用虛擬環境，或把 `command` 改成虛擬環境裡的 Python（例如 `.venv/bin/python`）。
- 伺服器標記為破壞性操作（`destructiveHint`）的工具，執行前會先問使用者；其他工具一樣可以用 `permissions.ask` 規則指定。
- 不同伺服器（或與 `tools/` 裡的工具）有同名的工具時，載入會失敗。

## 同一個專案給兩個版本使用

**`tools/` 裡有與伺服器同名的 Python 檔時，Python 版直接載入那個檔案，不啟動伺服器。**

`examples/report_bot` 就是這樣寫的：`tools/report.py`（`@harness.tool`）與 `tools/report_server.py`（MCP 伺服器）包裝同一份既有程式碼，`mcpServers` 裡的伺服器也叫 `report`。agent 檔案寫 `tools: [report]`，兩個版本各自找到自己的那一份，Python 版執行它不需要安裝 MCP 套件。

## 在程式裡使用 <span class="badge-py">Python 版</span> {#在程式裡使用}

用完專案要關閉伺服器。命令列會自動處理；在程式裡使用時，呼叫 `project.close()` 或用 `with`：

```python
with harness.load_project(".") as project:
    result = harness.run_sync(project.agents["main"], "幫我查一下")
```

不使用專案資料夾時，用 `harness.connect_mcp_server` 自己建立連線：

```python
with harness.connect_mcp_server(
    "github", command="npx", args=["-y", "@modelcontextprotocol/server-github"]
) as github:
    agent = harness.Agent(
        name="bot",
        instructions="你是開發助手。",
        tools=[build_report, *github.tools],
    )
    result = harness.run_sync(agent, "列出我最近的 PR")
```

連線在自己的背景執行緒裡維持，不屬於任何一個事件迴圈，所以可以在 FastAPI、Streamlit 這類服務裡建立一次，給之後每個請求共用。

TypeScript 版用 `loadProject()` 載入專案時同樣會啟動伺服器，用完呼叫 `project.close()`（見[嵌進自己的程式](/guide/embed)）。

## 其他語言的專案

- 任何語言只要有 MCP SDK（Go、Java、C#、Rust 等都有），把 `command` 換成啟動那個程式的指令即可。
- 已經是 HTTP 服務、支援 MCP 的 Streamable HTTP 時，改用 `url`：

```yaml
mcpServers:
  backend:
    url: https://my-service.example.com/mcp
    headers:
      Authorization: Bearer ${BACKEND_TOKEN}
```
