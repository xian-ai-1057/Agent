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
    headers: {X-Team: sales}               # 選用，附帶的 HTTP 標頭
```

從 Claude Code、Cursor 等工具複製過來的設定可以直接用，裡面的 `type`（`stdio`、`http`）會被接受；舊的 `sse` 傳輸不支援，請改用伺服器的 Streamable HTTP 端點（通常是 `/mcp`）。

agent 用伺服器名稱引用整組工具，也可以只列出其中幾個工具的名稱：

```markdown
---
name: main
tools: [report, github]
---
```

## 運作方式

- **載入專案時就會啟動並連上所有伺服器**，連線在整個專案使用期間共用。伺服器無法啟動時，錯誤訊息會附上它輸出到 stderr 的最後一段。
- 伺服器平常的 log 不會印到聊天畫面，設定環境變數 `HARNESS_DEBUG=1` 才會顯示。
- **本機伺服器在專案根目錄執行**，並繼承目前所有的環境變數，所以金鑰這類設定用環境變數提供即可，不必寫進 `harness.yaml`。專案使用虛擬環境時，先啟用虛擬環境，或把 `command` 改成虛擬環境裡的 Python（例如 `.venv/bin/python`）。
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
      Authorization: Bearer ...
```
