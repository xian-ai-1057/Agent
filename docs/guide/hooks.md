# Hooks

在固定的時間點插入自己的邏輯，例如稽核紀錄、攔截危險操作、遮蔽敏感資料。

::: code-group

```ts [TypeScript]
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

```python [Python]
hooks = harness.Hooks()


@hooks.on("pre_tool_use")
def block_weekend_mail(call, context):
    if call.name == "send_report" and is_weekend():
        return harness.deny("週末不寄信")


@hooks.on("post_tool_use")
def audit(call, result, context):
    log.info("%s %s -> %s", context.session_id, call.name, result.is_error)


await harness.run(agent, "把報告寄給老闆", hooks=hooks)
```

:::

## 事件

| TypeScript | Python | 參數 | 可以做什麼 |
|---|---|---|---|
| `runStart` | `run_start` | `userInput, context` | 記錄這一輪的輸入 |
| — | `pre_user_message` | `text, context` | 回傳新的字串改寫使用者訊息，或回傳 `deny(原因)` 擋下 |
| `preToolUse` | `pre_tool_use` | `call, context` | 回傳 `deny(原因)` 攔截、`allow()` 略過詢問，或修改 `call.arguments` |
| `postToolUse` | `post_tool_use` | `call, result, context` | 修改工具結果 |
| — | `pre_reply` | `text, context` | 回傳新的字串改寫回覆，或回傳 `deny(原因)` 換成拒絕說明 |
| `runEnd` | `run_end` | `result, context` | 記錄用量、發送通知 |

Python 版的 `pre_user_message` 與 `pre_reply` 只對直接和使用者對話的 agent 觸發。有註冊 `pre_reply` 時，回覆會整段檢查完才送出，不再逐字串流。

```python
@hooks.on("pre_reply")
def no_competitors(text, context):
    if "競品名稱" in text:
        return harness.deny("這個問題請洽業務窗口。")
    return text.replace("內部代號", "新產品")
```

[訊息防護](/advanced/guardrails)就是用這幾個事件實作的；需要更嚴格的檢查時，可以在同樣的事件掛自己的處理函式，例如呼叫內容安全服務。

## Subagent 與 hooks <span class="badge-py">Python 版</span> {#subagent-與-hooks}

- `run_start` / `run_end` 不會為 subagent 觸發（它屬於一次工具呼叫）。
- subagent 內部的工具呼叫照常觸發 `pre_tool_use` / `post_tool_use`，`context.agent_name` 是 subagent 的名稱。
