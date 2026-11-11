# 嵌進自己的程式

不需要專案資料夾也能用，適合把 agent 嵌進既有的服務：TypeScript 版嵌進 Node.js 服務，Python 版嵌進 FastAPI、Streamlit 等 Python 服務。

## 執行一次

::: code-group

```ts [TypeScript]
import { defineAgent, run } from "agent-harness";

const agent = defineAgent({
  name: "report_bot",
  instructions: "你是銷售報告助手。",
  tools: [buildReport],
  model: "claude",
});

const result = await run(agent, "給我十月的報告");
console.log(result.output);
```

```python [Python]
import harness

agent = harness.Agent(
    name="report_bot",
    instructions="你是銷售報告助手。",
    tools=[build_report],
    model="anthropic/claude-opus-5-5",
)

result = harness.run_sync(agent, "給我十月的報告")
print(result.output)
```

:::

延續對話時把同一個 session 傳進去，見[對話紀錄](/guide/sessions#在程式裡使用)。

## 串流輸出

接到聊天介面時使用：

::: code-group

```ts [TypeScript]
import { runStream } from "agent-harness";

for await (const event of runStream(agent, "給我十月的報告")) {
  if (event.type === "text_delta") process.stdout.write(event.text);
  if (event.type === "tool_call") console.log(`\n[呼叫 ${event.call.name}]`);
}
```

```python [Python]
async for event in harness.run_stream(agent, "給我十月的報告"):
    if event.type == "text_delta":
        print(event.data["text"], end="")
    elif event.type == "tool_call":
        print(f"\n[呼叫 {event.data['call'].name}]")
```

:::

事件種類：`text_delta`、`message`、`tool_call`、`tool_result`、`run_end`。Python 版另外有 `handoff`、`subagent`（見[多個 agent 協作](/advanced/multi-agent)），以及對話壓縮的 `compaction`、`compaction_failed`。

## 載入專案資料夾

::: code-group

```ts [TypeScript]
import { loadProject, run } from "agent-harness";

// 會啟動它的工具伺服器，用完要關閉
const project = await loadProject("path/to/my_bot");
try {
  const agent = project.agents[project.defaultAgent];
  await run(agent, "嗨", { permissions: project.permissions });
} finally {
  await project.close();
}
```

```python [Python]
project = harness.load_project("path/to/my_bot")
agent = project.agents["main"]
await harness.run(agent, "嗨", hooks=project.hooks)
project.close()  # 有設定 mcpServers 時結束伺服器
```

:::

## 嵌進既有的網頁服務 <span class="badge-py">Python 版</span> {#嵌進既有的網頁服務}

```python
import harness
from harness import sessions

project = harness.load_project(".")
agent = project.agents["main"]


async def handle_chat(user_id: str, session_id: str | None, text: str):
    if session_id:
        session = sessions.load(project.sessions_dir, session_id)
    else:
        session = sessions.create(project.sessions_dir)
    async for event in harness.run_stream(
        agent,
        text,
        session=session,
        deps={"user_id": user_id, "db": get_db()},
        permissions=harness.Permissions(mode="dontAsk"),
        hooks=project.hooks,  # harness.yaml 的 guardrails
        memory=harness.MemoryStore(f"data/memory/{user_id}.json"),
    ):
        if event.type == "text_delta":
            yield event.data["text"]
```

- 網頁服務沒辦法在終端機詢問使用者，所以這裡用 `dontAsk`：需要同意的工具會被拒絕。要讓網頁使用者可以按「同意」，就提供自己的 `Permissions(approver=...)`，在裡面等待前端的回應（見[權限](/guide/permissions)）。
- `deps` 會傳給工具，工具可以從中取得使用者身分、資料庫連線等（見[撰寫工具](/guide/tools#取得執行環境)）。
- `hooks` 與 `memory` 不需要時可以省略。記憶屬於使用者，所以每個使用者用自己的檔案（見[長期記憶](/advanced/memory)）。
- 專案有設定 `mcpServers` 時，服務關閉前呼叫 `project.close()` 結束伺服器。

::: tip
TypeScript 版的 `deps` 不會傳到 Python 工具伺服器（那是另一個行程）。需要依使用者區分資料時，請用 Python 版。
:::
