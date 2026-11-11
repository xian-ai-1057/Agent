# 多個 agent 協作 <span class="badge-py">Python 版</span>

本節的進階功能目前只有 Python 版支援。TypeScript 版會忽略相關設定，同一個專案仍然可以執行（只是沒有這些功能）。

一個 agent 可以找其他 agent 幫忙。有兩種方式，差別在於對話歸誰：

| | Subagent | Handoff |
|---|---|---|
| 做什麼 | 把一件工作交出去，拿回結果 | 把整段對話交出去 |
| 對方看得到目前的對話嗎 | 看不到，只收到交辦的內容 | 看得到全部 |
| 誰回答使用者 | 原本的 agent | 接手的 agent |
| 使用者的下一則訊息 | 仍由原本的 agent 處理 | 由接手的 agent 處理 |
| 適合 | 查資料、讀長文件這類會佔用大量 context 的工作 | 不同職責的窗口，例如總機轉給帳務 |

## 設定

在 agent 檔案的開頭列出對方的名稱（`agents/` 底下其他 agent 的 `name`）：

```markdown
---
name: billing
description: 處理帳單與退款
subagents: [researcher]     # 可以把工作交給 researcher
handoffs: [main]            # 可以把對話轉交回 main
---
你是帳務專員。需要查訂單時交給 researcher。
```

或在 Python 裡設定：

```python
researcher = harness.Agent(
    name="researcher",
    description="查詢訂單資料",
    instructions="你是研究員。",
    tools=[find_order],
)
billing = harness.Agent(
    name="billing",
    description="處理帳單與退款",
    instructions="你是帳務專員。需要查訂單時交給 researcher。",
    subagents=[researcher],
)
front = harness.Agent(
    name="front",
    instructions="你是總機。帳單與退款的問題轉交給 billing。",
    handoffs=[billing],
)
billing.handoffs.append(front)  # 讓帳務可以把對話交回總機

session = sessions.create("data/sessions")
result = await harness.run(front, "我要退訂單 123", session=session)
print(result.agent_name)  # billing

# 下一則訊息仍然傳入 front，會自動由 billing 接續
result = await harness.run(front, "謝謝", session=session)
```

共同的規則：

- **`description` 很重要。** 模型靠對方的 `description` 決定要不要找它，沒寫的話模型只看得到名稱。
- 兩者都是以工具的形式提供給模型，名稱分別是 `ask_<名稱>` 與 `transfer_to_<名稱>`，所以[權限](/guide/permissions)規則與 [hooks](/guide/hooks) 都管得到。例如轉交前先問使用者：`permissions.ask: ["transfer_to_*"]`。
- 名稱裡不是英數、底線、連字號的字元（例如中文）會換成底線，所以 agent 名稱建議用英文。

## Subagent 的細節

- 使用它自己的指示、工具、模型與 `max_turns`；hooks、權限設定與 `deps` 沿用上一層的。
- 它的 token 用量會算進 `RunResult.usage`。
- 它的對話不會存檔，主 agent 的對話紀錄裡只有交辦內容與它的最後回覆。
- `run_start` / `run_end` 這兩個 hook 不會為 subagent 觸發（它屬於一次工具呼叫）；它內部的工具呼叫照常觸發 `pre_tool_use` / `post_tool_use`，`context.agent_name` 是 subagent 的名稱。
- subagent 可以再有自己的 subagent，最多 3 層。
- 它在輪數上限內沒做完時，主 agent 會收到錯誤說明與它最後的回覆。

### 看到 subagent 的進度

subagent 工作時，它自己的每個事件（呼叫工具、拿到結果、逐字輸出的文字等）都會包成 `subagent` 事件，在它還在做的時候就送出，不用等它做完：

```python
async for event in harness.run_stream(main, "十月賣得如何？"):
    if event.type == "subagent":
        inner = event.data["event"]  # subagent 原本的事件
        if inner.type == "tool_call":
            print(f"  {event.data['agent']} 呼叫 {inner.data['call'].name}")
```

| 欄位 | 內容 |
|---|---|
| `agent` | 產生這個事件的 subagent |
| `path` | 由外到內經過的 agent，例如 `["researcher", "helper"]`（subagent 再交給 subagent 時） |
| `call_id` | 最上層那次 `ask_<名稱>` 工具呼叫的 id，可以用來把進度放在對應的 `tool_call` 底下 |
| `event` | subagent 原本的事件（`text_delta`、`tool_call`、`tool_result`、`message`、`run_end` 等） |

同一輪同時交給好幾個 subagent 時，用 `call_id` 區分各自的進度。命令列與網頁版會把 subagent 呼叫的工具顯示在交辦的步驟底下。

## Handoff 的細節

- **每次都傳入同一個起始 agent 即可。** 目前由誰負責是從對話紀錄推算的，所以延續 session 時會自動由接手的 agent 繼續。
- `RunResult.agent_name` 是結束時負責對話的 agent；串流時會收到 `handoff` 事件（`{"from": ..., "to": ...}`）。
- 要讓對方能把對話交回來，在對方的 `handoffs` 也列出原本的 agent。
- 一輪裡只有第一次轉交會生效。
- 命令列的 `-m` 會套用到專案裡所有的 agent。
