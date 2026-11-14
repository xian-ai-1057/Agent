# 權限

會寄信、改資料、花錢的工具，執行前應該先問使用者。

```yaml
# harness.yaml
permissions:
  mode: ask          # ask / bypass / dontAsk
  allow: ["send_report"]
  deny: ["delete_*"]
  ask: ["send_*"]
```

| 模式 | 行為 |
|---|---|
| `ask`（預設） | 需要同意的工具，執行前先問使用者 |
| `bypass` | 全部直接執行（命令列的 `--bypass`） |
| `dontAsk` | 不詢問；需要同意的工具一律拒絕，適合無人值守的排程 |

## 哪些工具需要同意

- 名稱符合 `ask` 規則的工具
- 定義時就標記的工具：
  - Python 版：`@harness.tool(needs_approval=True)`
  - TypeScript 版：`needsApproval: true`
  - MCP 伺服器：`destructiveHint=True`
- 內建的 `write_file`、`edit_file`、`run_command`、`web_fetch`

## 判斷順序

1. `deny` 規則
2. `allow` 規則
3. 模式

規則是工具名稱的萬用字元樣式。Subagent 與 handoff 也是以工具的形式提供（`ask_<名稱>`、`transfer_to_<名稱>`），所以同樣管得到，例如 `ask: ["transfer_to_*"]`。

::: tip 被拒絕時會怎樣
模型會收到「使用者沒有同意這次呼叫」，通常會改用其他方式完成，例如把報告內容直接回覆，而不是寫成檔案。
:::

## 在網頁版

需要同意的工具會停下來，可以「允許這一次」、「這個專案以後都允許」（寫進 `harness.yaml` 的 `allow`）或拒絕，也可以按 1、2、3。管理者可以在[管理平台](/guide/web#管理平台)修改模式與規則。見[網頁版](/guide/web)。

## 在自己的程式裡

::: code-group

```ts [TypeScript]
import { definePermissions, run } from "agent-harness";

const permissions = definePermissions({
  mode: "ask",
  approver: async (call) => askUser(`要執行 ${call.name} 嗎？`),
});
await run(agent, "把報告寄給老闆", { permissions });
```

```python [Python]
async def ask_user(call, tool) -> bool:
    return await confirm(f"要執行 {call.name} 嗎？")

permissions = harness.Permissions(mode="ask", approver=ask_user)
await harness.run(agent, "把報告寄給老闆", permissions=permissions)
```

:::

沒有提供詢問使用者的函式時，需要同意的工具會被拒絕。網頁服務沒辦法在終端機詢問使用者，可以用 `dontAsk`，或提供自己的 approver 等待前端的回應。
