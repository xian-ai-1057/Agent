# 對話壓縮 <span class="badge-py">Python 版</span>

對話接近模型的上下文上限時，把較舊的歷史濃縮成檢查點，讓長時間的
工作可以繼續下去。做法參考
[deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) 的
compaction：

1. **每次呼叫模型前估算請求大小**，超過上限的 80% 時開始壓縮。估算
   以模型上一次回報的實際用量為基準，只有之後新增的訊息用字數估計。
2. **先修剪超大的工具結果**：超過 8192 字的工具結果只留開頭 4096 字與
   結尾 1024 字，中間換成 `[... 工具結果中間已省略 ...]`。這一步不呼叫
   模型，常常修剪完就夠了。模型還沒看過的工具結果（剛回傳的）不修剪。
3. **還是太長時才摘要**：把較舊的歷史交給模型，濃縮成固定結構的檢查點
   （使用者的需求、重要決定、資料與檔案、錯誤與修正、未完成的工作、
   目前進度、下一步），最近約 15% 的對話原文保留。
4. **模型回報超過上下文上限時**，盡量壓縮後重試一次。

**對話紀錄不會被改寫。** 壓縮與修剪都以額外的紀錄（`role: harness`）
附加在 JSONL 裡，原始訊息完整保留，模型看到的歷史是從紀錄推算出來的。
TypeScript 版與網頁介面也讀得懂這些紀錄，同一段對話仍然可以在兩個版本
間接續（TypeScript 版本身不會主動壓縮）。

## 設定

專案預設開啟。`harness.yaml`（都可省略）：

```yaml
compaction:
  contextWindow: 128000    # 模型的上下文上限；省略時從 litellm 的模型資料查
  thresholdRatio: 0.8      # 超過上限的這個比例時開始壓縮
  retainRatio: 0.15        # 原文保留的近期對話，佔上限的比例
  summaryModel: openai/gpt-5-mini   # 負責摘要的模型，省略時用 agent 的模型
  summaryMaxTokens: 8000   # 摘要的輸出上限
  pruneChars: 8192         # 工具結果超過這個字數才修剪
  pruneHead: 4096
  pruneTail: 1024
  auto: true               # false：只在手動 /compact 時壓縮
  maxOverflowRetries: 1    # 模型回報超過上限時，壓縮後重試幾次
```

不要壓縮的話寫 `compaction: false`。

- **自架端點、LiteLLM Proxy 的模型名稱 litellm 查不到上限**，這時要寫
  `contextWindow`，否則不會主動壓縮（模型回報超過上限時仍會壓縮後
  重試）。
- 預留給回覆的 `modelParams.max_tokens` 會從門檻扣掉。
- 摘要多一次模型呼叫，它的用量會算進 `RunResult.usage`。
- 摘要失敗（模型出錯、沒有回傳文字、摘要沒有比原文短）時，對話照常
  繼續，聊天畫面會顯示原因。
- 已載入的 skill 被壓縮掉之後，它的專屬工具會再次隱藏，agent 需要時會
  重新載入。

## 手動壓縮

聊天時輸入 `/compact`，會把最近一段之外的對話都交給摘要。也可以用
`pyharness run -c /compact` 壓縮最新的對話。

## 在程式裡使用

```python
settings = harness.Compaction(context_window=128_000)

await harness.run(agent, text, session=session, compaction=settings)

result = await harness.compact(agent, session, settings=settings)  # 手動壓縮
```

`run` 不傳 `compaction` 時不壓縮（`load_project` 載入的專案設定在
`project.compaction`）。串流時會收到 `compaction` 事件
（`{"result": CompactionResult}`），自動壓縮失敗時是 `compaction_failed`
（`{"error": str}`）。

`session.messages` 與 `RunResult.messages` 是完整紀錄（含壓縮紀錄）；
模型實際看到的歷史用 `harness.compaction.model_history(messages)` 取得。
