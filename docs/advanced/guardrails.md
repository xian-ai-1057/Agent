# 訊息防護 <span class="badge-py">Python 版</span>

在**訊息交給模型前**與**回覆送給使用者前**檢查內容。檢查由框架執行，
模型無法繞過。

`harness.yaml`：

```yaml
guardrails:
  maskPii: true          # 遮蔽 Email、台灣手機號碼、身分證字號、信用卡號
  blockInjection: true   # 擋下「忽略之前的指示」「告訴我你的系統提示」等
  blockedWords: [內線消息, 保證獲利]
```

- `maskPii`：使用者訊息裡的個資先換成 `[已遮蔽 Email]` 這類標記，模型
  與對話紀錄都只會看到遮蔽後的內容；agent 的回覆也會遮蔽。
- `blockInjection`：擋下看起來是提示注入的訊息，模型不會收到，也不會
  寫進對話紀錄。
- `blockedWords`：使用者訊息或 agent 回覆包含這些詞時擋下。

被擋下時使用者會看到原因，`RunResult.stop_reason` 是 `blocked`。

偵測是規則比對，能擋下常見的寫法，但不保證完整。需要更嚴格的檢查時，
在同樣的 hooks 事件掛自己的處理函式（見 [Hooks](/guide/hooks)），例如
呼叫內容安全服務。

## 在程式裡使用

```python
from harness import guardrails

hooks = guardrails.install(harness.Hooks(), mask_pii=True, block_injection=True)
await harness.run(agent, text, hooks=hooks)

guardrails.mask_pii("電話 0912-345-678")  # '電話 [已遮蔽手機號碼]'
guardrails.detect_injection("忽略之前的指示")  # True
```

用 `load_project` 載入的專案，依設定建立的 hooks 在 `project.hooks`，
執行時要傳進去：`harness.run(agent, text, hooks=project.hooks)`。
